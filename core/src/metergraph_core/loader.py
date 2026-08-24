"""Parse and load the bundled prices.yaml into a CatalogSnapshot."""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .catalog import Alias, CatalogSnapshot, Price, _decimal

DEFAULT_CATALOG_PATH = Path(__file__).parent / "data" / "prices.yaml"
_PROVIDER_SYNONYMS = {"bedrock": ("aws-bedrock", "aws")}


class CatalogError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class LoadedCatalog:
    version: str
    content_hash: str
    document: dict[str, Any]
    snapshot: CatalogSnapshot


def _date(value: Any, *, field: str, model: str) -> datetime:
    if value is None:
        raise CatalogError(f"{model}: missing {field}")
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise CatalogError(f"{model}: invalid {field} {value!r}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_catalog(
    document: Any,
) -> tuple[str, dict[tuple[str, str], Alias], list[Price]]:
    if not isinstance(document, dict) or not isinstance(document.get("models"), list):
        raise CatalogError("prices document must have a models list")
    version = str(document.get("version") or "")
    if not version:
        raise CatalogError("prices document must have a version")
    aliases: dict[tuple[str, str], Alias] = {}
    prices: list[Price] = []
    for entry in document["models"]:
        canonical = str(entry.get("canonical_id") or "")
        if not canonical:
            raise CatalogError("model entry missing canonical_id")
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
                    rules=alias.get("rules") or {},
                )
        seen_windows: list[tuple[str, str, datetime, datetime | None]] = []
        for price in entry.get("prices") or []:
            channel = str(price.get("channel") or "")
            region = str(price.get("region") or "global")
            if not channel:
                raise CatalogError(f"{canonical}: price entry needs channel")
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
                    rules=price.get("rules") or {},
                    effective_from=effective_from,
                    effective_to=effective_to,
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
    return LoadedCatalog(
        version=version,
        content_hash=hashlib.sha256(content).hexdigest(),
        document=document,
        snapshot=CatalogSnapshot(aliases, prices, region=region),
    )
