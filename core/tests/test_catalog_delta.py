"""The pricing delta schema and its pure, atomic application to a catalog
document."""

import copy
from datetime import datetime, timezone

import pytest

from metergraph_core import CatalogError, load_catalog_document
from metergraph_core.catalog_delta import (
    PRICING_DELTA_SCHEMA_VERSION,
    CatalogDeltaConflict,
    CatalogDeltaError,
    PriceRowKey,
    apply_catalog_delta,
    parse_catalog_delta,
)

CURRENT_ROW = {
    "channel": "openai-api",
    "region": "global",
    "effective_from": "2026-01-01",
    "input_per_mtok": 1.0,
    "output_per_mtok": 4.0,
    "source_url": "https://example.com/pricing",
}


def _key(**overrides):
    key = {
        "canonical_id": "example/model-a",
        "channel": "openai-api",
        "region": "global",
        "effective_from": "2026-01-01",
    }
    key.update(overrides)
    return key


def _new_price(**overrides):
    price = {
        "channel": "openai-api",
        "region": "global",
        "effective_from": "2026-10-20",
        "input_per_mtok": "1.25",
        "output_per_mtok": "5",
        "source_url": "https://example.com/pricing",
    }
    price.update(overrides)
    return price


def _replace(**overrides):
    operation = {
        "op": "replace_price",
        "key": _key(),
        "expect": dict(CURRENT_ROW),
        "price": _new_price(),
    }
    operation.update(overrides)
    return operation


def _add(**overrides):
    operation = {
        "op": "add_price",
        "key": _key(channel="vercel-ai-gateway", effective_from="2026-10-20"),
        "expect": None,
        "price": _new_price(channel="vercel-ai-gateway"),
    }
    operation.update(overrides)
    return operation


def _close(**overrides):
    operation = {
        "op": "close_price",
        "key": _key(),
        "expect": dict(CURRENT_ROW),
        "effective_to": "2026-10-20",
    }
    operation.update(overrides)
    return operation


def _delta(*operations, **overrides):
    delta = {
        "schema_version": PRICING_DELTA_SCHEMA_VERSION,
        "kind": "pricing_delta",
        "provenance": {
            "observations": ["observation:openai-api-pricing:2026-10-20"],
            "evidence": ["sha256:" + "0" * 64],
        },
        "operations": list(operations),
    }
    delta.update(overrides)
    return delta


# --- Parsing -----------------------------------------------------------------


def test_parse_reads_each_operation_with_its_row_key():
    delta = parse_catalog_delta(_delta(_replace(), _add(), _close(key=_key(region="us"), expect=dict(CURRENT_ROW, region="us"))))
    assert [op.op for op in delta.operations] == ["replace_price", "add_price", "close_price"]
    assert delta.operations[0].key == PriceRowKey(
        "example/model-a", "openai-api", "global", "2026-01-01"
    )
    assert delta.operations[1].expect is None
    assert delta.operations[2].effective_to == "2026-10-20"
    assert delta.provenance["observations"] == [
        "observation:openai-api-pricing:2026-10-20"
    ]


def test_delta_errors_are_catalog_errors():
    assert issubclass(CatalogDeltaError, CatalogError)


@pytest.mark.parametrize(
    ("delta", "message"),
    [
        (["not", "a", "mapping"], "delta document must be a mapping"),
        (_delta(schema_version=2), "unsupported delta schema_version 2"),
        (_delta(kind="catalog"), "delta kind must be pricing_delta"),
        (_delta(operations="none"), "delta operations must be a list"),
        (_delta(provenance=None), "delta needs provenance"),
        (_delta(provenance={"evidence": []}), "provenance needs an observations list"),
        (_delta(_replace(op="rewrite_price")), "operation 0: unknown op 'rewrite_price'"),
        (_delta("replace"), "operation 0: must be a mapping"),
    ],
)
def test_parse_rejects_malformed_delta_documents(delta, message):
    with pytest.raises(CatalogDeltaError, match=message):
        parse_catalog_delta(delta)


@pytest.mark.parametrize("field", ["canonical_id", "channel", "region", "effective_from"])
def test_parse_requires_the_full_row_key(field):
    key = _key()
    del key[field]
    with pytest.raises(CatalogDeltaError, match=f"operation 0: key needs {field}"):
        parse_catalog_delta(_delta(_replace(key=key)))


def test_parse_rejects_a_non_iso_key_date():
    with pytest.raises(CatalogDeltaError, match="operation 0: invalid key effective_from"):
        parse_catalog_delta(_delta(_replace(key=_key(effective_from="Jan 1"))))


