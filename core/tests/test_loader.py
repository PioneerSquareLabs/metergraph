from datetime import datetime, timezone
from decimal import Decimal

import pytest

from metergraph_core import CatalogError, load_catalog, parse_retrieval


def _retrieval_entry(**overrides):
    entry = {
        "channel": "anthropic-api",
        "operation": "web_search",
        "region": "global",
        "unit": "completed_search",
        "per_1k_usd": "10.00",
        "effective_from": "2026-08-26",
        "source_url": "https://example.test/anthropic-search",
    }
    entry.update(overrides)
    return {"retrieval": [entry]}


def test_parse_retrieval_rejects_missing_unit():
    entry = _retrieval_entry()
    del entry["retrieval"][0]["unit"]
    with pytest.raises(CatalogError, match="needs a unit"):
        parse_retrieval(entry)


def test_parse_retrieval_rejects_blank_unit():
    with pytest.raises(CatalogError, match="needs a unit"):
        parse_retrieval(_retrieval_entry(unit="   "))


def test_parse_retrieval_accepts_a_well_formed_entry():
    [price] = parse_retrieval(_retrieval_entry())
    assert price.unit == "completed_search"
    assert price.id == "anthropic-api:web_search:global:2026-08-26"


def test_bundled_catalog_has_identity_and_prices_a_call():
    loaded = load_catalog()
    assert loaded.version == "2026-08-26"
    assert len(loaded.content_hash) == 64
    result = loaded.snapshot.cost(
        provider="openai",
        model="gpt-5.4-mini",
        at=datetime(2026, 8, 17, tzinfo=timezone.utc),
        input_tokens=100_000,
        output_tokens=100_000,
    )
    assert result.canonical_model == "openai/gpt-5.4-mini"
    assert result.price_id == "openai/gpt-5.4-mini:openai-api:global:2026-03-17"
    assert result.cost_usd == Decimal("0.52500000")
    assert result.status == "priced"
    assert result.reasons == ()
