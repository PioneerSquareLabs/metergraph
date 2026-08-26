"""Pure selection of effective LLM cost from catalog and gateway evidence."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from .catalog import CostResult

_OPENROUTER = "openrouter"
_CHAT_COMPLETIONS = "chat.completions"
_OPENROUTER_COST_SOURCE = "openrouter.usage.cost"
_OPENROUTER_UPSTREAM_COST_SOURCE = (
    "openrouter.usage.cost_details.upstream_inference_cost"
)


@dataclass(frozen=True, slots=True)
class GatewayBillingEvidence:
    """Validated gateway billing fields from one content-blind call row."""

    gateway: str | None
    endpoint: str | None
    reported_cost_usd: Decimal | None
    reported_cost_source: str | None
    reported_upstream_cost_usd: Decimal | None
    reported_upstream_cost_source: str | None


@dataclass(frozen=True, slots=True)
class BillingDecision:
    """Effective cost plus the independent evidence used to select it."""

    cost_usd: Decimal | None
    cost_status: str
    cost_provenance: str
    reported_cost_usd: Decimal | None
    reported_upstream_cost_usd: Decimal | None
    catalog_cost_usd: Decimal | None
    catalog_price_id: str | None
    catalog_reasons: tuple[str, ...]
    cost_discrepancy_status: str | None = None


def _text(value: Any, *, limit: int = 128) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped[:limit] if stripped else None


def _non_negative_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not result.is_finite() or result < 0:
        return None
    return result


def normalize_gateway_evidence(row: Mapping[str, Any]) -> GatewayBillingEvidence:
    """Allowlist and validate gateway billing evidence from a call mapping."""

    gateway = _text(row.get("gateway"), limit=64)
    if gateway is not None:
        gateway = gateway.lower()
    endpoint = _text(row.get("endpoint"), limit=64)
    reported_source = _text(row.get("reported_cost_source"))
    upstream_source = _text(row.get("reported_upstream_cost_source"))

    return GatewayBillingEvidence(
        gateway=gateway,
        endpoint=endpoint,
        reported_cost_usd=_non_negative_decimal(row.get("reported_cost_usd")),
        reported_cost_source=reported_source,
        reported_upstream_cost_usd=_non_negative_decimal(
            row.get("reported_upstream_cost_usd")
        ),
        reported_upstream_cost_source=upstream_source,
    )


def _is_openrouter_chat_completions(evidence: GatewayBillingEvidence) -> bool:
    return (
        evidence.gateway == _OPENROUTER
        and evidence.endpoint == _CHAT_COMPLETIONS
    )


def resolve_billing(
    catalog_result: CostResult,
    evidence: GatewayBillingEvidence,
) -> BillingDecision:
    """Select effective cost without combining independent reported amounts."""

    qualified_openrouter = _is_openrouter_chat_completions(evidence)
    reported_cost = (
        evidence.reported_cost_usd
        if qualified_openrouter
        and evidence.reported_cost_source == _OPENROUTER_COST_SOURCE
        else None
    )
    upstream_cost = (
        evidence.reported_upstream_cost_usd
        if qualified_openrouter
        and evidence.reported_upstream_cost_source
        == _OPENROUTER_UPSTREAM_COST_SOURCE
        else None
    )

    if reported_cost is not None:
        cost_usd = reported_cost
        cost_status = "priced"
        provenance = "gateway_reported"
    elif catalog_result.cost_usd is not None:
        cost_usd = catalog_result.cost_usd
        cost_status = catalog_result.status
        provenance = "catalog"
    else:
        cost_usd = None
        cost_status = catalog_result.status
        provenance = "none"

    return BillingDecision(
        cost_usd=cost_usd,
        cost_status=cost_status,
        cost_provenance=provenance,
        reported_cost_usd=reported_cost,
        reported_upstream_cost_usd=upstream_cost,
        catalog_cost_usd=catalog_result.cost_usd,
        catalog_price_id=catalog_result.price_id,
        catalog_reasons=catalog_result.reasons,
    )