def test_add_price_requires_a_null_expect():
    with pytest.raises(CatalogDeltaError, match="operation 0: add_price expect must be null"):
        parse_catalog_delta(_delta(_add(expect=dict(CURRENT_ROW))))
    operation = _add()
    del operation["expect"]
    with pytest.raises(CatalogDeltaError, match="operation 0: add_price expect must be null"):
        parse_catalog_delta(_delta(operation))


@pytest.mark.parametrize("make", [_replace, _close])
def test_replace_and_close_require_the_expected_row(make):
    with pytest.raises(CatalogDeltaError, match="expect must be the full current row"):
        parse_catalog_delta(_delta(make(expect=None)))


def test_add_price_identity_must_match_its_key():
    with pytest.raises(CatalogDeltaError, match="operation 0: price effective_from must match key"):
        parse_catalog_delta(_delta(_add(price=_new_price(channel="vercel-ai-gateway", effective_from="2026-10-21"))))
    with pytest.raises(CatalogDeltaError, match="operation 0: price channel must match key"):
        parse_catalog_delta(_delta(_add(price=_new_price())))


@pytest.mark.parametrize("field", ["channel", "region"])
def test_replace_price_keeps_channel_and_region(field):
    with pytest.raises(CatalogDeltaError, match=f"operation 0: price {field} must match key"):
        parse_catalog_delta(_delta(_replace(price=_new_price(**{field: "other"}))))


@pytest.mark.parametrize("make", [_replace, _add])
def test_new_price_requires_a_source_url(make):
    operation = make()
    operation["price"] = dict(operation["price"], source_url="  ")
    with pytest.raises(CatalogDeltaError, match="operation 0: price needs source_url"):
        parse_catalog_delta(_delta(operation))


@pytest.mark.parametrize("value", [1.25, 1, None, "1.2.5", "-1", "NaN"])
def test_new_rates_must_be_non_negative_decimal_strings(value):
    with pytest.raises(
        CatalogDeltaError,
        match="operation 0: price input_per_mtok must be a non-negative decimal string",
    ):
        parse_catalog_delta(_delta(_replace(price=_new_price(input_per_mtok=value))))


def test_new_price_rejects_unknown_fields_and_a_closing_date():
    with pytest.raises(CatalogDeltaError, match="operation 0: price has unknown field 'per_call'"):
        parse_catalog_delta(_delta(_replace(price=_new_price(per_call="1"))))
    with pytest.raises(CatalogDeltaError, match="operation 0: a new price row is open"):
        parse_catalog_delta(_delta(_replace(price=_new_price(effective_to="2027-01-01"))))


def test_new_price_rules_must_be_a_mapping():
    with pytest.raises(CatalogDeltaError, match="operation 0: price rules must be a mapping"):
        parse_catalog_delta(_delta(_replace(price=_new_price(rules=["x"]))))


def test_parse_rejects_a_non_iso_price_date():
    with pytest.raises(CatalogDeltaError, match="operation 0: invalid price effective_from"):
        parse_catalog_delta(_delta(_replace(price=_new_price(effective_from="tomorrow"))))


def test_close_price_needs_a_later_iso_effective_to():
    with pytest.raises(CatalogDeltaError, match="operation 0: invalid effective_to"):
        parse_catalog_delta(_delta(_close(effective_to=None)))
    with pytest.raises(CatalogDeltaError, match="operation 0: effective_to must follow key effective_from"):
        parse_catalog_delta(_delta(_close(effective_to="2026-01-01")))


def test_close_price_carries_no_new_row():
    with pytest.raises(CatalogDeltaError, match="operation 0: close_price takes no price"):
        parse_catalog_delta(_delta(_close(price=_new_price())))


def test_parse_rejects_two_operations_on_one_row():
    with pytest.raises(CatalogDeltaError, match="operation 1: duplicate target"):
        parse_catalog_delta(_delta(_replace(), _close()))


def test_parse_does_not_mutate_the_delta():
    delta = _delta(_replace(), _add())
    before = copy.deepcopy(delta)
    parse_catalog_delta(delta)
    assert delta == before


# --- Application -------------------------------------------------------------


def _document():
    return {
        "version": "2026-10-19",
        "currency": "USD",
        "pricing_verified_at": "2026-10-19",
        "models": [
            {
                "canonical_id": "example/model-a",
                "publisher": "example",
                "aliases": [
                    {"provider": "openai", "alias": "model-a", "channel": "openai-api"},
                    {"provider": "vercel", "alias": "example/model-a", "channel": "vercel-ai-gateway"},
                ],
                "prices": [
                    {
                        "channel": "openai-api",
                        "region": "global",
                        "effective_from": "2025-06-01",
                        "effective_to": "2026-01-01",
                        "input_per_mtok": 2.0,
                        "output_per_mtok": 8.0,
                        "source_url": "https://example.com/pricing-old",
                    },
                    dict(CURRENT_ROW),
                    dict(CURRENT_ROW, region="us", input_per_mtok=1.1, output_per_mtok=4.4),
                ],
            }
        ],
    }


