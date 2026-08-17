"""Effective-dated model catalog and deterministic token-cost enrichment."""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping

_MILLION = Decimal("1000000")
_COST_QUANTUM = Decimal("0.00000001")
_PROVIDER_ALIASES = {
    "amazon-bedrock": "bedrock",
    "aws": "bedrock",
    "aws-bedrock": "bedrock",
    "gemini": "google",
    "google-genai": "google",
}


@dataclass(frozen=True, slots=True)
class Alias:
    model_id: str
    canonical_id: str
    pricing_channel: str
    rules: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Price:
    id: str
    model_id: str
    pricing_channel: str
    region: str
    input_per_mtok: Decimal | None
    output_per_mtok: Decimal | None
    cache_read_per_mtok: Decimal | None
    cache_write_5m_per_mtok: Decimal | None
    cache_write_1h_per_mtok: Decimal | None
    batch_input_per_mtok: Decimal | None
    batch_output_per_mtok: Decimal | None
    rules: Mapping[str, Any]
    effective_from: datetime
    effective_to: datetime | None


@dataclass(frozen=True, slots=True)
class CostResult:
    canonical_model: str | None
    price_id: str | None
    cost_usd: Decimal | None
    status: str
    reasons: tuple[str, ...] = ()


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() else None


def _tokens(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return result if result >= 0 else None


class CatalogSnapshot:
    def __init__(
        self,
        aliases: Mapping[tuple[str, str], Alias],
        prices: list[Price],
        *,
        region: str,
    ) -> None:
        self._aliases = dict(aliases)
        self._prices: dict[tuple[str, str], list[Price]] = {}
        self._region = region.strip().lower()
        for price in prices:
            self._prices.setdefault((price.model_id, price.pricing_channel), []).append(
                price
            )
        for candidates in self._prices.values():
            candidates.sort(key=lambda price: price.effective_from, reverse=True)

    def _price_for(self, alias: Alias, at: datetime) -> Price | None:
        at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
        candidates = self._prices.get((alias.model_id, alias.pricing_channel), [])
        for region in dict.fromkeys((self._region, "*", "global")):
            for price in candidates:
                if price.region.lower() != region:
                    continue
                if price.effective_from <= at and (
                    price.effective_to is None or at < price.effective_to
                ):
                    return price
        return None

    def cost(
        self,
        *,
        provider: Any,
        model: Any,
        at: datetime,
        input_tokens: Any,
        output_tokens: Any,
        cache_read_tokens: Any = None,
        cache_write_tokens: Any = None,
        batch: bool = False,
    ) -> CostResult:
        provider_key = str(provider or "").strip().lower()
        provider_key = _PROVIDER_ALIASES.get(provider_key, provider_key)
        model_key = str(model or "").strip().lower()
        alias = self._aliases.get((provider_key, model_key))
        if alias is None:
            return CostResult(None, None, None, "unpriced", ("unknown_model",))
        price = self._price_for(alias, at)
        if price is None:
            return CostResult(
                alias.canonical_id,
                None,
                None,
                "unpriced",
                ("no_effective_price",),
            )

        reasons: list[str] = []
        input_count = _tokens(input_tokens)
        output_count = _tokens(output_tokens)
        cache_read_count = _tokens(cache_read_tokens) or 0
        cache_write_count = _tokens(cache_write_tokens) or 0
        if input_count is None:
            reasons.append("missing_input_tokens")
            input_count = 0
        if output_count is None:
            reasons.append("missing_output_tokens")
            output_count = 0

        rules = {**price.rules, **alias.rules}
        billable_input = input_count
        deducted_input = 0
        if rules.get("input_includes_cache_read"):
            if cache_read_count > input_count:
                reasons.append("cache_read_exceeds_input")
                deducted_input = input_count
            else:
                deducted_input += cache_read_count
        if rules.get("input_includes_cache_write"):
            if cache_write_count > input_count - deducted_input:
                reasons.append("cache_write_exceeds_input")
                deducted_input = input_count
            else:
                deducted_input += cache_write_count
        billable_input -= deducted_input

        input_rate = price.input_per_mtok
        output_rate = price.output_per_mtok
        if batch:
            if (
                price.batch_input_per_mtok is None
                or price.batch_output_per_mtok is None
            ):
                reasons.append("batch_rate_unavailable")
            else:
                input_rate = price.batch_input_per_mtok
                output_rate = price.batch_output_per_mtok

        input_multiplier = Decimal("1")
        output_multiplier = Decimal("1")
        long_context = rules.get("long_context") or {}
        threshold = _tokens(long_context.get("threshold"))
        if threshold is not None and input_count > threshold:
            input_multiplier = _decimal(
                long_context.get("input_multiplier")
            ) or Decimal("1")
            output_multiplier = _decimal(
                long_context.get("output_multiplier")
            ) or Decimal("1")

        cost = Decimal("0")
        if input_rate is None:
            if billable_input:
                reasons.append("input_rate_unavailable")
        else:
            cost += Decimal(billable_input) * input_rate * input_multiplier / _MILLION
        if output_rate is None:
            if output_count:
                reasons.append("output_rate_unavailable")
        else:
            cost += Decimal(output_count) * output_rate * output_multiplier / _MILLION
        if cache_read_count:
            if price.cache_read_per_mtok is None:
                reasons.append("cache_read_rate_unavailable")
            else:
                cost += (
                    Decimal(cache_read_count)
                    * price.cache_read_per_mtok
                    * input_multiplier
                    / _MILLION
                )
        if cache_write_count:
            if price.cache_write_5m_per_mtok is None:
                reasons.append("cache_write_rate_unavailable")
            else:
                cost += (
                    Decimal(cache_write_count)
                    * price.cache_write_5m_per_mtok
                    * input_multiplier
                    / _MILLION
                )
        if rules.get("uncaptured_fees"):
            reasons.append("uncaptured_fees")

        return CostResult(
            alias.canonical_id,
            price.id,
            cost.quantize(_COST_QUANTUM, rounding=ROUND_HALF_UP),
            "partial" if reasons else "priced",
            tuple(dict.fromkeys(reasons)),
        )
