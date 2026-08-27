from datetime import datetime, timezone
from decimal import Decimal

import pytest

from metergraph_core import CatalogError, load_catalog, parse_catalog

LOADED = load_catalog()
VERSION = LOADED.version
DOC = LOADED.document
SNAPSHOT = LOADED.snapshot


def _at(day: str) -> datetime:
    return datetime.fromisoformat(day).replace(tzinfo=timezone.utc)


def test_prices_yaml_parses():
    assert VERSION
    assert DOC["models"]
    assert LOADED.currency == "USD"
    assert LOADED.pricing_verified_at.isoformat() == "2026-08-27"


def test_resolve_price_by_deployment_identity_and_channel():
    resolved = SNAPSHOT.resolve_price(
        model="  OPENAI/GPT-5.6-LUNA  ",
        channel=" VERCEL-AI-GATEWAY ",
        at=_at("2026-08-01"),
    )

    assert resolved is not None
    assert resolved.canonical_model == "openai/gpt-5.6-luna"
    assert resolved.price.id == (
        "openai/gpt-5.6-luna:vercel-ai-gateway:global:2026-07-30"
    )
    assert resolved.price.input_per_mtok == Decimal("0.20")
    assert resolved.price.source_url == "https://ai-gateway.vercel.sh/v1/models"
    assert resolved.rules["input_includes_cache_read"] is True
    with pytest.raises(TypeError):
        resolved.rules["input_includes_cache_read"] = False
    with pytest.raises(TypeError):
        resolved.price.rules["long_context"] = {}


def test_resolve_price_does_not_guess_another_channel():
    assert SNAPSHOT.resolve_price(
        model="openai/gpt-4o",
        channel="vercel-ai-gateway",
        at=_at("2026-08-01"),
    ) is None


def test_gemini_direct_price_changes_on_its_effective_date():
    before = SNAPSHOT.resolve_price(
        model="gemini-3.6-flash", channel="google-api", at=_at("2026-08-19")
    )
    after = SNAPSHOT.resolve_price(
        model="gemini-3.6-flash", channel="google-api", at=_at("2026-08-20")
    )

    assert before is not None
    assert after is not None
    assert before.price.input_per_mtok == Decimal("1.50")
    assert after.price.input_per_mtok == Decimal("0.75")


@pytest.mark.parametrize(
    "model,channel,input_rate,output_rate",
    [
        ("gpt-4o-mini", "openai-api", "0.15", "0.60"),
        ("gpt-5.4-mini", "openai-api", "0.75", "4.50"),
        ("claude-opus-4-8", "anthropic-api", "5.00", "25.00"),
        ("claude-haiku-4-5", "anthropic-api", "1.00", "5.00"),
        ("anthropic/claude-opus-5", "vercel-ai-gateway", "5.00", "25.00"),
        ("anthropic/claude-sonnet-5", "vercel-ai-gateway", "2.00", "10.00"),
        ("anthropic/claude-3-haiku", "vercel-ai-gateway", "0.25", "1.25"),
        ("openai/gpt-5.6-sol", "vercel-ai-gateway", "5.00", "30.00"),
        ("openai/gpt-5.6-luna", "vercel-ai-gateway", "0.20", "1.20"),
        ("gpt-5-mini", "openai-api", "0.25", "2.00"),
        ("openai/gpt-5-mini", "vercel-ai-gateway", "0.25", "2.00"),
        ("openai/gpt-5.6-terra", "vercel-ai-gateway", "2.00", "12.00"),
        ("google/gemini-3.1-pro-preview", "vercel-ai-gateway", "2.00", "12.00"),
        ("gemini-3.6-flash", "google-api", "0.75", "3.75"),
        ("google/gemini-3.6-flash", "vercel-ai-gateway", "1.50", "7.50"),
        ("google/gemini-3.7-flash", "vercel-ai-gateway", "0.75", "3.75"),
        ("google/gemma-4-31b-it", "vercel-ai-gateway", "0.14", "0.40"),
        ("meta/muse-glimmer-30b", "vercel-ai-gateway", "0.35", "1.50"),
        ("thinkingmachines/inkling", "vercel-ai-gateway", "1.00", "4.05"),
        ("zai/glm-5.2", "vercel-ai-gateway", "0.80", "2.55"),
        ("moonshotai/kimi-k3", "vercel-ai-gateway", "2.90", "14.00"),
        ("minimax/minimax-m3", "vercel-ai-gateway", "0.30", "1.20"),
        ("nvidia/nemotron-3-super-120b-a12b", "vercel-ai-gateway", "0.15", "0.65"),
        ("deepseek/deepseek-v4-pro", "vercel-ai-gateway", "0.435", "0.87"),
        ("deepseek/deepseek-v4-flash", "vercel-ai-gateway", "0.14", "0.28"),
        ("fireworks:accounts/fireworks/models/glm-5p2", "fireworks-api", "1.40", "4.40"),
        ("fireworks:accounts/fireworks/models/qwen3p7-plus", "fireworks-api", "0.40", "1.60"),
        ("xai/grok-4.1-fast-reasoning", "vercel-ai-gateway", "0.20", "0.50"),
    ],
)
def test_pipeline_catalog_compatibility(model, channel, input_rate, output_rate):
    resolved = SNAPSHOT.resolve_price(
        model=model,
        channel=channel,
        at=_at("2026-08-25"),
    )

    assert resolved is not None
    assert resolved.price.input_per_mtok == Decimal(input_rate)
    assert resolved.price.output_per_mtok == Decimal(output_rate)


