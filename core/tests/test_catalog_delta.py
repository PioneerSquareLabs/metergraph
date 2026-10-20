"""The pricing delta schema and its pure, atomic application to a catalog
document."""

import copy

import pytest

from metergraph_core import CatalogError
from metergraph_core.catalog_delta import (
    PRICING_DELTA_SCHEMA_VERSION,
    CatalogDeltaError,
    PriceRowKey,
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
