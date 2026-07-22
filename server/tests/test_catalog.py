from datetime import datetime, timezone
from decimal import Decimal

import pytest

from metergraph_server import prices

VERSION, DOC, SNAPSHOT = prices.load()


def _at(day: str) -> datetime:
    return datetime.fromisoformat(day).replace(tzinfo=timezone.utc)


def test_prices_yaml_parses():
    assert VERSION
    assert DOC["models"]


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


def test_anthropic_effective_dating():
    early = SNAPSHOT.cost(
        provider="anthropic",
        model="claude-sonnet-5",
        at=_at("2026-07-15"),
        input_tokens=1_000_000,
        output_tokens=0,
    )
    late = SNAPSHOT.cost(
        provider="anthropic",
        model="claude-sonnet-5",
        at=_at("2026-09-02"),
        input_tokens=1_000_000,
        output_tokens=0,
    )
    assert early.cost_usd == Decimal("2")
    assert late.cost_usd == Decimal("3")


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
        "models": [
            {
                "canonical_id": "x/y",
                "aliases": [{"provider": "x", "alias": "y", "channel": "c"}],
                "prices": [
                    {"channel": "c", "effective_from": "2026-01-01", "input_per_mtok": 1},
                    {"channel": "c", "effective_from": "2026-02-01", "input_per_mtok": 2},
                ],
            }
        ],
    }
    try:
        prices.parse(doc)
    except prices.PricesError:
        pass
    else:
        raise AssertionError("expected PricesError for overlapping open windows")
