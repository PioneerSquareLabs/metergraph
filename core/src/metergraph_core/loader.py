"""Parse and load the bundled prices.yaml into a CatalogSnapshot."""

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import yaml

from .catalog import (
    Alias,
    CatalogSnapshot,
    CostResult,
    Price,
    _decimal,
    _normalize_provider,
)

DEFAULT_CATALOG_PATH = Path(__file__).parent / "data" / "prices.yaml"
_PROVIDER_SYNONYMS = {"bedrock": ("aws-bedrock", "aws")}


class CatalogError(ValueError):
    pass


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _canonical_index(document: dict[str, Any]) -> dict[tuple[str, str], str]:
    """Map every ``(provider, name)`` -- for each declared alias and, under the
    providers that publish it, the canonical id itself -- to its canonical id,
    dropping any pair that would resolve ambiguously. Qualifying by provider
    lets a capture path canonicalize a name two providers share (each to its own
    canonical) while still never guessing when the provider is unknown."""
    index: dict[tuple[str, str], str] = {}
    ambiguous: set[tuple[str, str]] = set()
    for entry in document.get("models", []):
        canonical = str(entry["canonical_id"])
        providers: set[str] = set()
        pairs: list[tuple[str, str]] = []
        for alias in entry.get("aliases") or []:
            provider = str(alias.get("provider") or "").strip().lower()
            if not provider:
                continue
            providers.add(provider)
            pairs.append((provider, str(alias["alias"])))
        for provider in providers:
            pairs.append((provider, canonical))
        for key in pairs:
            if index.get(key, canonical) != canonical:
                ambiguous.add(key)
            index[key] = canonical
    for key in ambiguous:
        index.pop(key, None)
    return index


