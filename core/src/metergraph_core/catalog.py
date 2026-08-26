"""Effective-dated model catalog and deterministic token-cost enrichment."""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from types import MappingProxyType
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

# The direct catalog channel each captured source provider was actually billed
# on. A captured/reference execution was served by the customer's own provider,
# so its price must come from that provider's direct channel -- never a gateway
# list price and never a guess. A source provider absent from this map yields no
# channel, and the caller must keep the execution explicitly unpriced.
_DIRECT_CHANNEL_BY_PROVIDER = {
    "anthropic": "anthropic-api",
    "openai": "openai-api",
    "google": "google-api",
    "vertex-ai": "google-vertex-ai",
    "fireworks": "fireworks-api",
    "bedrock": "aws-bedrock",
}


def _normalize_provider(provider: str) -> str:
    """Fold a provider spelling through metergraph-core's provider-alias map
    (e.g. ``aws``/``amazon-bedrock`` -> ``bedrock``, ``google-genai`` ->
    ``google``) so identity and channel lookups accept every spelling the
    pricing path already does."""
    key = provider.strip().lower()
    return _PROVIDER_ALIASES.get(key, key)


def direct_channel_for_provider(provider: Any) -> str | None:
    """The direct catalog channel for a recorded source provider, or ``None``
    when the provider has no direct channel defined -- callers must treat
    ``None`` as explicitly unpriced rather than falling back to another
    channel."""
    if not isinstance(provider, str):
        return None
    return _DIRECT_CHANNEL_BY_PROVIDER.get(_normalize_provider(provider))


def _coerce_datetime(at: Any) -> datetime:
    """Normalize an effective time to an aware UTC datetime. A bare ``date`` is
    anchored at UTC midnight; an ISO string is parsed. Prices resolve at a
    timestamp, so every entry point funnels through this."""
    if isinstance(at, datetime):
        return at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    if isinstance(at, date):
        return datetime(at.year, at.month, at.day, tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(at))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


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
    source_url: str


@dataclass(frozen=True, slots=True)
class CostResult:
    canonical_model: str | None
    price_id: str | None
    cost_usd: Decimal | None
    status: str
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResolvedPrice:
    """An effective price selected for a planned model deployment."""

    canonical_model: str
    price: Price
    rules: Mapping[str, Any]


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


def _price_tokens(
    price: Price,
    rules: Mapping[str, Any],
    *,
    input_tokens: Any,
    output_tokens: Any,
    cache_read_tokens: Any,
    cache_write_tokens: Any,
    batch: bool,
) -> tuple[Decimal, list[str]]:
    """Cost a token usage against one already-selected price and its merged
    rules. Shared by ``cost`` (provider+model entry) and ``price_deployment``
    (model+channel entry) so both compute identically."""
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
        if price.batch_input_per_mtok is None or price.batch_output_per_mtok is None:
            reasons.append("batch_rate_unavailable")
        else:
            input_rate = price.batch_input_per_mtok
            output_rate = price.batch_output_per_mtok

    input_multiplier = Decimal("1")
    output_multiplier = Decimal("1")
    long_context = rules.get("long_context") or {}
    threshold = _tokens(long_context.get("threshold"))
    if threshold is not None and input_count > threshold:
        input_multiplier = _decimal(long_context.get("input_multiplier")) or Decimal("1")
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

    return cost.quantize(_COST_QUANTUM, rounding=ROUND_HALF_UP), reasons


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
        self._deployment_aliases: dict[tuple[str, str], Alias] = {}
        self._region = region.strip().lower()
        for (_, observed_model), alias in aliases.items():
            channel = alias.pricing_channel.strip().lower()
            for model in (observed_model, alias.canonical_id):
                key = (model.strip().lower(), channel)
                existing = self._deployment_aliases.get(key)
                if existing is not None and (
                    existing.canonical_id != alias.canonical_id
                    or dict(existing.rules) != dict(alias.rules)
                ):
                    raise ValueError(
                        f"ambiguous deployment alias {model!r} for channel {channel!r}"
                    )
                self._deployment_aliases[key] = alias
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

    def resolve_price(
        self, *, model: Any, channel: Any, at: datetime
    ) -> ResolvedPrice | None:
        """Resolve pricing for an explicitly selected model and channel.

        This is intended for planners and evaluation pipelines that know the
        deployment channel before making a provider call. It never falls back
        to pricing from a different channel.
        """

        model_key = str(model or "").strip().lower()
        channel_key = str(channel or "").strip().lower()
        alias = self._deployment_aliases.get((model_key, channel_key))
        if alias is None:
            return None
        price = self._price_for(alias, at)
        if price is None:
            return None
        return ResolvedPrice(
            canonical_model=alias.canonical_id,
            price=price,
            rules=MappingProxyType({**price.rules, **alias.rules}),
        )

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

        cost, reasons = _price_tokens(
            price,
            {**price.rules, **alias.rules},
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            batch=batch,
        )
        return CostResult(
            alias.canonical_id,
            price.id,
            cost,
            "partial" if reasons else "priced",
            tuple(dict.fromkeys(reasons)),
        )

    def price_deployment(
        self,
        *,
        model: Any,
        channel: Any,
        at: Any,
        input_tokens: Any,
        output_tokens: Any,
        cache_read_tokens: Any = None,
        cache_write_tokens: Any = None,
        batch: bool = False,
    ) -> CostResult:
        """Price an observed deployment from the identity a caller already has:
        a model id, the pricing channel it was served on, the execution time,
        and its token usage.

        The channel is honored exactly -- a model priced on a channel the
        catalog does not carry for it comes back ``unpriced`` rather than being
        repriced off a different channel. The caller never has to rebuild a
        provider or alias index from the catalog document; resolution and cost
        both live here.
        """
        when = _coerce_datetime(at)
        resolved = self.resolve_price(model=model, channel=channel, at=when)
        if resolved is None:
            return CostResult(None, None, None, "unpriced", ("unknown_deployment",))
        cost, reasons = _price_tokens(
            resolved.price,
            resolved.rules,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            batch=batch,
        )
        return CostResult(
            resolved.canonical_model,
            resolved.price.id,
            cost,
            "partial" if reasons else "priced",
            tuple(dict.fromkeys(reasons)),
        )
