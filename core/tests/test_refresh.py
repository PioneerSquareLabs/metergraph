"""Pure low-risk refresh planning and discovery evidence over supplied
content. Nothing here fetches or applies a delta."""

import copy
from datetime import date
from pathlib import Path

import pytest

from metergraph_core import load_catalog_document
from metergraph_core.catalog_delta import apply_catalog_delta
from metergraph_core.extractors import structural_fingerprint
from metergraph_core.refresh import discovery_evidence, plan_low_risk_refresh
from metergraph_core.sources import parse_source_registry

FIXTURES = Path(__file__).parent / "fixtures" / "sources"
OBSERVED_ON = date(2026, 10, 20)
OPENAI_URL = "https://example.com/openai-pricing"


def _fixture(source_id):
    return (FIXTURES / f"{source_id}.html").read_text()


def _source(source_id, parser, channel, provider, *, low_risk=True, fingerprint="fixture", dimensions=None, low_risk_dimensions=None, url=None):
    dimensions = dimensions or ["input_per_mtok", "cache_read_per_mtok", "cache_write_5m_per_mtok", "output_per_mtok"]
    if fingerprint == "fixture":
        fingerprint = structural_fingerprint(parser, _fixture(source_id))
    return {
        "id": source_id,
        "provider": provider,
        "channel": channel,
        "catalog_channel": True,
        "url": url or f"https://example.com/{parser}-pricing",
        "parser": parser,
        "source_shape": "token_table",
        "clarity": "structured",
        "dimensions": dimensions,
        "fingerprint": fingerprint,
        "low_risk": (
            {"eligible": True, "max_relative_change": "0.5", "dimensions": low_risk_dimensions or dimensions}
            if low_risk
            else {"eligible": False, "max_relative_change": None, "dimensions": []}
        ),
        "evidence": {"store_raw": False, "redistribute_raw": False, "excerpt_chars": 0, "reason": "Synthetic test source."},
    }


def _registry(*sources):
    return parse_source_registry({"schema_version": 1, "sources": list(sources) or [_openai_source()]})


def _openai_source(**overrides):
    return _source("openai-api-pricing", "openai", "openai-api", "openai", url=OPENAI_URL, **overrides)


def _row(**rates):
    row = {"channel": "openai-api", "region": "global", "effective_from": "2026-01-01"}
    row.update(rates)
    row["source_url"] = "https://example.com/model-page"
    return row


def _model(canonical_id, alias, *rows, channel="openai-api", provider="openai"):
    return {
        "canonical_id": canonical_id,
        "publisher": "example",
        "aliases": [{"provider": provider, "alias": alias, "channel": channel}],
        "prices": list(rows),
    }


def _document(*models):
    # Every price in this catalog matches the OpenAI fixture.
    return {
        "version": "2026-10-19",
        "currency": "USD",
        "pricing_verified_at": "2026-10-19",
        "models": list(models)
        or [
            _model("example/gpt-x", "gpt-x", _row(input_per_mtok=2.0, cache_read_per_mtok=0.2, cache_write_5m_per_mtok=2.5, output_per_mtok=8.0)),
            _model("example/gpt-x-mini", "gpt-x-mini", _row(input_per_mtok=0.4, cache_read_per_mtok=0.04, output_per_mtok=1.6)),
            _model("example/gpt-legacy", "gpt-legacy", _row(input_per_mtok=2.5, cache_read_per_mtok=1.25, output_per_mtok=10.0)),
        ],
    }


def _with_gpt_x(**rates):
    document = _document()
    document["models"][0]["prices"][0] = _row(**rates)
    return document


GPT_X = dict(input_per_mtok=2.0, cache_read_per_mtok=0.2, cache_write_5m_per_mtok=2.5, output_per_mtok=8.0)


def _plan(document=None, registry=None, contents=None, observed_on=OBSERVED_ON):
    return plan_low_risk_refresh(
        document if document is not None else _document(),
        registry or _registry(),
        contents if contents is not None else {"openai-api-pricing": _fixture("openai-api-pricing")},
        observed_on=observed_on,
    )


def _reasons(result):
    # A row-level escalation names its catalog row; a page-only one its model.
    return [(e.source_id, e.reason, e.key.canonical_id if e.key else e.model_key) for e in result.escalations]


def test_unchanged_content_yields_an_empty_delta_and_an_unchanged_observation():
    result = _plan()
    assert result.delta["operations"] == []
    assert result.escalations == ()
    [observation] = result.observations
    assert observation.source_id == "openai-api-pricing"
    assert observation.result == "unchanged"
    assert observation.rows_compared == 3
    assert observation.changes == 0