@pytest.mark.parametrize(
    "field,value",
    [("currency", "EUR"), ("currency", None), ("pricing_verified_at", "soon"),
     ("pricing_verified_at", None)],
)
def test_catalog_metadata_is_required_and_validated(field, value):
    doc = {"version": "test", "currency": "USD", "pricing_verified_at": "2026-08-24", "models": []}
    if value is None:
        doc.pop(field)
    else:
        doc[field] = value

    with pytest.raises(CatalogError):
        parse_catalog(doc)


def test_price_source_is_required():
    doc = {
        "version": "test",
        "currency": "USD",
        "pricing_verified_at": "2026-08-24",
        "models": [
            {
                "canonical_id": "example/model",
                "aliases": [
                    {"provider": "example", "alias": "model", "channel": "api"}
                ],
                "prices": [
                    {
                        "channel": "api",
                        "effective_from": "2026-08-24",
                        "input_per_mtok": 1,
                        "output_per_mtok": 2,
                    }
                ],
            }
        ],
    }

    with pytest.raises(CatalogError, match="source_url"):
        parse_catalog(doc)


def test_openai_cache_read_included_in_input():
    result = SNAPSHOT.cost(
        provider="openai",
        model="gpt-5.6-luna",
        at=_at("2026-07-15"),
        input_tokens=100_000,
        output_tokens=0,
        cache_read_tokens=50_000,
    )
    assert result.status == "priced"
    assert result.canonical_model == "openai/gpt-5.6-luna"
    assert result.cost_usd == Decimal("0.05") + Decimal("0.005")


def test_gateway_luna_price_drop_does_not_reprice_history():
    result = SNAPSHOT.cost(
        provider="openai",
        model="openai/gpt-5.6-luna",
        at=_at("2026-07-15"),
        input_tokens=100_000,
        output_tokens=100_000,
    )
    assert result.status == "priced"
    assert result.cost_usd == Decimal("0.70")


