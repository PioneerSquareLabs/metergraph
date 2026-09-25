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
    assert loaded.version == "2026-09-24"
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
    ("model", "input_rate", "output_rate", "cache_read_rate"),
    [
        ("openai/gpt-6-luna", "0.10", "0.50", "0.01"),
        ("openai/gpt-6-sol", "2.00", "10.00", "0.20"),
        ("openai/gpt-6-astra", "10.00", "50.00", "1.00"),
    ],
)
def test_bundled_catalog_prices_gpt6_gateway_candidates(
    model, input_rate, output_rate, cache_read_rate,
):
    loaded = load_catalog()
    result = loaded.price(
        model=model,
        channel="vercel-ai-gateway",
        at=datetime(2026, 9, 23, 12, tzinfo=timezone.utc),
        input_tokens=1_000,
        output_tokens=1_000,
        cache_read_tokens=1_000,
    )
    assert result.status == "priced"
    assert result.canonical_model == model
    assert result.cost_usd == (
        Decimal(output_rate) + Decimal(cache_read_rate)
    ) / 1_000


@pytest.mark.parametrize(
    ("model", "input_rate", "output_rate", "cache_read_rate"),
    [
        ("mistral/ministral-14b", "0.20", "0.20", "0.02"),
        ("google/gemini-3.8-flash", "0.75", "3.75", "0.075"),
        ("google/gemini-3.5-flash-lite", "0.30", "2.50", "0.03"),
        ("anthropic/claude-fable-5.1", "10.00", "50.00", "0.25"),
    ],
)
def test_bundled_catalog_prices_gateway_rows_added_2026_09_24(
    model, input_rate, output_rate, cache_read_rate,
):
    # The gateway reports cached tokens inside input, so 1k input of which 1k
    # was cached bills only the cache-read rate for input.
    loaded = load_catalog()
    result = loaded.price(
        model=model,
        channel="vercel-ai-gateway",
        at=datetime(2026, 9, 24, 12, tzinfo=timezone.utc),
        input_tokens=1_000,
        output_tokens=1_000,
        cache_read_tokens=1_000,
    )
    assert result.status == "priced"
    assert result.canonical_model == model
    assert result.cost_usd == (
        Decimal(output_rate) + Decimal(cache_read_rate)
    ) / 1_000


@pytest.mark.parametrize(
    ("model", "channel", "canonical", "input_rate", "output_rate"),
    [
        ("ministral-14b-2512", "mistral-api", "mistral/ministral-14b", "0.20", "0.20"),
        ("ministral-14b-latest", "mistral-api", "mistral/ministral-14b", "0.20", "0.20"),
        (
            "mistral.ministral-3-14b-instruct", "aws-bedrock",
            "mistral/ministral-14b", "0.20", "0.20",
        ),
        (
            "claude-fable-5-1", "anthropic-api",
            "anthropic/claude-fable-5.1", "10.00", "50.00",
        ),
    ],
)
def test_bundled_catalog_prices_direct_rows_added_2026_09_24(
    model, channel, canonical, input_rate, output_rate,
):
    loaded = load_catalog(region="us-west-2")
    result = loaded.price(
        model=model,
        channel=channel,
        at=datetime(2026, 9, 24, 12, tzinfo=timezone.utc),
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    assert result.status == "priced"
    assert result.canonical_model == canonical
    assert result.cost_usd == Decimal(input_rate) + Decimal(output_rate)


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