def test_an_in_bounds_change_replaces_the_row_with_its_exact_current_state():
    document = _with_gpt_x(**dict(GPT_X, input_per_mtok=2.5))
    result = _plan(document)
    assert result.escalations == ()
    assert result.delta["operations"] == [
        {
            "op": "replace_price",
            "key": {"canonical_id": "example/gpt-x", "channel": "openai-api", "region": "global", "effective_from": "2026-01-01"},
            "expect": _row(**dict(GPT_X, input_per_mtok=2.5)),
            "price": {
                "channel": "openai-api",
                "region": "global",
                "effective_from": "2026-10-20",
                "input_per_mtok": "2",
                "cache_read_per_mtok": "0.2",
                "cache_write_5m_per_mtok": "2.5",
                "output_per_mtok": "8",
                "source_url": OPENAI_URL,
            },
        }
    ]
    assert result.observations[0].result == "changed"
    assert result.observations[0].changes == 1


def test_a_change_of_exactly_the_bound_is_in_bounds():
    result = _plan(_with_gpt_x(**dict(GPT_X, input_per_mtok=4.0)))
    assert [op["price"]["input_per_mtok"] for op in result.delta["operations"]] == ["2"]


def test_a_change_beyond_the_bound_escalates():
    result = _plan(_with_gpt_x(**dict(GPT_X, input_per_mtok=1.0)))
    assert result.delta["operations"] == []
    [escalation] = result.escalations
    assert escalation.reason == "bound_exceeded"
    assert escalation.result == "escalated"
    assert escalation.dimension == "input_per_mtok"
    assert (escalation.current, escalation.observed) == ("1", "2")
    assert result.observations[0].result == "escalated"


def test_one_escalated_rate_holds_back_the_whole_row():
    result = _plan(_with_gpt_x(**dict(GPT_X, input_per_mtok=1.0, output_per_mtok=7.0)))
    assert result.delta["operations"] == []
    assert [e.reason for e in result.escalations] == ["bound_exceeded"]


def test_rows_are_independent():
    document = _with_gpt_x(**dict(GPT_X, input_per_mtok=1.0))
    document["models"][1]["prices"][0]["output_per_mtok"] = 2.0
    result = _plan(document)
    assert [op["key"]["canonical_id"] for op in result.delta["operations"]] == ["example/gpt-x-mini"]
    assert [e.reason for e in result.escalations] == ["bound_exceeded"]
    assert result.observations[0].result == "changed_and_escalated"


def test_the_new_row_starts_on_the_utc_observation_date():
    result = _plan(_with_gpt_x(**dict(GPT_X, output_per_mtok=9.0)), observed_on=date(2026, 11, 3))
    [operation] = result.delta["operations"]
    assert operation["price"]["effective_from"] == "2026-11-03"


def test_a_row_that_started_on_the_observation_date_escalates():
    document = _with_gpt_x(**dict(GPT_X, output_per_mtok=9.0))
    document["models"][0]["prices"][0]["effective_from"] = "2026-10-20"
    result = _plan(document)
    assert result.delta["operations"] == []
    assert _reasons(result) == [("openai-api-pricing", "not_after_current", "example/gpt-x")]


def test_a_second_change_to_one_row_on_the_same_utc_day_escalates():
    first = _plan(_with_gpt_x(**dict(GPT_X, output_per_mtok=9.0)))
    applied = apply_catalog_delta(_with_gpt_x(**dict(GPT_X, output_per_mtok=9.0)), first.delta)
    changed_again = _fixture("openai-api-pricing").replace("[0,2.5],[0,8]", "[0,2.5],[0,8.5]")
    second = _plan(applied, contents={"openai-api-pricing": changed_again})
    assert second.delta["operations"] == []
    assert [e.reason for e in second.escalations] == ["not_after_current"]


def test_a_dimension_the_row_lacks_escalates_as_new():
    document = _document()
    del document["models"][1]["prices"][0]["cache_read_per_mtok"]
    result = _plan(document)
    assert result.delta["operations"] == []
    assert [(e.reason, e.dimension) for e in result.escalations] == [("new_dimension", "cache_read_per_mtok")]


def test_a_change_outside_the_low_risk_dimensions_escalates():
    registry = _registry(_openai_source(low_risk_dimensions=["input_per_mtok", "output_per_mtok"]))
    result = _plan(_with_gpt_x(**dict(GPT_X, cache_read_per_mtok=0.25)), registry)
    assert result.delta["operations"] == []
    assert [(e.reason, e.dimension) for e in result.escalations] == [("dimension_not_allowed", "cache_read_per_mtok")]


