"""Public channel-aware deployment pricing and identity helpers.

metergraph-core lets a caller price an observed deployment from the identity it
already has -- a model id, the pricing channel it was served on, the execution
time, and the token usage -- without rebuilding provider/alias indexes from the
catalog document. Channel selection is exact: a model priced on a channel the
catalog does not carry for it stays explicitly unpriced rather than being
repriced off another channel. Two thin identity helpers round out the surface:
canonical-id resolution (never guessing an ambiguous alias) and the direct
channel a recorded source provider bills on.

The synthetic catalog below pins the contract against deterministic numbers so
the assertions never depend on the shipped price list.
"""

import textwrap
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from metergraph_core import direct_channel_for_provider, load_catalog


_SYNTHETIC_CATALOG = textwrap.dedent(
    """
    version: "test-public-1"
    currency: USD
    pricing_verified_at: "2026-08-01"
    models:
      - canonical_id: openai/gpt-x
        aliases:
          - {provider: openai, alias: gpt-x, channel: openai-api}
          - {provider: openai, alias: openai/gpt-x, channel: vercel-ai-gateway}
          - {provider: openai, alias: shared-name, channel: openai-api}
        prices:
          - channel: openai-api
            region: global
            effective_from: "2026-01-01"
            effective_to: "2026-06-01"
            input_per_mtok: 2.0
            output_per_mtok: 4.0
            source_url: https://example.test/openai
          - channel: openai-api
            region: global
            effective_from: "2026-06-01"
            input_per_mtok: 1.0
            output_per_mtok: 2.0
            source_url: https://example.test/openai-new
          - channel: vercel-ai-gateway
            region: global
            effective_from: "2026-01-01"
            input_per_mtok: 3.0
            output_per_mtok: 6.0
            source_url: https://example.test/gateway
      - canonical_id: google/gem-x
        aliases:
          - {provider: google, alias: gem-x, channel: google-api}
        prices:
          - channel: google-api
            region: global
            effective_from: "2026-01-01"
            input_per_mtok: 0.5
            output_per_mtok: 1.5
            source_url: https://example.test/google
      - canonical_id: fireworks/fw-x
        aliases:
          - {provider: fireworks, alias: fw-x, channel: fireworks-api}
          - {provider: fireworks, alias: shared-name, channel: fireworks-api}
        prices:
          - channel: fireworks-api
            region: global
            effective_from: "2026-01-01"
            input_per_mtok: 0.9
            output_per_mtok: 0.9
            source_url: https://example.test/fireworks
      - canonical_id: anthropic/claude-x
        aliases:
          - {provider: bedrock, alias: claude-x-bedrock, channel: aws-bedrock}
        prices:
          - channel: aws-bedrock
            region: global
            effective_from: "2026-01-01"
            input_per_mtok: 3.0
            output_per_mtok: 15.0
            source_url: https://example.test/bedrock
    """
)

_AT = datetime(2026, 3, 1, tzinfo=timezone.utc)


def _catalog(tmp_path):
    path = tmp_path / "prices.yaml"
    path.write_text(_SYNTHETIC_CATALOG)
    return load_catalog(path)


def test_price_direct_openai_channel(tmp_path):
    catalog = _catalog(tmp_path)
    priced = catalog.price(
        model="gpt-x", channel="openai-api", at=_AT,
        input_tokens=1000, output_tokens=500,
    )
    assert priced.status == "priced"
    assert priced.canonical_model == "openai/gpt-x"
    assert priced.price_id == "openai/gpt-x:openai-api:global:2026-01-01"
    # (1000 * 2.0 + 500 * 4.0) / 1e6, computed by core as Decimal.
    assert priced.cost_usd == Decimal("0.00400000")


def test_price_direct_google_channel(tmp_path):
    catalog = _catalog(tmp_path)
    priced = catalog.price(
        model="gem-x", channel="google-api", at=_AT,
        input_tokens=1000, output_tokens=1000,
    )
    assert priced.status == "priced"
    assert priced.canonical_model == "google/gem-x"
    # (1000 * 0.5 + 1000 * 1.5) / 1e6
    assert priced.cost_usd == Decimal("0.00200000")


def test_price_direct_fireworks_channel(tmp_path):
    catalog = _catalog(tmp_path)
    priced = catalog.price(
        model="fw-x", channel="fireworks-api", at=_AT,
        input_tokens=1000, output_tokens=1000,
    )
    assert priced.status == "priced"
    assert priced.canonical_model == "fireworks/fw-x"
    # (1000 * 0.9 + 1000 * 0.9) / 1e6
    assert priced.cost_usd == Decimal("0.00180000")


def test_price_channel_is_exact_never_a_fallback(tmp_path):
    catalog = _catalog(tmp_path)
    # gpt-x is priced on openai-api and (as openai/gpt-x) on the gateway, but
    # never on anthropic-api: it stays unpriced, not repriced off another
    # channel.
    priced = catalog.price(
        model="gpt-x", channel="anthropic-api", at=_AT,
        input_tokens=1000, output_tokens=500,
    )
    assert priced.status == "unpriced"
    assert priced.cost_usd is None
    assert priced.price_id is None
    assert priced.reasons


def test_price_unknown_model_is_explicitly_unpriced_never_zero(tmp_path):
    catalog = _catalog(tmp_path)
    priced = catalog.price(
        model="not-a-model", channel="openai-api", at=_AT,
        input_tokens=1000, output_tokens=500,
    )
    assert priced.status == "unpriced"
    assert priced.cost_usd is None
    assert priced.reasons


