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
    assert loaded.version == "2026-09-02"
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


@pytest.mark.parametrize(
    ("model", "canonical", "input_rate", "output_rate"),
    [
        ("us.openai.gpt-5.6-sol", "openai/gpt-5.6-sol", "5.50", "33.00"),
        ("us.openai.gpt-5.6-terra", "openai/gpt-5.6-terra", "2.20", "13.20"),
        ("us.openai.gpt-5.6-luna", "openai/gpt-5.6-luna", "0.22", "1.32"),
        ("us.anthropic.claude-sonnet-5", "anthropic/claude-sonnet-5", "2.00", "10.00"),
        ("google.gemma-3-27b-it", "google/gemma-3-27b-it", "0.23", "0.38"),
        ("moonshotai.kimi-k2.5", "moonshotai/kimi-k2.5", "0.60", "3.00"),
        ("deepseek.v3.2", "deepseek/v3.2", "0.62", "1.85"),
        ("deepseek.v3-v1:0", "deepseek/v3.1", "0.58", "1.68"),
    ],
)
def test_bundled_catalog_prices_aws_bedrock_analysis_models(
    model, canonical, input_rate, output_rate,
):
    loaded = load_catalog(region="us-west-2")
    result = loaded.price(
        model=model,
        channel="aws-bedrock",
        at=datetime(2026, 8, 27, 12, tzinfo=timezone.utc),
        input_tokens=100_000,
        output_tokens=1_000_000,
    )
    assert result.status == "priced"
    assert result.canonical_model == canonical
    assert result.cost_usd == Decimal(input_rate) / 10 + Decimal(output_rate)
    assert loaded.canonical_model_id("bedrock", model) == canonical