def test_a_change_to_a_row_with_rates_the_source_does_not_represent_escalates():
    result = _plan(_with_gpt_x(**dict(GPT_X, output_per_mtok=9.0, batch_input_per_mtok=1.0)))
    assert result.delta["operations"] == []
    assert [(e.reason, e.dimension) for e in result.escalations] == [("unrepresented_dimension", "batch_input_per_mtok")]


def test_an_unchanged_row_with_unrepresented_rates_is_fine():
    result = _plan(_with_gpt_x(**dict(GPT_X, batch_input_per_mtok=1.0)))
    assert result.escalations == ()


def test_a_model_the_catalog_lacks_escalates_as_unknown():
    document = _document()
    del document["models"][2]
    result = _plan(document)
    assert result.delta["operations"] == []
    assert _reasons(result) == [("openai-api-pricing", "unknown_model", "gpt-legacy")]


def test_a_catalog_row_the_page_no_longer_lists_escalates():
    document = _document()
    document["models"].append(_model("example/gpt-gone", "gpt-gone", _row(input_per_mtok=1.0, output_per_mtok=2.0)))
    result = _plan(document)
    assert _reasons(result) == [("openai-api-pricing", "model_not_listed", "example/gpt-gone")]


def test_a_region_the_catalog_lacks_escalates_as_new():
    source = _source("xai-api-pricing", "xai", "xai-api", "xai", dimensions=["input_per_mtok", "output_per_mtok", "cache_read_per_mtok"])
    document = _document(
        _model("example/grok-x", "grok-x", dict(_row(input_per_mtok=2.0, cache_read_per_mtok=0.5, output_per_mtok=6.0), channel="xai-api"), channel="xai-api", provider="x-ai")
    )
    result = plan_low_risk_refresh(document, _registry(source), {"xai-api-pricing": _fixture("xai-api-pricing")}, observed_on=OBSERVED_ON)
    assert result.delta["operations"] == []
    [escalation] = result.escalations
    assert (escalation.reason, escalation.model_key, escalation.region) == ("new_region", "grok-x", "us")


def test_a_source_that_is_not_low_risk_escalates_without_comparing():
    registry = _registry(_openai_source(low_risk=False))
    result = _plan(_with_gpt_x(**dict(GPT_X, output_per_mtok=9.0)), registry)
    assert result.delta["operations"] == []
    assert _reasons(result) == [("openai-api-pricing", "not_low_risk", None)]


def test_a_missing_fingerprint_escalates():
    result = _plan(_with_gpt_x(**dict(GPT_X, output_per_mtok=9.0)), _registry(_openai_source(fingerprint=None)))
    assert result.delta["operations"] == []
    assert [e.reason for e in result.escalations] == ["fingerprint_missing"]


def test_a_mismatched_fingerprint_escalates():
    registry = _registry(_openai_source(fingerprint="sha256:" + "0" * 64))
    result = _plan(_with_gpt_x(**dict(GPT_X, output_per_mtok=9.0)), registry)
    assert result.delta["operations"] == []
    [escalation] = result.escalations
    assert escalation.reason == "fingerprint_mismatch"
    assert result.observations[0].fingerprint == structural_fingerprint("openai", _fixture("openai-api-pricing"))


def test_missing_content_for_a_low_risk_source_is_observed_and_escalated():
    result = _plan(contents={})
    assert [e.reason for e in result.escalations] == ["content_missing"]
    assert result.observations[0].result == "missing"


def test_a_non_positive_rate_escalates():
    content = _fixture("openai-api-pricing").replace("[0,2],[0,0.2],[0,2.5],[0,8]", "[0,0],[0,0.2],[0,2.5],[0,8]")
    result = _plan(contents={"openai-api-pricing": content})
    assert result.delta["operations"] == []
    assert [(e.reason, e.dimension) for e in result.escalations] == [("non_positive_rate", "input_per_mtok")]


def test_a_missing_required_rate_escalates():
    source = _source("anthropic-api-pricing", "anthropic", "anthropic-api", "anthropic")
    document = _document(
        _model("example/claude-example-5", "claude-example-5", dict(_row(input_per_mtok=5.0, output_per_mtok=25.0, cache_write_5m_per_mtok=6.25, cache_read_per_mtok=0.5), channel="anthropic-api"), channel="anthropic-api", provider="anthropic"),
        _model("example/claude-example-5-5", "claude-example-5-5", dict(_row(input_per_mtok=4.0, output_per_mtok=20.0, cache_write_5m_per_mtok=5.0, cache_read_per_mtok=0.4), channel="anthropic-api"), channel="anthropic-api", provider="anthropic"),
    )
    content = _fixture("anthropic-api-pricing").replace("<td>$25 / MTok</td>", "<td>Contact us</td>")
    result = plan_low_risk_refresh(document, _registry(source), {"anthropic-api-pricing": content}, observed_on=OBSERVED_ON)
    assert [(e.reason, e.dimension) for e in result.escalations] == [("missing_rate", "output_per_mtok")]