def _rows(document, **match):
    [model] = document["models"]
    return [
        row
        for row in model["prices"]
        if all(row.get(field) == value for field, value in match.items())
    ]


def test_replace_price_closes_the_current_row_and_appends_the_new_row():
    result = apply_catalog_delta(_document(), _delta(_replace()))
    rows = _rows(result, channel="openai-api", region="global")
    assert rows == [
        _document()["models"][0]["prices"][0],
        dict(CURRENT_ROW, effective_to="2026-10-20"),
        _new_price(),
    ]


def test_replace_price_keeps_unrelated_rows_unchanged():
    result = apply_catalog_delta(_document(), _delta(_replace()))
    assert _rows(result, region="us") == _rows(_document(), region="us")
    assert result["models"][0]["aliases"] == _document()["models"][0]["aliases"]


def test_add_price_appends_a_row_for_a_new_channel():
    result = apply_catalog_delta(_document(), _delta(_add()))
    assert _rows(result, channel="vercel-ai-gateway") == [
        _new_price(channel="vercel-ai-gateway")
    ]
    assert len(result["models"][0]["prices"]) == 4


def test_close_price_sets_only_effective_to():
    result = apply_catalog_delta(_document(), _delta(_close()))
    assert _rows(result, channel="openai-api", region="global")[1] == dict(
        CURRENT_ROW, effective_to="2026-10-20"
    )
    assert len(result["models"][0]["prices"]) == 3


def test_apply_preserves_version_and_pricing_verified_at():
    result = apply_catalog_delta(_document(), _delta(_replace()))
    assert result["version"] == "2026-10-19"
    assert result["pricing_verified_at"] == "2026-10-19"


def test_apply_never_mutates_the_document_or_the_delta():
    document = _document()
    delta = _delta(_replace(), _add())
    document_before = copy.deepcopy(document)
    delta_before = copy.deepcopy(delta)
    result = apply_catalog_delta(document, delta)
    assert document == document_before
    assert delta == delta_before
    result["models"][0]["prices"][-1]["input_per_mtok"] = "9"
    assert delta == delta_before


def test_applied_result_prices_the_new_rate_from_its_effective_date():
    loaded = load_catalog_document(apply_catalog_delta(_document(), _delta(_replace())))

    def input_rate(day):
        found = loaded.lookup_price(
            model="model-a",
            provider="openai",
            region="global",
            at=datetime(2026, 10, day, tzinfo=timezone.utc),
        )
        return str(found.price.input_per_mtok)

    assert input_rate(19) == "1.0"
    assert input_rate(20) == "1.25"


def _conflicts(document, delta):
    with pytest.raises(CatalogDeltaConflict) as raised:
        apply_catalog_delta(document, delta)
    return [(c.index, c.reason) for c in raised.value.conflicts]


def _new_model_key():
    return _key(
        canonical_id="example/new-model",
        channel="vercel-ai-gateway",
        effective_from="2026-10-20",
    )


def test_an_unknown_model_is_a_conflict():
    assert _conflicts(_document(), _delta(_add(key=_new_model_key()))) == [
        (0, "unknown_model")
    ]


def test_a_missing_row_is_a_conflict():
    key = _key(effective_from="2026-02-01")
    expect = dict(CURRENT_ROW, effective_from="2026-02-01")
    assert _conflicts(_document(), _delta(_replace(key=key, expect=expect))) == [
        (0, "row_missing")
    ]


def test_adding_an_existing_row_is_a_conflict():
    operation = _add(key=_key(), price=_new_price(effective_from="2026-01-01"))
    assert _conflicts(_document(), _delta(operation)) == [(0, "row_exists")]


def test_an_expect_that_differs_from_the_row_is_a_conflict():
    stale = dict(CURRENT_ROW, input_per_mtok=1.05)
    assert _conflicts(_document(), _delta(_replace(expect=stale))) == [
        (0, "expect_mismatch")
    ]


def test_an_expect_missing_a_field_is_a_conflict():
    partial = dict(CURRENT_ROW)
    del partial["source_url"]
    assert _conflicts(_document(), _delta(_close(expect=partial))) == [
        (0, "expect_mismatch")
    ]


def test_replacing_a_closed_row_is_a_conflict():
    closed = _document()["models"][0]["prices"][0]
    key = _key(effective_from="2025-06-01")
    assert _conflicts(_document(), _delta(_replace(key=key, expect=closed))) == [
        (0, "row_not_open")
    ]


