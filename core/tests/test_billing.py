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


def _portkey_row(**overrides):
    row = {
        "gateway": "portkey",
        "endpoint": "responses",
        "reported_cost_usd": "0.01807375",
        "reported_cost_source": "portkey.cost",
    }
    row.update(overrides)
    return {key: value for key, value in row.items() if value is not None}


def test_a_verified_gateway_amount_is_what_the_call_cost():
    """The gateway's figure is what the customer was billed, including the
    per-request and per-query charges no token rate can express. Where it has
    been reconciled against the providers' published rates, it outranks a cost
    computed from tokens alone."""
    decision = resolve_billing(
        CATALOG_PRICED, normalize_gateway_evidence(_portkey_row())
    )

    assert decision.cost_usd == Decimal("0.01807375")
    assert decision.cost_status == "priced"
    assert decision.cost_provenance == "gateway_reported"
    # The computed cost is still recorded, so the two remain comparable.
    assert decision.catalog_cost_usd == Decimal("0.006")


def test_a_verified_gateway_prices_a_model_the_catalog_cannot():
    decision = resolve_billing(
        CATALOG_UNPRICED, normalize_gateway_evidence(_portkey_row())
    )

    assert decision.cost_usd == Decimal("0.01807375")
    assert decision.cost_status == "priced"


@pytest.mark.parametrize("endpoint", ["chat.completions", "responses", "messages"])
def test_each_endpoint_a_gateway_is_verified_on_qualifies(endpoint):
    decision = resolve_billing(
        CATALOG_PRICED, normalize_gateway_evidence(_portkey_row(endpoint=endpoint))
    )

    assert decision.cost_provenance == "gateway_reported"


@pytest.mark.parametrize(
    "overrides",
    [
        # An endpoint the gateway was not checked on prices differently.
        {"endpoint": "embeddings"},
        {"endpoint": None},
        # A gateway name alone is not enough: the source names the field read.
        {"reported_cost_source": "portkey.estimated_cost"},
        {"reported_cost_source": None},
        # Another gateway's source string does not transfer.
        {"reported_cost_source": "openrouter.usage.cost"},
        {"gateway": "langfuse"},
        {"gateway": None},
    ],
)
def test_evidence_that_does_not_line_up_leaves_the_catalog_in_charge(overrides):
    decision = resolve_billing(
        CATALOG_PRICED, normalize_gateway_evidence(_portkey_row(**overrides))
    )

    assert decision.cost_usd == Decimal("0.006")
    assert decision.cost_provenance == "catalog"
    assert decision.reported_cost_usd is None


def test_an_upstream_amount_is_only_taken_from_a_gateway_that_reports_one():
    """Portkey publishes no upstream figure, so one appearing on a Portkey row
    did not come from the gateway and is not recorded as though it had."""
    decision = resolve_billing(
        CATALOG_PRICED,
        normalize_gateway_evidence(
            _portkey_row(
                reported_upstream_cost_usd="0.99",
                reported_upstream_cost_source=(
                    "openrouter.usage.cost_details.upstream_inference_cost"
                ),
            )
        ),
    )

    assert decision.reported_upstream_cost_usd is None
    assert decision.cost_usd == Decimal("0.01807375")


def test_every_entry_names_a_gateway_endpoint_and_source():
    """An entry missing any of the three would qualify rows it was never checked
    against."""
    from metergraph_core.billing import _QUALIFIED_SOURCES

    for source in _QUALIFIED_SOURCES:
        assert source.gateway and source.endpoints and source.cost_source
        assert source.cost_source.startswith(f"{source.gateway}.")


# --- Disputed gateways ------------------------------------------------------

from datetime import datetime, timezone

from metergraph_core import Dispute

_DEEPSEEK_DISPUTE = Dispute(
    gateway="portkey",
    publisher="deepseek",
    since=datetime(2026, 9, 10, tzinfo=timezone.utc),
    note="Portkey's DeepSeek table predates the 2026-09-10 change.",
    source_url="https://api-docs.deepseek.com/updates/",
)
_DEEPSEEK_PRICED = CostResult(
    canonical_model="deepseek/v4-flash",
    price_id="deepseek/v4-flash:deepseek-api:global:2026-09-10",
    cost_usd=Decimal("0.0030"),
    status="priced",
    publisher="deepseek",
)


def _portkey_deepseek_row(**overrides):
    row = {
        "gateway": "portkey",
        "endpoint": "chat.completions",
        "reported_cost_usd": "0.0012",
        "reported_cost_source": "portkey.cost",
    }
    row.update(overrides)
    return row


def test_a_disputed_gateway_amount_is_declined_for_the_catalog_price():
    decision = resolve_billing(
        _DEEPSEEK_PRICED,
        normalize_gateway_evidence(_portkey_deepseek_row()),
        disputes=[_DEEPSEEK_DISPUTE],
        at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    assert decision.cost_usd == Decimal("0.0030")
    assert decision.cost_provenance == "catalog"
    assert decision.cost_status == "priced"
    # The gateway's figure stays as evidence, and the decline is recorded.
    assert decision.reported_cost_usd == Decimal("0.0012")
    assert "gateway_disputed" in decision.catalog_reasons


def test_a_dispute_applies_from_its_date_and_to_its_publisher_only():
    before = resolve_billing(
        _DEEPSEEK_PRICED,
        normalize_gateway_evidence(_portkey_deepseek_row()),
        disputes=[_DEEPSEEK_DISPUTE],
        at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    assert before.cost_provenance == "gateway_reported"
    assert "gateway_disputed" not in before.catalog_reasons

    other_publisher = CostResult(
        canonical_model="anthropic/claude-sonnet-4.6",
        price_id="p",
        cost_usd=Decimal("0.0060"),
        status="priced",
        publisher="anthropic",
    )
    elsewhere = resolve_billing(
        other_publisher,
        normalize_gateway_evidence(_portkey_deepseek_row()),
        disputes=[_DEEPSEEK_DISPUTE],
        at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    assert elsewhere.cost_provenance == "gateway_reported"

    other_gateway = resolve_billing(
        _DEEPSEEK_PRICED,
        normalize_gateway_evidence(_openrouter_row()),
        disputes=[_DEEPSEEK_DISPUTE],
        at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    assert other_gateway.cost_provenance == "gateway_reported"


def test_without_the_call_time_nothing_is_disputed():
    decision = resolve_billing(
        _DEEPSEEK_PRICED,
        normalize_gateway_evidence(_portkey_deepseek_row()),
        disputes=[_DEEPSEEK_DISPUTE],
    )
    assert decision.cost_usd == Decimal("0.0012")
    assert decision.cost_provenance == "gateway_reported"
    assert "gateway_disputed" not in decision.catalog_reasons


def test_a_disputed_amount_still_stands_when_the_catalog_cannot_price():
    unpriced = CostResult(None, None, None, "unpriced", ("unknown_model",), publisher=None)
    decision = resolve_billing(
        unpriced,
        normalize_gateway_evidence(_portkey_deepseek_row()),
        disputes=[_DEEPSEEK_DISPUTE],
        at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    # No publisher is known, so nothing is disputed and the figure stands.
    assert decision.cost_usd == Decimal("0.0012")
    assert decision.cost_provenance == "gateway_reported"


def test_the_shipped_catalog_disputes_portkey_for_deepseek():
    from metergraph_core import load_catalog

    disputes = load_catalog().disputes
    assert [(d.gateway, d.publisher, d.since.date().isoformat()) for d in disputes] == [
        ("portkey", "deepseek", "2026-09-10")
    ]
    assert disputes[0].note and disputes[0].source_url