def test_an_ambiguous_parse_escalates_the_row():
    content = _fixture("openai-api-pricing").replace(
        "[1,[[0,&quot;gpt-x-mini&quot;]",
        "[1,[[0,&quot;gpt-x&quot;],[0,2.2],[0,0.2],[0,2.5],[0,8.8]]][1,[[0,&quot;gpt-x-mini&quot;]",
    )
    result = _plan(_with_gpt_x(**dict(GPT_X, output_per_mtok=9.0)), contents={"openai-api-pricing": content})
    assert result.delta["operations"] == []
    assert _reasons(result) == [("openai-api-pricing", "ambiguous_parse", "example/gpt-x")]


def test_a_duplicated_model_entry_escalates():
    document = _document()
    duplicate = _model("example/gpt-x", "gpt-x-alt", dict(_row(input_per_mtok=1.0, output_per_mtok=1.0), channel="vercel-ai-gateway"), channel="vercel-ai-gateway")
    document["models"].append(duplicate)
    load_catalog_document(document)  # a valid catalog, but two entries share an id
    result = _plan(document)
    assert _reasons(result) == [("openai-api-pricing", "duplicate_model_row", "example/gpt-x")]


def test_the_delta_references_its_observations_and_evidence():
    result = _plan(_with_gpt_x(**dict(GPT_X, output_per_mtok=9.0)))
    assert result.delta["schema_version"] == 1
    assert result.delta["kind"] == "pricing_delta"
    assert result.delta["provenance"]["observations"] == ["openai-api-pricing@2026-10-20"]
    assert result.delta["provenance"]["evidence"] == [
        {
            "source_id": "openai-api-pricing",
            "url": OPENAI_URL,
            "fingerprint": structural_fingerprint("openai", _fixture("openai-api-pricing")),
        }
    ]
    assert result.observations[0].id == "openai-api-pricing@2026-10-20"


def test_every_low_risk_source_gets_an_observation_even_without_changes():
    anthropic = _source("anthropic-api-pricing", "anthropic", "anthropic-api", "anthropic")
    result = _plan(registry=_registry(_openai_source(), anthropic))
    assert [(o.source_id, o.result) for o in result.observations] == [
        ("openai-api-pricing", "unchanged"),
        ("anthropic-api-pricing", "missing"),
    ]


def test_the_refresh_delta_applies_cleanly():
    document = _with_gpt_x(**dict(GPT_X, output_per_mtok=9.0))
    applied = apply_catalog_delta(document, _plan(document).delta)
    rows = applied["models"][0]["prices"]
    assert [(row["effective_from"], row.get("effective_to")) for row in rows] == [
        ("2026-01-01", "2026-10-20"),
        ("2026-10-20", None),
    ]
    assert load_catalog_document(applied).version == "2026-10-19"


def test_refresh_does_not_mutate_its_inputs():
    document = _with_gpt_x(**dict(GPT_X, output_per_mtok=9.0))
    contents = {"openai-api-pricing": _fixture("openai-api-pricing")}
    before = (copy.deepcopy(document), dict(contents))
    _plan(document, contents=contents)
    assert (document, contents) == before


def test_refresh_rejects_an_invalid_catalog():
    with pytest.raises(ValueError, match="currency must be USD"):
        _plan(dict(_document(), currency="EUR"))


def test_refresh_is_deterministic():
    document = _with_gpt_x(**dict(GPT_X, output_per_mtok=9.0, input_per_mtok=1.0))
    assert _plan(document) == _plan(document)


def test_the_result_serializes_to_json_data():
    data = _plan(_with_gpt_x(**dict(GPT_X, input_per_mtok=1.0))).as_json()
    assert data["observations"][0]["result"] == "escalated"
    assert data["escalations"][0] == {
        "result": "escalated",
        "source_id": "openai-api-pricing",
        "reason": "bound_exceeded",
        "key": {"canonical_id": "example/gpt-x", "channel": "openai-api", "region": "global", "effective_from": "2026-01-01"},
        "model_key": "gpt-x",
        "region": "global",
        "dimension": "input_per_mtok",
        "current": "1",
        "observed": "2",
        "detail": "relative change 1 exceeds 0.5",
    }


# --- Discovery evidence ---------------------------------------------------------


