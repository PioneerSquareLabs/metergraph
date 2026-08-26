"""Effective-dated retrieval pricing (searches, tool calls, grounded queries).

metergraph-core prices retrieval operations from the identity a caller already
has -- a provider channel, an operation name, the count of units performed, the
execution time, and an optional region -- and returns a price id, a USD cost,
and the same explicit priced/partial/unpriced semantics the token path uses.

Retrieval pricing is deliberately separate from model-token pricing: units are
counted operations (completed searches, tool calls, grounded queries), never
tokens. A missing, non-integer, or negative unit count and an unknown operation
or channel are rejected as explicitly unpriced -- never silently priced at zero.

The synthetic catalog below pins the edge-case contract against deterministic
numbers; the four shipped operations are asserted against the bundled catalog
because their rates are fixed requirements.
"""

import textwrap
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

import metergraph_core
from metergraph_core import (
    RetrievalCatalog,
    RetrievalCostResult,
    RetrievalPrice,
    load_catalog,
)


_SYNTHETIC_CATALOG = textwrap.dedent(
    """
    version: "test-retrieval-1"
    currency: USD
    pricing_verified_at: "2026-08-01"
    models: []
    retrieval:
      - channel: anthropic-api
        operation: web_search
        region: global
        unit: completed_search
        per_1k_usd: 8.00
        effective_from: "2026-01-01"
        effective_to: "2026-06-01"
        source_url: https://example.test/anthropic-search-old
      - channel: anthropic-api
        operation: web_search
        region: global
        unit: completed_search
        per_1k_usd: 10.00
        effective_from: "2026-06-01"
        source_url: https://example.test/anthropic-search-new
      - channel: anthropic-api
        operation: web_fetch
        region: global
        unit: tool_call
        per_1k_usd: 0.00
        effective_from: "2026-01-01"
        source_url: https://example.test/anthropic-fetch
      - channel: openai-api
        operation: web_search
        region: global
        unit: tool_call
        per_1k_usd: 10.00
        effective_from: "2026-01-01"
        source_url: https://example.test/openai-search
      - channel: openai-api
        operation: web_search
        region: eu
        unit: tool_call
        per_1k_usd: 12.00
        effective_from: "2026-01-01"
        source_url: https://example.test/openai-search-eu
      - channel: google-api
        operation: google_search_grounding
        region: global
        unit: executed_query
        per_1k_usd: 14.00
        effective_from: "2026-01-01"
        source_url: https://example.test/google-grounding
    """
)

_AT = datetime(2026, 8, 26, tzinfo=timezone.utc)


def _catalog(tmp_path):
    path = tmp_path / "prices.yaml"
    path.write_text(_SYNTHETIC_CATALOG)
    return load_catalog(path)


# --- Each shipped operation, against the bundled catalog (fixed rates) --------


def test_prices_anthropic_web_search_ten_dollars_per_thousand():
    catalog = load_catalog()
    priced = catalog.price_retrieval(
        channel="anthropic-api", operation="web_search", units=1000, at=_AT
    )
    assert priced.status == "priced"
    # $10 per 1,000 completed searches.
    assert priced.cost_usd == Decimal("10.00000000")
    assert priced.price_id == "anthropic-api:web_search:global:2026-08-26"


def test_prices_anthropic_web_fetch_as_a_priced_zero_fee():
    catalog = load_catalog()
    priced = catalog.price_retrieval(
        channel="anthropic-api", operation="web_fetch", units=1000, at=_AT
    )
    # A $0 direct tool fee is an explicit priced zero, not an unpriced miss.
    assert priced.status == "priced"
    assert priced.cost_usd == Decimal("0.00000000")
    assert priced.price_id == "anthropic-api:web_fetch:global:2026-08-26"


def test_prices_openai_web_search_ten_dollars_per_thousand():
    catalog = load_catalog()
    priced = catalog.price_retrieval(
        channel="openai-api", operation="web_search", units=1000, at=_AT
    )
    assert priced.status == "priced"
    # $10 per 1,000 tool calls.
    assert priced.cost_usd == Decimal("10.00000000")
    assert priced.price_id == "openai-api:web_search:global:2026-08-26"


def test_prices_google_search_grounding_fourteen_dollars_per_thousand():
    catalog = load_catalog()
    priced = catalog.price_retrieval(
        channel="google-api",
        operation="google_search_grounding",
        units=1000,
        at=_AT,
    )
    assert priced.status == "priced"
    # $14 per 1,000 executed queries.
    assert priced.cost_usd == Decimal("14.00000000")
    assert priced.price_id == "google-api:google_search_grounding:global:2026-08-26"


def test_cost_scales_linearly_with_unit_count(tmp_path):
    catalog = _catalog(tmp_path)
    priced = catalog.price_retrieval(
        channel="openai-api", operation="web_search", units=2500, at=_AT
    )
    assert priced.status == "priced"
    # 2500 * 10.00 / 1000
    assert priced.cost_usd == Decimal("25.00000000")