def test_partial_price_reports_uncaptured_fees():
    result = SNAPSHOT.cost(
        provider="perplexity-ai",
        model="sonar",
        at=_at("2026-08-10"),
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    assert result.canonical_model == "perplexity/sonar"
    assert result.price_id == "perplexity/sonar:perplexity-api:global:2025-04-18"
    assert result.cost_usd == Decimal("2.00000000")
    assert result.status == "partial"
    assert result.reasons == ("uncaptured_fees",)


@pytest.mark.parametrize(
    ("provider", "model", "expected_canonical", "expected_channel", "expected_cost"),
    [
        (
            "openai",
            "gpt-5.6-luna",
            "openai/gpt-5.6-luna",
            "openai-api",
            Decimal("0.14"),
        ),
        (
            "openai",
            "openai/gpt-5.6-luna",
            "openai/gpt-5.6-luna",
            "vercel-ai-gateway",
            Decimal("0.14"),
        ),
        (
            "anthropic",
            "claude-haiku-4-5",
            "anthropic/claude-haiku-4.5",
            "anthropic-api",
            Decimal("0.60"),
        ),
        (
            "anthropic",
            "anthropic/claude-haiku-4.5",
            "anthropic/claude-haiku-4.5",
            "vercel-ai-gateway",
            Decimal("0.60"),
        ),
        (
            "anthropic",
            "claude-opus-5",
            "anthropic/claude-opus-5",
            "anthropic-api",
            Decimal("3.00"),
        ),
        (
            "anthropic",
            "anthropic/claude-opus-5",
            "anthropic/claude-opus-5",
            "vercel-ai-gateway",
            Decimal("3.00"),
        ),
        (
            "openai",
            "gpt-5.4-mini",
            "openai/gpt-5.4-mini",
            "openai-api",
            Decimal("0.525"),
        ),
        (
            "openai",
            "openai/gpt-5.4-mini",
            "openai/gpt-5.4-mini",
            "vercel-ai-gateway",
            Decimal("0.525"),
        ),
        (
            "google",
            "google/gemma-4-26b-a4b-it",
            "google/gemma-4-26b-a4b-it",
            "vercel-ai-gateway",
            Decimal("0.075"),
        ),
        (
            "nvidia",
            "nvidia/nemotron-3-super-120b-a12b",
            "nvidia/nemotron-3-super-120b-a12b",
            "vercel-ai-gateway",
            Decimal("0.080"),
        ),
        (
            "meta",
            "meta/muse-spark-1.1",
            "meta/muse-spark-1.1",
            "vercel-ai-gateway",
            Decimal("0.550"),
        ),
    ],
)
def test_design_partner_models_are_priced(
    provider, model, expected_canonical, expected_channel, expected_cost
):
    result = SNAPSHOT.cost(
        provider=provider,
        model=model,
        at=_at("2026-08-17"),
        input_tokens=100_000,
        output_tokens=100_000,
    )
    assert result.status == "priced"
    assert result.canonical_model == expected_canonical
    assert f":{expected_channel}:" in result.price_id
    assert result.cost_usd == expected_cost


@pytest.mark.parametrize(
    (
        "provider",
        "model",
        "expected_canonical",
        "expected_channel",
        "expected_cost",
        "expected_status",
        "expected_reasons",
    ),
    [
        (
            "perplexity-ai",
            "sonar",
            "perplexity/sonar",
            "perplexity-api",
            Decimal("2.00"),
            "partial",
            ("uncaptured_fees",),
        ),
        (
            "openai",
            "gpt-5.4-nano",
            "openai/gpt-5.4-nano",
            "openai-api",
            Decimal("1.45"),
            "priced",
            (),
        ),
        (
            "vertex-ai",
            "gemini-3.5-flash",
            "google/gemini-3.5-flash",
            "google-vertex-ai",
            Decimal("10.50"),
            "priced",
            (),
        ),
        (
            "openai",
            "gpt-5.4",
            "openai/gpt-5.4",
            "openai-api",
            Decimal("27.50"),
            "priced",
            (),
        ),
        (
            "deepseek",
            "deepseek-v4-flash",
            "deepseek/v4-flash",
            "deepseek-api",
            Decimal("0.42"),
            "priced",
            (),
        ),
        (
            "anthropic",
            "claude-opus-4-8",
            "anthropic/claude-opus-4.8",
            "anthropic-api",
            Decimal("30.00"),
            "priced",
            (),
        ),
        (
            "x-ai",
            "grok-4.3",
            "xai/grok-4.3",
            "xai-api",
            Decimal("7.50"),
            "priced",
            (),
        ),
        (
            "vertex-ai",
            "gemini-3.1-pro-preview",
            "google/gemini-3.1-pro-preview",
            "google-vertex-ai",
            Decimal("22.00"),
            "priced",
            (),
        ),
        (
            "deepseek",
            "deepseek-v4-pro",
            "deepseek/v4-pro",
            "deepseek-api",
            Decimal("1.305"),
            "priced",
            (),
        ),
    ],
)
def test_observed_design_partner_models_are_priced(
    provider,
    model,
    expected_canonical,
    expected_channel,
    expected_cost,
    expected_status,
    expected_reasons,
):
    result = SNAPSHOT.cost(
        provider=provider,
        model=model,
        at=_at("2026-08-10"),
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    assert result.status == expected_status
    assert result.canonical_model == expected_canonical
    assert f":{expected_channel}:" in result.price_id
    assert result.cost_usd == expected_cost
    assert result.reasons == expected_reasons


def test_gateway_input_excludes_cache_reads_and_writes():
    result = SNAPSHOT.cost(
        provider="openai",
        model="openai/gpt-5.6-luna",
        at=_at("2026-08-17"),
        input_tokens=200_000,
        output_tokens=0,
        cache_read_tokens=40_000,
        cache_write_tokens=60_000,
    )
    assert result.status == "priced"
    assert result.cost_usd == Decimal("0.03580000")


def test_gpt_4o_mini_is_priced():
    result = SNAPSHOT.cost(
        provider="openai",
        model="gpt-4o-mini",
        at=_at("2026-07-22"),
        input_tokens=100_000,
        output_tokens=50_000,
        cache_read_tokens=20_000,
    )
    assert result.status == "priced"
    assert result.canonical_model == "openai/gpt-4o-mini"
    # billable input excludes the cached tokens (input_includes_cache_read)
    expected = (
        Decimal(80_000) * Decimal("0.15") / Decimal(1_000_000)
        + Decimal(50_000) * Decimal("0.60") / Decimal(1_000_000)
        + Decimal(20_000) * Decimal("0.075") / Decimal(1_000_000)
    )
    assert result.cost_usd == expected.quantize(Decimal("0.00000001"))


@pytest.mark.parametrize(
    ("model", "input_rate", "output_rate"),
    [
        ("gpt-4o", Decimal("2.50"), Decimal("10.00")),
        ("gpt-4.1", Decimal("2.00"), Decimal("8.00")),
        ("gpt-4.1-mini", Decimal("0.40"), Decimal("1.60")),
        ("gpt-4.1-nano", Decimal("0.10"), Decimal("0.40")),
    ],
)
def test_legacy_openai_models_are_priced(model, input_rate, output_rate):
    result = SNAPSHOT.cost(
        provider="openai",
        model=model,
        at=_at("2026-07-22"),
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    assert result.status == "priced"
    assert result.canonical_model == f"openai/{model}"
    assert result.cost_usd == input_rate + output_rate


@pytest.mark.parametrize(
    ("provider", "model", "region"),
    [
        ("anthropic", "claude-sonnet-5", "global"),
        ("bedrock", "us.anthropic.claude-sonnet-5", "us-west-2"),
    ],
)
@pytest.mark.parametrize(
    "at",
    [
        "2026-08-31T23:59:59",
        "2026-09-01T00:00:00",
        "2026-09-02T00:00:00",
    ],
)
def test_sonnet_5_cancelled_increase_never_takes_effect(provider, model, region, at):
    snapshot = load_catalog(region=region).snapshot
    result = snapshot.cost(
        provider=provider,
        model=model,
        at=_at(at),
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    assert result.status == "priced"
    assert result.cost_usd == Decimal("12")


def test_gemini_provider_synonym_and_long_context():
    result = SNAPSHOT.cost(
        provider="gemini",
        model="gemini-2.5-pro",
        at=_at("2026-07-15"),
        input_tokens=300_000,
        output_tokens=1_000,
    )
    assert result.status == "priced"
    expected = (
        Decimal(300_000) * Decimal("1.25") * 2 / Decimal(1_000_000)
        + Decimal(1_000) * Decimal("10") * Decimal("1.5") / Decimal(1_000_000)
    )
    assert result.cost_usd == expected.quantize(Decimal("0.00000001"))


def test_unknown_model_is_unpriced():
    result = SNAPSHOT.cost(
        provider="openai",
        model="gpt-99",
        at=_at("2026-07-15"),
        input_tokens=10,
        output_tokens=10,
    )
    assert result.status == "unpriced"
    assert result.cost_usd is None


def test_batch_rates():
    result = SNAPSHOT.cost(
        provider="anthropic",
        model="claude-haiku-4-5",
        at=_at("2026-07-15"),
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        batch=True,
    )
    assert result.cost_usd == Decimal("0.50") + Decimal("2.50")


def test_overlapping_windows_rejected():
    doc = {
        "version": "test",
        "currency": "USD",
        "pricing_verified_at": "2026-08-24",
        "models": [
            {
                "canonical_id": "x/y",
                "aliases": [{"provider": "x", "alias": "y", "channel": "c"}],
                "prices": [
                    {
                        "channel": "c",
                        "effective_from": "2026-01-01",
                        "input_per_mtok": 1,
                        "source_url": "https://example.test/prices",
                    },
                    {
                        "channel": "c",
                        "effective_from": "2026-02-01",
                        "input_per_mtok": 2,
                        "source_url": "https://example.test/prices",
                    },
                ],
            }
        ],
    }
    try:
        parse_catalog(doc)
    except CatalogError:
        pass
    else:
        raise AssertionError("expected CatalogError for overlapping open windows")


def test_duplicate_alias_rejected():
    doc = {
        "version": "test",
        "currency": "USD",
        "pricing_verified_at": "2026-08-24",
        "models": [
            {
                "canonical_id": "x/y",
                "aliases": [
                    {"provider": "x", "alias": "y", "channel": "c"},
                    {"provider": "x", "alias": "y", "channel": "d"},
                ],
                "prices": [],
            }
        ],
    }
    with pytest.raises(CatalogError):
        parse_catalog(doc)


def test_malformed_catalog_rejected():
    with pytest.raises(CatalogError):
        parse_catalog({"models": []})
    with pytest.raises(CatalogError):
        parse_catalog({"version": "test"})