@dataclass(frozen=True, slots=True)
class LoadedCatalog:
    version: str
    currency: str
    pricing_verified_at: date
    content_hash: str
    document: dict[str, Any]
    snapshot: CatalogSnapshot
    canonical_ids: Mapping[tuple[str, str], str]

    def canonical_model_id(self, provider: str, model_id: str) -> str:
        """Resolve a captured provider-native model id to its canonical id,
        using the known source ``provider`` to disambiguate names shared across
        providers. An unknown provider, unknown id, or non-string input is left
        unchanged so none is ever misattributed."""
        if not isinstance(model_id, str) or not isinstance(provider, str):
            return model_id
        return self.canonical_ids.get(
            (_normalize_provider(provider), model_id), model_id
        )

    def infer_direct_channel(self, model: Any) -> str | None:
        """Infer a direct pricing channel only when the catalog is unambiguous."""
        return self.snapshot.infer_direct_channel(model)

    def price(
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
        """Price an observed deployment channel-exactly. See
        :meth:`CatalogSnapshot.price_deployment`."""
        return self.snapshot.price_deployment(
            model=model,
            channel=channel,
            at=at,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            batch=batch,
        )


def _date(value: Any, *, field: str, model: str) -> datetime:
    if value is None:
        raise CatalogError(f"{model}: missing {field}")
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise CatalogError(f"{model}: invalid {field} {value!r}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _catalog_metadata(document: dict[str, Any]) -> tuple[str, date]:
    currency = str(document.get("currency") or "").strip().upper()
    if currency != "USD":
        raise CatalogError("prices document currency must be USD")
    try:
        pricing_verified_at = date.fromisoformat(
            str(document.get("pricing_verified_at") or "")
        )
    except ValueError as exc:
        raise CatalogError(
            "prices document must have an ISO pricing_verified_at date"
        ) from exc
    return currency, pricing_verified_at


def parse_catalog(
    document: Any,
) -> tuple[str, dict[tuple[str, str], Alias], list[Price]]:
    if not isinstance(document, dict) or not isinstance(document.get("models"), list):
        raise CatalogError("prices document must have a models list")
    version = str(document.get("version") or "")
    if not version:
        raise CatalogError("prices document must have a version")
    _catalog_metadata(document)
    aliases: dict[tuple[str, str], Alias] = {}
    prices: list[Price] = []
    for entry in document["models"]:
        canonical = str(entry.get("canonical_id") or "")
        if not canonical:
            raise CatalogError("model entry missing canonical_id")
        publisher = str(entry.get("publisher") or "").strip().lower() or None
        for alias in entry.get("aliases") or []:
            provider = str(alias.get("provider") or "").lower()
            name = str(alias.get("alias") or "").lower()
            channel = str(alias.get("channel") or "")
            if not provider or not name or not channel:
                raise CatalogError(f"{canonical}: alias needs provider/alias/channel")
            keys = [(provider, name)]
            for synonym in _PROVIDER_SYNONYMS.get(provider, ()):
                keys.append((synonym, name))
            for key in keys:
                if key in aliases:
                    raise CatalogError(f"{canonical}: duplicate alias {key}")
                aliases[key] = Alias(
                    model_id=canonical,
                    canonical_id=canonical,
                    pricing_channel=channel,
                    rules=_freeze(alias.get("rules") or {}),
                    publisher=publisher,
                )
        seen_windows: list[tuple[str, str, datetime, datetime | None]] = []
        for price in entry.get("prices") or []:
            channel = str(price.get("channel") or "")
            region = str(price.get("region") or "global")
            if not channel:
                raise CatalogError(f"{canonical}: price entry needs channel")
            if not str(price.get("source_url") or "").strip():
                raise CatalogError(f"{canonical}: price entry needs source_url")
            effective_from = _date(
                price.get("effective_from"), field="effective_from", model=canonical
            )
            effective_to = (
                _date(price.get("effective_to"), field="effective_to", model=canonical)
                if price.get("effective_to") is not None
                else None
            )
            if effective_to is not None and effective_to <= effective_from:
                raise CatalogError(f"{canonical}: effective_to before effective_from")
            for other_channel, other_region, other_from, other_to in seen_windows:
                if (other_channel, other_region) != (channel, region):
                    continue
                if (effective_to is None or other_from < effective_to) and (
                    other_to is None or effective_from < other_to
                ):
                    raise CatalogError(
                        f"{canonical}: overlapping {channel}/{region} price windows"
                    )
            seen_windows.append((channel, region, effective_from, effective_to))
            prices.append(
                Price(
                    id=f"{canonical}:{channel}:{region}:{effective_from.date()}",
                    model_id=canonical,
                    pricing_channel=channel,
                    region=region,
                    input_per_mtok=_decimal(price.get("input_per_mtok")),
                    output_per_mtok=_decimal(price.get("output_per_mtok")),
                    cache_read_per_mtok=_decimal(price.get("cache_read_per_mtok")),
                    cache_write_5m_per_mtok=_decimal(
                        price.get("cache_write_5m_per_mtok")
                    ),
                    cache_write_1h_per_mtok=_decimal(
                        price.get("cache_write_1h_per_mtok")
                    ),
                    batch_input_per_mtok=_decimal(price.get("batch_input_per_mtok")),
                    batch_output_per_mtok=_decimal(price.get("batch_output_per_mtok")),
                    rules=_freeze(price.get("rules") or {}),
                    effective_from=effective_from,
                    effective_to=effective_to,
                    source_url=str(price["source_url"]).strip(),
                )
            )
    return version, aliases, prices


def load_catalog(
    path: str | Path | None = None, *, region: str = "global"
) -> LoadedCatalog:
    resolved = Path(path) if path is not None else DEFAULT_CATALOG_PATH
    content = resolved.read_bytes()
    document = yaml.safe_load(content)
    version, aliases, prices = parse_catalog(document)
    currency, pricing_verified_at = _catalog_metadata(document)
    return LoadedCatalog(
        version=version,
        currency=currency,
        pricing_verified_at=pricing_verified_at,
        content_hash=hashlib.sha256(content).hexdigest(),
        document=document,
        snapshot=CatalogSnapshot(aliases, prices, region=region),
        canonical_ids=_canonical_index(document),
    )
