"""Effective-dated retrieval pricing: searches, tool calls, grounded queries.

Retrieval pricing sits alongside -- and deliberately apart from -- the
model-token catalog. A retrieval operation is billed per counted unit (a
completed web search, a tool call, an executed grounding query), never per
token, so it carries its own price shape and its own catalog object.

The contract mirrors the token path's explicit priced/partial/unpriced
semantics: a resolved operation with a valid unit count is ``priced`` (a $0
fee is a real priced zero), while a missing, non-integer, or negative unit
count and an unknown operation or channel come back ``unpriced`` with a reason
-- never silently priced at zero.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from .catalog import _COST_QUANTUM, _coerce_datetime

_THOUSAND = Decimal("1000")


@dataclass(frozen=True, slots=True)
class RetrievalPrice:
    """An effective-dated per-1,000-unit price for one channel/operation."""

    id: str
    channel: str
    operation: str
    region: str
    unit: str
    per_1k_usd: Decimal
    effective_from: datetime
    effective_to: datetime | None
    source_url: str


@dataclass(frozen=True, slots=True)
class RetrievalCostResult:
    """The outcome of pricing a retrieval operation.

    ``status`` reuses the token path's vocabulary (``priced``/``partial``/
    ``unpriced``); retrieval never produces ``partial`` because a unit count is
    all-or-nothing -- a bad count leaves nothing knowable to price.
    """

    price_id: str | None
    cost_usd: Decimal | None
    status: str
    reasons: tuple[str, ...] = ()


def _units(value: Any) -> tuple[int | None, str | None]:
    """Coerce a retrieval unit count under a strict-integer contract.

    Only a real, non-negative ``int`` is a valid count. ``bool`` (``type`` is
    ``bool``, not ``int``), ``float``, strings, and ``Decimal`` are rejected as
    ``invalid_units`` rather than truncated or parsed; ``None`` is
    ``missing_units`` and a negative int is ``negative_units``. Each rejection
    keeps the operation explicitly unpriced instead of turning it into zero.
    """
    if value is None:
        return None, "missing_units"
    if type(value) is not int:
        return None, "invalid_units"
    if value < 0:
        return None, "negative_units"
    return value, None


class RetrievalCatalog:
    """Resolve and cost retrieval operations by ``(channel, operation)``.

    Channel selection is exact and region resolution falls back
    ``region -> "*" -> "global"``, mirroring the token snapshot: an operation a
    channel does not carry stays unpriced rather than being repriced off another
    channel or region.
    """

    def __init__(self, prices: list[RetrievalPrice]) -> None:
        self._by_key: dict[tuple[str, str], list[RetrievalPrice]] = {}
        self._channels: set[str] = set()
        for price in prices:
            self._channels.add(price.channel)
            self._by_key.setdefault((price.channel, price.operation), []).append(price)
        for candidates in self._by_key.values():
            candidates.sort(key=lambda price: price.effective_from, reverse=True)

    def _select(
        self, channel: str, operation: str, region: str, at: datetime
    ) -> RetrievalPrice | None:
        at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
        candidates = self._by_key.get((channel, operation), [])
        for candidate_region in dict.fromkeys((region, "*", "global")):
            for price in candidates:
                if price.region.lower() != candidate_region:
                    continue
                if price.effective_from <= at and (
                    price.effective_to is None or at < price.effective_to
                ):
                    return price
        return None

    def price(
        self,
        *,
        channel: Any,
        operation: Any,
        units: Any,
        at: Any,
        region: Any = "global",
    ) -> RetrievalCostResult:
        """Price ``units`` of ``operation`` on ``channel`` at time ``at``.

        Resolution happens before unit validation: an unknown channel or
        operation is reported as such regardless of the unit count, because an
        operation that cannot be identified cannot be priced at all.
        """
        channel_key = str(channel or "").strip().lower()
        operation_key = str(operation or "").strip().lower()
        region_key = str(region or "global").strip().lower() or "global"

        if channel_key not in self._channels:
            return RetrievalCostResult(None, None, "unpriced", ("unknown_channel",))
        if (channel_key, operation_key) not in self._by_key:
            return RetrievalCostResult(None, None, "unpriced", ("unknown_operation",))

        price = self._select(channel_key, operation_key, region_key, _coerce_datetime(at))
        if price is None:
            return RetrievalCostResult(None, None, "unpriced", ("no_effective_price",))

        count, reason = _units(units)
        if reason is not None:
            return RetrievalCostResult(None, None, "unpriced", (reason,))

        cost = (Decimal(count) * price.per_1k_usd / _THOUSAND).quantize(
            _COST_QUANTUM, rounding=ROUND_HALF_UP
        )
        return RetrievalCostResult(price.id, cost, "priced", ())