def test_price_selects_the_effective_dated_window(tmp_path):
    catalog = _catalog(tmp_path)
    later = datetime(2026, 7, 1, tzinfo=timezone.utc)
    priced = catalog.price(
        model="gpt-x", channel="openai-api", at=later,
        input_tokens=1000, output_tokens=500,
    )
    assert priced.status == "priced"
    # The open second window (1.0/2.0) wins over the closed first (2.0/4.0).
    assert priced.price_id == "openai/gpt-x:openai-api:global:2026-06-01"
    assert priced.cost_usd == Decimal("0.00200000")


def test_price_accepts_bare_date_and_iso_string_effective_times(tmp_path):
    catalog = _catalog(tmp_path)
    from_date = catalog.price(
        model="gpt-x", channel="openai-api", at=date(2026, 3, 1),
        input_tokens=1000, output_tokens=500,
    )
    from_iso = catalog.price(
        model="gpt-x", channel="openai-api", at="2026-03-01T00:00:00",
        input_tokens=1000, output_tokens=500,
    )
    assert from_date.price_id == "openai/gpt-x:openai-api:global:2026-01-01"
    assert from_iso.price_id == from_date.price_id
    assert from_iso.cost_usd == from_date.cost_usd == Decimal("0.00400000")


def test_price_missing_token_count_is_partial_not_zero(tmp_path):
    catalog = _catalog(tmp_path)
    priced = catalog.price(
        model="gpt-x", channel="openai-api", at=_AT,
        input_tokens=None, output_tokens=500,
    )
    assert priced.status == "partial"
    assert "missing_input_tokens" in priced.reasons
    # Output still priced by core; input contributes nothing knowable.
    assert priced.cost_usd == Decimal("0.00200000")


def test_canonical_model_id_resolves_provider_qualified_aliases(tmp_path):
    catalog = _catalog(tmp_path)
    assert catalog.canonical_model_id("openai", "gpt-x") == "openai/gpt-x"
    assert catalog.canonical_model_id("openai", "openai/gpt-x") == "openai/gpt-x"
    assert catalog.canonical_model_id("google", "gem-x") == "google/gem-x"
    assert catalog.canonical_model_id("fireworks", "fw-x") == "fireworks/fw-x"
    # An unknown model under a known provider stays raw.
    assert catalog.canonical_model_id("openai", "nope") == "nope"


def test_canonical_model_id_uses_provider_to_disambiguate_shared_names(tmp_path):
    catalog = _catalog(tmp_path)
    # "shared-name" is declared under both openai/gpt-x and fireworks/fw-x; the
    # captured provider selects the right canonical -- no guessing needed.
    assert catalog.canonical_model_id("openai", "shared-name") == "openai/gpt-x"
    assert catalog.canonical_model_id("fireworks", "shared-name") == "fireworks/fw-x"
    # An unknown provider cannot be disambiguated -> id returned unchanged.
    assert catalog.canonical_model_id("mystery", "shared-name") == "shared-name"
    # A non-string provider likewise leaves the id unchanged, never guessed.
    assert catalog.canonical_model_id(None, "shared-name") == "shared-name"


def test_canonical_model_id_normalizes_provider_aliases(tmp_path):
    catalog = _catalog(tmp_path)
    # google-genai / gemini normalize to google before the (provider, name) lookup.
    assert catalog.canonical_model_id("google-genai", "gem-x") == "google/gem-x"
    assert catalog.canonical_model_id("gemini", "gem-x") == "google/gem-x"
    # aws / amazon-bedrock normalize to bedrock.
    assert catalog.canonical_model_id("aws", "claude-x-bedrock") == "anthropic/claude-x"
    assert (
        catalog.canonical_model_id("amazon-bedrock", "claude-x-bedrock")
        == "anthropic/claude-x"
    )


def test_direct_channel_for_provider_normalizes_provider_aliases():
    # metergraph-core's provider-alias map applies before the channel lookup, so
    # names core pricing already accepts are not unknown to the direct helper.
    assert direct_channel_for_provider("aws") == "aws-bedrock"
    assert direct_channel_for_provider("amazon-bedrock") == "aws-bedrock"
    assert direct_channel_for_provider("aws-bedrock") == "aws-bedrock"
    assert direct_channel_for_provider("google-genai") == "google-api"
    assert direct_channel_for_provider("gemini") == "google-api"


def test_direct_channel_for_provider_maps_known_and_rejects_unknown():
    assert direct_channel_for_provider("openai") == "openai-api"
    assert direct_channel_for_provider("google") == "google-api"
    assert direct_channel_for_provider("fireworks") == "fireworks-api"
    assert direct_channel_for_provider("vertex-ai") == "google-vertex-ai"
    assert direct_channel_for_provider("bedrock") == "aws-bedrock"
    assert direct_channel_for_provider("anthropic") == "anthropic-api"
    assert direct_channel_for_provider("vercel") == "vercel-ai-gateway"
    # A provider with no direct channel defined -- caller keeps it unpriced.
    assert direct_channel_for_provider("mystery") is None
    assert direct_channel_for_provider(None) is None
    assert direct_channel_for_provider(123) is None


def test_installed_catalog_prices_openai_direct_end_to_end():
    # No path -> the metergraph-core bundled catalog. Structural assertions only
    # so shipped price numbers stay free to change.
    catalog = load_catalog()
    priced = catalog.price(
        model="gpt-5.6", channel="openai-api",
        at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        input_tokens=1000, output_tokens=500,
    )
    assert priced.status in ("priced", "partial")
    assert priced.canonical_model == "openai/gpt-5.6-sol"
    assert priced.price_id
    assert priced.cost_usd is not None and priced.cost_usd > 0