@pytest.mark.parametrize("effective_from", ["2026-01-01", "2025-12-31"])
def test_a_replacement_not_after_the_current_row_is_a_conflict(effective_from):
    operation = _replace(price=_new_price(effective_from=effective_from))
    assert _conflicts(_document(), _delta(operation)) == [(0, "not_after_current")]


def test_a_duplicated_model_entry_is_a_conflict():
    document = _document()
    document["models"].append(copy.deepcopy(document["models"][0]))
    assert _conflicts(document, _delta(_replace())) == [(0, "duplicate_model")]


def test_a_duplicated_price_row_is_a_conflict():
    document = _document()
    document["models"][0]["prices"].append(dict(CURRENT_ROW))
    assert _conflicts(document, _delta(_replace())) == [(0, "duplicate_row")]


def test_every_conflict_is_collected_and_nothing_is_applied():
    document = _document()
    before = copy.deepcopy(document)
    delta = _delta(
        _replace(expect=dict(CURRENT_ROW, input_per_mtok=3)),
        _add(),
        _add(key=_new_model_key()),
        _close(key=_key(region="us"), expect=dict(CURRENT_ROW, region="us")),
    )
    assert _conflicts(document, delta) == [
        (0, "expect_mismatch"),
        (2, "unknown_model"),
        (3, "expect_mismatch"),
    ]
    assert document == before


def test_the_conflict_message_names_each_failing_change():
    with pytest.raises(
        CatalogDeltaConflict,
        match=(
            r"operation 0 replace_price example/model-a "
            r"openai-api/global@2026-01-01: expect_mismatch"
        ),
    ):
        apply_catalog_delta(_document(), _delta(_replace(expect={})))


def test_a_result_that_is_not_a_valid_catalog_is_rejected():
    # The new row overlaps the open global row, which the loader rejects.
    operation = _add(
        key=_key(effective_from="2026-06-01"),
        price=_new_price(effective_from="2026-06-01"),
    )
    with pytest.raises(
        CatalogDeltaError,
        match="delta result is not a valid catalog: .*overlapping openai-api/global",
    ):
        apply_catalog_delta(_document(), _delta(operation))


def test_applying_the_same_delta_twice_conflicts():
    delta = _delta(_replace())
    once = apply_catalog_delta(_document(), delta)
    assert _conflicts(once, delta) == [(0, "row_not_open")]


def test_a_malformed_delta_is_rejected_before_application():
    with pytest.raises(CatalogDeltaError, match="unsupported delta schema_version"):
        apply_catalog_delta(_document(), _delta(_replace(), schema_version=3))


def test_an_empty_delta_returns_an_equal_copy():
    document = _document()
    result = apply_catalog_delta(document, _delta())
    assert result == document
    assert result is not document


# --- Mixed date and offset inputs --------------------------------------------


def test_a_close_date_with_an_offset_is_compared_as_an_instant():
    # 2026-01-01T00:30:00+02:00 is 2025-12-31T22:30Z, before the row starts.
    with pytest.raises(CatalogDeltaError, match="effective_to must follow key effective_from"):
        parse_catalog_delta(_delta(_close(effective_to="2026-01-01T00:30:00+02:00")))


def test_a_naive_key_and_an_aware_close_date_parse():
    [operation] = parse_catalog_delta(_delta(_close(effective_to="2026-10-20T12:00:00+00:00"))).operations
    assert operation.effective_to == "2026-10-20T12:00:00+00:00"


def test_dates_naming_the_same_instant_are_one_key():
    with pytest.raises(CatalogDeltaError, match="operation 1: duplicate target"):
        parse_catalog_delta(
            _delta(
                _replace(),
                _close(key=_key(effective_from="2026-01-01T00:00:00+00:00")),
            )
        )


def test_a_utc_midnight_key_matches_a_date_only_row():
    operation = _close(key=_key(effective_from="2026-01-01T00:00:00Z"))
    result = apply_catalog_delta(_document(), _delta(operation))
    assert _rows(result, channel="openai-api", region="global")[1]["effective_to"] == "2026-10-20"


@pytest.mark.parametrize("effective_from", ["2026-01-01T00:00:00+01:00", "2025-12-31T23:59:59-00:00"])
def test_a_replacement_before_the_row_with_an_offset_is_a_conflict(effective_from):
    operation = _replace(price=_new_price(effective_from=effective_from))
    assert _conflicts(_document(), _delta(operation)) == [(0, "not_after_current")]


def test_a_replacement_with_an_offset_after_the_row_applies():
    operation = _replace(price=_new_price(effective_from="2026-10-20T00:00:00+00:00"))
    result = apply_catalog_delta(_document(), _delta(operation))
    rows = _rows(result, channel="openai-api", region="global")
    assert rows[1]["effective_to"] == "2026-10-20"
    assert rows[2]["effective_from"] == "2026-10-20"
