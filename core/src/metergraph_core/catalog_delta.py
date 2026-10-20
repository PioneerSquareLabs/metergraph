"""Pricing deltas: proposed changes to the price rows of a catalog document.

A delta never serves prices. It names each target row by its identity
``(canonical_id, channel, region, effective_from)``, carries the row's
expected current state as a precondition, and carries the new
effective-dated value. Schema version 1 covers price rows on models the
catalog already has:

- ``add_price`` appends a row; ``expect`` is null because no such row may
  exist.
- ``replace_price`` closes the open row at the new row's ``effective_from``
  and appends the new row. History is never rewritten.
- ``close_price`` closes the open row without a replacement.

``provenance`` references the observations and evidence the delta came
from.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from .loader import CatalogError

PRICING_DELTA_SCHEMA_VERSION = 1
PRICING_DELTA_KIND = "pricing_delta"
OPERATIONS = ("add_price", "replace_price", "close_price")

RATE_FIELDS = (
    "input_per_mtok",
    "output_per_mtok",
    "cache_read_per_mtok",
    "cache_write_5m_per_mtok",
    "cache_write_1h_per_mtok",
    "batch_input_per_mtok",
    "batch_output_per_mtok",
    "request_fee_per_1k_usd",
)
_PRICE_FIELDS = frozenset(
    ("channel", "region", "effective_from", "source_url", "rules") + RATE_FIELDS
)
_KEY_FIELDS = ("canonical_id", "channel", "region", "effective_from")


class CatalogDeltaError(CatalogError):
    """A delta document that does not follow the schema."""


@dataclass(frozen=True, slots=True)
class PriceRowKey:
    canonical_id: str
    channel: str
    region: str
    effective_from: str


@dataclass(frozen=True, slots=True)
class DeltaConflict:
    """One operation whose precondition does not hold against the document."""

    index: int
    op: str
    key: PriceRowKey
    reason: str
    detail: str = ""


class CatalogDeltaConflict(CatalogDeltaError):
    """The delta does not apply; ``conflicts`` names every failing operation."""

    def __init__(self, conflicts: tuple[DeltaConflict, ...]) -> None:
        self.conflicts = conflicts
        summary = "; ".join(
            f"operation {c.index} {c.op} {c.key.canonical_id} "
            f"{c.key.channel}/{c.key.region}@{c.key.effective_from}: {c.reason}"
            + (f" ({c.detail})" if c.detail else "")
            for c in conflicts
        )
        super().__init__(f"delta conflicts: {summary}")


@dataclass(frozen=True, slots=True)
class DeltaOperation:
    index: int
    op: str
    key: PriceRowKey
    # The exact current row for replace/close; None for add.
    expect: Mapping[str, Any] | None
    # The new row for add/replace; None for close.
    price: Mapping[str, Any] | None
    # The closing date for close_price.
    effective_to: str | None


@dataclass(frozen=True, slots=True)
class CatalogDelta:
    schema_version: int
    operations: tuple[DeltaOperation, ...]
    provenance: Mapping[str, Any]


def _iso_date(value: Any) -> str | None:
    """A date as its ISO string, or None when ``value`` is not one."""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return value.strip()


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _parse_key(raw: Any, label: str) -> PriceRowKey:
    if not isinstance(raw, Mapping):
        raise CatalogDeltaError(f"{label}: key must be a mapping")
    for field in ("canonical_id", "channel", "region"):
        if _text(raw.get(field)) is None:
            raise CatalogDeltaError(f"{label}: key needs {field}")
    if raw.get("effective_from") is None:
        raise CatalogDeltaError(f"{label}: key needs effective_from")
    effective_from = _iso_date(raw.get("effective_from"))
    if effective_from is None:
        raise CatalogDeltaError(f"{label}: invalid key effective_from")
    return PriceRowKey(
        canonical_id=_text(raw["canonical_id"]),
        channel=_text(raw["channel"]),
        region=_text(raw["region"]),
        effective_from=effective_from,
    )


def _decimal_string(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        return False
    return parsed.is_finite() and parsed >= 0


def _parse_price(raw: Any, key: PriceRowKey, label: str, *, same_date: bool) -> dict:
    if not isinstance(raw, Mapping):
        raise CatalogDeltaError(f"{label}: price must be a mapping")
    if "effective_to" in raw:
        raise CatalogDeltaError(f"{label}: a new price row is open; drop effective_to")
    for field in raw:
        if field not in _PRICE_FIELDS:
            raise CatalogDeltaError(f"{label}: price has unknown field {field!r}")
    for field in ("channel", "region"):
        if _text(raw.get(field)) != getattr(key, field):
            raise CatalogDeltaError(f"{label}: price {field} must match key")
    effective_from = _iso_date(raw.get("effective_from"))
    if effective_from is None:
        raise CatalogDeltaError(f"{label}: invalid price effective_from")
    if same_date and effective_from != key.effective_from:
        raise CatalogDeltaError(f"{label}: price effective_from must match key")
    if _text(raw.get("source_url")) is None:
        raise CatalogDeltaError(f"{label}: price needs source_url")
    for field in RATE_FIELDS:
        if field in raw and not _decimal_string(raw[field]):
            raise CatalogDeltaError(
                f"{label}: price {field} must be a non-negative decimal string"
            )
    if "rules" in raw and not isinstance(raw["rules"], Mapping):
        raise CatalogDeltaError(f"{label}: price rules must be a mapping")
    price = copy.deepcopy(dict(raw))
    price["effective_from"] = effective_from
    return price


def _parse_operation(index: int, raw: Any) -> DeltaOperation:
    label = f"operation {index}"
    if not isinstance(raw, Mapping):
        raise CatalogDeltaError(f"{label}: must be a mapping")
    op = raw.get("op")
    if op not in OPERATIONS:
        raise CatalogDeltaError(f"{label}: unknown op {op!r}")
    key = _parse_key(raw.get("key"), label)
    expect = raw.get("expect")
    if op == "add_price":
        if "expect" not in raw or expect is not None:
            raise CatalogDeltaError(f"{label}: add_price expect must be null")
    elif not isinstance(expect, Mapping):
        raise CatalogDeltaError(f"{label}: {op} expect must be the full current row")
    price = None
    effective_to = None
    if op == "close_price":
        if "price" in raw:
            raise CatalogDeltaError(f"{label}: close_price takes no price")
        effective_to = _iso_date(raw.get("effective_to"))
        if effective_to is None:
            raise CatalogDeltaError(f"{label}: invalid effective_to")
        if datetime.fromisoformat(effective_to) <= datetime.fromisoformat(
            key.effective_from
        ):
            raise CatalogDeltaError(
                f"{label}: effective_to must follow key effective_from"
            )
    else:
        price = _parse_price(
            raw.get("price"), key, label, same_date=op == "add_price"
        )
    return DeltaOperation(
        index=index,
        op=op,
        key=key,
        expect=copy.deepcopy(dict(expect)) if expect is not None else None,
        price=price,
        effective_to=effective_to,
    )


def parse_catalog_delta(delta_document: Mapping[str, Any]) -> CatalogDelta:
    """Validate a delta document against schema version 1 without applying
    it. Raises :class:`CatalogDeltaError` naming the first malformed part."""
    if not isinstance(delta_document, Mapping):
        raise CatalogDeltaError("delta document must be a mapping")
    version = delta_document.get("schema_version")
    if version != PRICING_DELTA_SCHEMA_VERSION:
        raise CatalogDeltaError(f"unsupported delta schema_version {version}")
    if delta_document.get("kind") != PRICING_DELTA_KIND:
        raise CatalogDeltaError(f"delta kind must be {PRICING_DELTA_KIND}")
    provenance = delta_document.get("provenance")
    if not isinstance(provenance, Mapping):
        raise CatalogDeltaError("delta needs provenance")
    if not isinstance(provenance.get("observations"), list):
        raise CatalogDeltaError("delta provenance needs an observations list")
    if "evidence" in provenance and not isinstance(provenance["evidence"], list):
        raise CatalogDeltaError("delta provenance evidence must be a list")
    raw_operations = delta_document.get("operations")
    if not isinstance(raw_operations, list):
        raise CatalogDeltaError("delta operations must be a list")
    operations: list[DeltaOperation] = []
    seen: set[PriceRowKey] = set()
    for index, raw in enumerate(raw_operations):
        operation = _parse_operation(index, raw)
        if operation.key in seen:
            raise CatalogDeltaError(f"operation {index}: duplicate target")
        seen.add(operation.key)
        operations.append(operation)
    return CatalogDelta(
        schema_version=version,
        operations=tuple(operations),
        provenance=copy.deepcopy(dict(provenance)),
    )
