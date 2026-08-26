from decimal import Decimal

import pytest

from metergraph_core import (
    CostResult,
    normalize_gateway_evidence,
    resolve_billing,
)


CATALOG_PRICED = CostResult(
    canonical_model="anthropic/claude-sonnet-4.6",
    price_id="catalog-price-1",
    cost_usd=Decimal("0.006"),
    status="priced",
)
CATALOG_UNPRICED = CostResult(
    canonical_model=None,
    price_id=None,
    cost_usd=None,
    status="unpriced",
    reasons=("model_not_found",),
)


def _openrouter_row(**overrides):
    row = {
        "gateway": "openrouter",
        "endpoint": "chat.completions",
        "reported_cost_usd": "0.00482",
        "reported_cost_source": "openrouter.usage.cost",
        "reported_upstream_cost_usd": "0.00410",
        "reported_upstream_cost_source": (
            "openrouter.usage.cost_details.upstream_inference_cost"
        ),
    }
    row.update(overrides)
    return row


def test_reported_openrouter_cost_wins_and_catalog_is_retained():
    decision = resolve_billing(
        CATALOG_PRICED,
        normalize_gateway_evidence(_openrouter_row()),
    )

    assert decision.cost_usd == Decimal("0.00482")
    assert decision.cost_status == "priced"
    assert decision.cost_provenance == "gateway_reported"
    assert decision.reported_cost_usd == Decimal("0.00482")
    assert decision.reported_upstream_cost_usd == Decimal("0.00410")
    assert decision.catalog_cost_usd == Decimal("0.006")
    assert decision.catalog_price_id == "catalog-price-1"
    assert decision.cost_discrepancy_status is None


def test_reported_cost_prices_a_call_without_catalog_support():
    decision = resolve_billing(
        CATALOG_UNPRICED,
        normalize_gateway_evidence(_openrouter_row()),
    )

    assert decision.cost_usd == Decimal("0.00482")
    assert decision.cost_status == "priced"
    assert decision.cost_provenance == "gateway_reported"
    assert decision.catalog_cost_usd is None


def test_catalog_cost_is_used_without_qualified_reported_cost():
    decision = resolve_billing(
        CATALOG_PRICED,
        normalize_gateway_evidence({}),
    )

    assert decision.cost_usd == Decimal("0.006")
    assert decision.cost_status == "priced"
    assert decision.cost_provenance == "catalog"
    assert decision.reported_cost_usd is None


def test_call_is_unpriced_when_neither_source_can_price_it():
    decision = resolve_billing(
        CATALOG_UNPRICED,
        normalize_gateway_evidence({}),
    )

    assert decision.cost_usd is None
    assert decision.cost_status == "unpriced"
    assert decision.cost_provenance == "none"
    assert decision.catalog_reasons == ("model_not_found",)


def test_zero_reported_cost_is_a_valid_gateway_charge():
    decision = resolve_billing(
        CATALOG_PRICED,
        normalize_gateway_evidence(_openrouter_row(reported_cost_usd=0)),
    )

    assert decision.cost_usd == Decimal("0")
    assert decision.cost_provenance == "gateway_reported"


@pytest.mark.parametrize(
    "value",
    [True, False, -1, "-0.1", "not-a-number", float("nan"), float("inf")],
)
def test_invalid_reported_cost_falls_back_to_catalog(value):
    decision = resolve_billing(
        CATALOG_PRICED,
        normalize_gateway_evidence(_openrouter_row(reported_cost_usd=value)),
    )

    assert decision.cost_usd == Decimal("0.006")
    assert decision.cost_provenance == "catalog"
    assert decision.reported_cost_usd is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"gateway": "openrouter-lookalike"},
        {"endpoint": "responses"},
        {"reported_cost_source": "custom.usage.cost"},
    ],
)
def test_unqualified_reported_cost_falls_back_to_catalog(overrides):
    decision = resolve_billing(
        CATALOG_PRICED,
        normalize_gateway_evidence(_openrouter_row(**overrides)),
    )

    assert decision.cost_usd == Decimal("0.006")
    assert decision.cost_provenance == "catalog"


def test_upstream_cost_is_retained_but_never_added_to_effective_cost():
    decision = resolve_billing(
        CATALOG_UNPRICED,
        normalize_gateway_evidence(_openrouter_row()),
    )

    assert decision.cost_usd == Decimal("0.00482")
    assert decision.reported_upstream_cost_usd == Decimal("0.00410")


def test_normalized_evidence_is_immutable():
    evidence = normalize_gateway_evidence(_openrouter_row())

    with pytest.raises(AttributeError):
        evidence.gateway = "other"
