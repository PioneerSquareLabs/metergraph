"""The public pricing source registry.

Each entry names one official pricing source: the provider and catalog
channel it prices, its URL, the parser that reads it, the shape and clarity
of the page, the pricing dimensions it represents, whether automated
low-risk refreshes may use it and within which bounds, and what evidence
from it may be kept. The registry is reviewed data: a source becomes
low-risk only through a reviewed change to this file.

The registry never fetches anything. Refresh scripts fetch only the URLs it
lists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

import yaml

from .catalog_delta import RATE_FIELDS
from .loader import LoadedCatalog

SOURCE_REGISTRY_SCHEMA_VERSION = 1
DEFAULT_REGISTRY_PATH = Path(__file__).parent / "data" / "sources.yaml"

# Parser id -> whether its output can become catalog price rows. A parser
# that emits no rows produces discovery evidence only.
PARSER_EMITS_ROWS: Mapping[str, bool] = {
    "openai": True,
    "anthropic": True,
    "google": True,
    "deepseek": True,
    "mistral": True,
    "xai": True,
    "perplexity": True,
    "fireworks": False,
    "zai": False,
    "aws-bedrock": False,
    "azure-foundry": False,
    "nvidia": False,
}
TOKEN_DIMENSIONS = RATE_FIELDS
NON_TOKEN_DIMENSIONS = ("gpu_hour_usd", "license_per_gpu_year_usd")
SOURCE_SHAPES = (
    "token_table",
    "token_embedded_data",
    "token_model_card",
    "token_option_dependent",
    "gpu_hour_or_license",
)
CLARITIES = ("structured", "semi_structured", "prose", "option_dependent")

_ID = re.compile(r"[a-z0-9][a-z0-9-]*")
_FINGERPRINT = re.compile(r"sha256:[0-9a-f]{64}")
_HTTPS_URL = re.compile(r"https://[A-Za-z0-9.-]+(?::[0-9]+)?(?:/\S*)?")


class SourceRegistryError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class LowRiskPolicy:
    eligible: bool
    # The largest relative change a low-risk update may make to one rate.
    max_relative_change: Decimal | None
    # The catalog price columns a low-risk update may change.
    dimensions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvidencePolicy:
    # Whether the raw fetched content may be kept as private evidence.
    store_raw: bool
    # Whether the raw content may be published or redistributed.
    redistribute_raw: bool
    # The longest source excerpt evidence may quote.
    excerpt_chars: int
    # Why raw content may not be stored or redistributed, when it may not.
    reason: str | None


@dataclass(frozen=True, slots=True)
class PriceSource:
    id: str
    provider: str
    channel: str
    # False for a channel the price catalog does not carry yet.
    catalog_channel: bool
    url: str
    parser: str
    source_shape: str
    clarity: str
    dimensions: tuple[str, ...]
    # The expected structural fingerprint of the page; None until reviewed.
    fingerprint: str | None
    low_risk: LowRiskPolicy
    evidence: EvidencePolicy


@dataclass(frozen=True, slots=True)
class SourceRegistry:
    schema_version: int
    sources: tuple[PriceSource, ...]

    def get(self, source_id: str) -> PriceSource:
        for source in self.sources:
            if source.id == source_id:
                return source
        raise SourceRegistryError(f"unknown source {source_id!r}")

    def low_risk(self) -> tuple[PriceSource, ...]:
        return tuple(source for source in self.sources if source.low_risk.eligible)

    def allowed_urls(self) -> frozenset[str]:
        """The only URLs a refresh script may fetch."""
        return frozenset(source.url for source in self.sources)


def _boolean(value: Any, message: str) -> bool:
    if not isinstance(value, bool):
        raise SourceRegistryError(message)
    return value


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _low_risk(raw: Any, label: str, dimensions: tuple[str, ...]) -> LowRiskPolicy:
    if not isinstance(raw, Mapping):
        raise SourceRegistryError(f"{label}: low_risk must be a mapping")
    eligible = _boolean(
        raw.get("eligible"), f"{label}: low_risk.eligible must be true or false"
    )
    bound = raw.get("max_relative_change")
    allowed = raw.get("dimensions") or []
    if not isinstance(allowed, list):
        raise SourceRegistryError(f"{label}: low_risk.dimensions must be a list")
    if not eligible:
        if bound is not None or allowed:
            raise SourceRegistryError(
                f"{label}: a non-low-risk source carries no low-risk bounds"
            )
        return LowRiskPolicy(False, None, ())
    try:
        parsed = Decimal(bound) if isinstance(bound, str) else None
    except InvalidOperation:
        parsed = None
    if parsed is None or not parsed.is_finite() or not Decimal(0) < parsed <= 1:
        raise SourceRegistryError(
            f"{label}: max_relative_change must be a decimal string in (0, 1]"
        )
    if not allowed:
        raise SourceRegistryError(f"{label}: a low-risk source needs dimensions")
    for dimension in allowed:
        if dimension not in dimensions:
            raise SourceRegistryError(
                f"{label}: low-risk dimension {dimension!r} is not a source dimension"
            )
    return LowRiskPolicy(True, parsed, tuple(allowed))


def _evidence(raw: Any, label: str) -> EvidencePolicy:
    if not isinstance(raw, Mapping):
        raise SourceRegistryError(f"{label}: evidence must be a mapping")
    store_raw = _boolean(
        raw.get("store_raw"), f"{label}: evidence.store_raw must be true or false"
    )
    redistribute_raw = _boolean(
        raw.get("redistribute_raw"),
        f"{label}: evidence.redistribute_raw must be true or false",
    )
    if redistribute_raw and not store_raw:
        raise SourceRegistryError(
            f"{label}: raw content cannot be redistributed unless it may be stored"
        )
    excerpt_chars = raw.get("excerpt_chars")
    if not isinstance(excerpt_chars, int) or isinstance(excerpt_chars, bool) or excerpt_chars < 0:
        raise SourceRegistryError(
            f"{label}: evidence.excerpt_chars must be a non-negative integer"
        )
    reason = _text(raw.get("reason"))
    if reason is None and not (store_raw and redistribute_raw):
        raise SourceRegistryError(
            f"{label}: evidence needs a reason when raw content may not be "
            "stored or redistributed"
        )
    return EvidencePolicy(store_raw, redistribute_raw, excerpt_chars, reason)


def _source(raw: Any) -> PriceSource:
    if not isinstance(raw, Mapping):
        raise SourceRegistryError("source entry must be a mapping")
    source_id = raw.get("id")
    if not isinstance(source_id, str) or not _ID.fullmatch(source_id):
        raise SourceRegistryError(f"invalid source id {source_id!r}")
    label = source_id
    provider = _text(raw.get("provider"))
    if provider is None:
        raise SourceRegistryError(f"{label}: needs provider")
    channel = _text(raw.get("channel"))
    if channel is None:
        raise SourceRegistryError(f"{label}: needs channel")
    catalog_channel = _boolean(
        raw.get("catalog_channel"), f"{label}: catalog_channel must be true or false"
    )
    url = raw.get("url")
    if not isinstance(url, str) or not _HTTPS_URL.fullmatch(url):
        raise SourceRegistryError(f"{label}: url must be an https URL")
    parser = raw.get("parser")
    if parser not in PARSER_EMITS_ROWS:
        raise SourceRegistryError(f"{label}: unknown parser {parser!r}")
    shape = raw.get("source_shape")
    if shape not in SOURCE_SHAPES:
        raise SourceRegistryError(f"{label}: unknown source_shape {shape!r}")
    clarity = raw.get("clarity")
    if clarity not in CLARITIES:
        raise SourceRegistryError(f"{label}: unknown clarity {clarity!r}")
    raw_dimensions = raw.get("dimensions")
    if not isinstance(raw_dimensions, list):
        raise SourceRegistryError(f"{label}: dimensions must be a list")
    for dimension in raw_dimensions:
        if dimension not in TOKEN_DIMENSIONS + NON_TOKEN_DIMENSIONS:
            raise SourceRegistryError(f"{label}: unknown dimension {dimension!r}")
        if shape == "gpu_hour_or_license" and dimension in TOKEN_DIMENSIONS:
            raise SourceRegistryError(
                f"{label}: a gpu_hour_or_license source cannot represent token "
                f"dimension {dimension!r}"
            )
        if shape != "gpu_hour_or_license" and dimension in NON_TOKEN_DIMENSIONS:
            raise SourceRegistryError(
                f"{label}: a token source cannot represent dimension {dimension!r}"
            )
    if len(set(raw_dimensions)) != len(raw_dimensions):
        raise SourceRegistryError(f"{label}: duplicate dimension")
    dimensions = tuple(raw_dimensions)
    fingerprint = raw.get("fingerprint")
    if fingerprint is not None and (
        not isinstance(fingerprint, str) or not _FINGERPRINT.fullmatch(fingerprint)
    ):
        raise SourceRegistryError(
            f"{label}: fingerprint must be null or sha256:<64 hex digits>"
        )
    low_risk = _low_risk(raw.get("low_risk"), label, dimensions)
    if low_risk.eligible:
        if not catalog_channel:
            raise SourceRegistryError(f"{label}: a low-risk source needs a catalog channel")
        if not PARSER_EMITS_ROWS[parser]:
            raise SourceRegistryError(
                f"{label}: parser {parser!r} emits no price rows, so it cannot be low-risk"
            )
    return PriceSource(
        id=source_id,
        provider=provider,
        channel=channel,
        catalog_channel=catalog_channel,
        url=url,
        parser=parser,
        source_shape=shape,
        clarity=clarity,
        dimensions=dimensions,
        fingerprint=fingerprint,
        low_risk=low_risk,
        evidence=_evidence(raw.get("evidence"), label),
    )


def parse_source_registry(document: Any) -> SourceRegistry:
    if not isinstance(document, Mapping):
        raise SourceRegistryError("source registry must be a mapping")
    version = document.get("schema_version")
    if version != SOURCE_REGISTRY_SCHEMA_VERSION:
        raise SourceRegistryError(
            f"unsupported source registry schema_version {version}"
        )
    entries = document.get("sources")
    if not isinstance(entries, list) or not entries:
        raise SourceRegistryError("source registry needs a non-empty sources list")
    sources: list[PriceSource] = []
    providers_by_url: dict[str, str] = {}
    for raw in entries:
        source = _source(raw)
        if any(other.id == source.id for other in sources):
            raise SourceRegistryError(f"duplicate source id {source.id!r}")
        other_provider = providers_by_url.setdefault(source.url, source.provider)
        if other_provider != source.provider:
            first, second = sorted((other_provider, source.provider))
            raise SourceRegistryError(
                f"url {source.url} is registered by providers {first} and {second}"
            )
        sources.append(source)
    return SourceRegistry(version, tuple(sources))


def load_source_registry(path: str | Path | None = None) -> SourceRegistry:
    """Load a registry file; the bundled registry by default."""
    resolved = Path(path) if path is not None else DEFAULT_REGISTRY_PATH
    return parse_source_registry(yaml.safe_load(resolved.read_bytes()))


def _catalog_channels(document: Mapping[str, Any]) -> set[str]:
    channels: set[str] = set()
    for entry in document.get("models") or []:
        for row in (entry.get("prices") or []) + (entry.get("aliases") or []):
            if row.get("channel"):
                channels.add(str(row["channel"]))
    return channels


def validate_source_registry(
    registry: SourceRegistry, catalog: LoadedCatalog
) -> None:
    """Check each source's channel against a loaded catalog: a catalog
    channel must have catalog rows, and a registry-only channel must not."""
    channels = _catalog_channels(catalog.document)
    for source in registry.sources:
        if source.catalog_channel and source.channel not in channels:
            raise SourceRegistryError(
                f"{source.id}: catalog channel {source.channel!r} has no catalog rows"
            )
        if not source.catalog_channel and source.channel in channels:
            raise SourceRegistryError(
                f"{source.id}: registry-only channel {source.channel!r} is a "
                "catalog channel"
            )