def test_zero_units_is_a_priced_zero(tmp_path):
    catalog = _catalog(tmp_path)
    priced = catalog.price_retrieval(
        channel="openai-api", operation="web_search", units=0, at=_AT
    )
    assert priced.status == "priced"
    assert priced.cost_usd == Decimal("0.00000000")
    assert priced.price_id


# --- Date resolution ----------------------------------------------------------


def test_selects_the_effective_dated_window(tmp_path):
    catalog = _catalog(tmp_path)
    early = catalog.price_retrieval(
        channel="anthropic-api",
        operation="web_search",
        units=1000,
        at=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )
    late = catalog.price_retrieval(
        channel="anthropic-api",
        operation="web_search",
        units=1000,
        at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    assert early.price_id == "anthropic-api:web_search:global:2026-01-01"
    assert early.cost_usd == Decimal("8.00000000")
    assert late.price_id == "anthropic-api:web_search:global:2026-06-01"
    assert late.cost_usd == Decimal("10.00000000")


def test_accepts_bare_date_and_iso_string_effective_times(tmp_path):
    catalog = _catalog(tmp_path)
    from_date = catalog.price_retrieval(
        channel="anthropic-api",
        operation="web_search",
        units=1000,
        at=date(2026, 3, 1),
    )
    from_iso = catalog.price_retrieval(
        channel="anthropic-api",
        operation="web_search",
        units=1000,
        at="2026-03-01T00:00:00",
    )
    assert from_date.price_id == "anthropic-api:web_search:global:2026-01-01"
    assert from_iso.price_id == from_date.price_id
    assert from_iso.cost_usd == from_date.cost_usd == Decimal("8.00000000")


def test_before_first_effective_window_is_unpriced(tmp_path):
    catalog = _catalog(tmp_path)
    priced = catalog.price_retrieval(
        channel="anthropic-api",
        operation="web_search",
        units=1000,
        at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    assert priced.status == "unpriced"
    assert priced.cost_usd is None
    assert "no_effective_price" in priced.reasons


# --- Region resolution --------------------------------------------------------


def test_region_specific_price_wins_and_falls_back_to_global(tmp_path):
    catalog = _catalog(tmp_path)
    eu = catalog.price_retrieval(
        channel="openai-api", operation="web_search", units=1000, at=_AT, region="eu"
    )
    glob = catalog.price_retrieval(
        channel="openai-api",
        operation="web_search",
        units=1000,
        at=_AT,
        region="global",
    )
    apac = catalog.price_retrieval(
        channel="openai-api", operation="web_search", units=1000, at=_AT, region="apac"
    )
    assert eu.cost_usd == Decimal("12.00000000")
    assert glob.cost_usd == Decimal("10.00000000")
    # An unknown region falls back to the global price, never unpriced.
    assert apac.cost_usd == Decimal("10.00000000")


# --- Rejected inputs: never silently zero -------------------------------------


def test_missing_units_is_unpriced_never_zero(tmp_path):
    catalog = _catalog(tmp_path)
    priced = catalog.price_retrieval(
        channel="anthropic-api", operation="web_search", units=None, at=_AT
    )
    assert priced.status == "unpriced"
    assert priced.cost_usd is None
    assert priced.price_id is None
    assert "missing_units" in priced.reasons


@pytest.mark.parametrize("bad_units", [True, False, 1.5, "1000", Decimal("5")])
def test_non_integer_units_is_unpriced_never_zero(tmp_path, bad_units):
    catalog = _catalog(tmp_path)
    priced = catalog.price_retrieval(
        channel="anthropic-api", operation="web_search", units=bad_units, at=_AT
    )
    assert priced.status == "unpriced"
    assert priced.cost_usd is None
    assert "invalid_units" in priced.reasons


def test_negative_units_is_unpriced_never_zero(tmp_path):
    catalog = _catalog(tmp_path)
    priced = catalog.price_retrieval(
        channel="anthropic-api", operation="web_search", units=-5, at=_AT
    )
    assert priced.status == "unpriced"
    assert priced.cost_usd is None
    assert "negative_units" in priced.reasons


def test_unknown_operation_is_unpriced_never_zero(tmp_path):
    catalog = _catalog(tmp_path)
    priced = catalog.price_retrieval(
        channel="anthropic-api", operation="image_generation", units=1000, at=_AT
    )
    assert priced.status == "unpriced"
    assert priced.cost_usd is None
    assert priced.price_id is None
    assert "unknown_operation" in priced.reasons


def test_unknown_channel_is_unpriced_never_zero(tmp_path):
    catalog = _catalog(tmp_path)
    priced = catalog.price_retrieval(
        channel="mystery-api", operation="web_search", units=1000, at=_AT
    )
    assert priced.status == "unpriced"
    assert priced.cost_usd is None
    assert "unknown_channel" in priced.reasons


# --- Public-package export ----------------------------------------------------


def test_retrieval_symbols_are_public_package_exports():
    for name in ("RetrievalCatalog", "RetrievalCostResult", "RetrievalPrice"):
        assert name in metergraph_core.__all__
        assert getattr(metergraph_core, name) is not None
    assert isinstance(RetrievalCostResult(None, None, "unpriced", ()), RetrievalCostResult)
    assert RetrievalPrice is not None
    assert RetrievalCatalog is not None