def test_discovery_evidence_lists_candidates_with_prior_values_and_reasons():
    source = _registry(_openai_source(low_risk=False)).get("openai-api-pricing")
    document = _with_gpt_x(**dict(GPT_X, output_per_mtok=9.0))
    del document["models"][2]
    evidence = discovery_evidence(source, _fixture("openai-api-pricing"), document, observed_on=OBSERVED_ON)
    assert evidence["source_id"] == "openai-api-pricing"
    assert evidence["observed_on"] == "2026-10-20"
    assert evidence["fingerprint_matches"] is True
    assert evidence["applicable"] is True
    assert evidence["candidates"] == [
        {"model_key": "gpt-legacy", "region": "global", "canonical_id": None, "reason": "unknown_model", "prior": None, "candidate": {"input_per_mtok": "2.5", "cache_read_per_mtok": "1.25", "output_per_mtok": "10"}},
        {"model_key": "gpt-x", "region": "global", "canonical_id": "example/gpt-x", "reason": "changed", "prior": {"input_per_mtok": "2", "cache_read_per_mtok": "0.2", "cache_write_5m_per_mtok": "2.5", "output_per_mtok": "9"}, "candidate": {"input_per_mtok": "2", "cache_read_per_mtok": "0.2", "cache_write_5m_per_mtok": "2.5", "output_per_mtok": "8"}},
    ]
    assert evidence["retention"] == {"store_raw": False, "redistribute_raw": False, "excerpt_chars": 0, "reason": "Synthetic test source."}
    assert evidence["excerpt"] is None


def test_discovery_evidence_proposes_operations_only_for_changed_catalog_rows():
    source = _registry(_openai_source(low_risk=False)).get("openai-api-pricing")
    # Beyond the low-risk bound, and the catalog lacks gpt-legacy.
    document = _with_gpt_x(**dict(GPT_X, input_per_mtok=1.0))
    del document["models"][2]
    evidence = discovery_evidence(source, _fixture("openai-api-pricing"), document, observed_on=OBSERVED_ON)
    assert evidence["operations"] == [
        {
            "op": "replace_price",
            "key": {"canonical_id": "example/gpt-x", "channel": "openai-api", "region": "global", "effective_from": "2026-01-01"},
            "expect": _row(**dict(GPT_X, input_per_mtok=1.0)),
            "price": {
                "channel": "openai-api",
                "region": "global",
                "effective_from": "2026-10-20",
                "input_per_mtok": "2",
                "cache_read_per_mtok": "0.2",
                "cache_write_5m_per_mtok": "2.5",
                "output_per_mtok": "8",
                "source_url": OPENAI_URL,
            },
        }
    ]


def test_discovery_evidence_proposes_no_operation_for_a_row_starting_that_day():
    source = _registry(_openai_source(low_risk=False)).get("openai-api-pricing")
    document = _with_gpt_x(**dict(GPT_X, input_per_mtok=1.0))
    document["models"][0]["prices"][0]["effective_from"] = "2026-10-20"
    evidence = discovery_evidence(source, _fixture("openai-api-pricing"), document, observed_on=OBSERVED_ON)
    assert evidence["operations"] == []
    assert [c["reason"] for c in evidence["candidates"]] == ["changed"]


def test_discovery_evidence_for_gpu_licensing_carries_no_token_candidates():
    source = parse_source_registry(
        {
            "schema_version": 1,
            "sources": [
                dict(
                    _source("nvidia-nim-pricing", "nvidia", "nvidia-ai-enterprise", "nvidia", low_risk=False, dimensions=["gpu_hour_usd", "license_per_gpu_year_usd"]),
                    catalog_channel=False,
                    source_shape="gpu_hour_or_license",
                    clarity="prose",
                )
            ],
        }
    ).get("nvidia-nim-pricing")
    evidence = discovery_evidence(source, _fixture("nvidia-nim-pricing"), _document(), observed_on=OBSERVED_ON)
    assert evidence["applicable"] is False
    assert evidence["candidates"] == []
    assert evidence["evidence"]["candidates"] == {"license_per_gpu_year_usd": "1000", "gpu_hour_usd": "0.50"}


def test_discovery_evidence_quotes_only_the_permitted_excerpt():
    entry = _openai_source(low_risk=False)
    entry["evidence"] = {"store_raw": True, "redistribute_raw": False, "excerpt_chars": 12, "reason": "Excerpts only."}
    source = _registry(entry).get("openai-api-pricing")
    evidence = discovery_evidence(source, "<p>Prices changed on the page today.</p>", _document(), observed_on=OBSERVED_ON)
    assert evidence["excerpt"] == "Prices chang"
