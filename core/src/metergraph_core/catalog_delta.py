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
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from .loader import CatalogError, _canonical_json, load_catalog_document

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


def _instant(value: Any) -> datetime | None:
    """An ISO date or datetime as an aware UTC instant, or None when
    ``value`` is not one. A value without an offset is UTC, as the catalog
    loader reads it."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day)
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text[-1] in "Zz":
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_date(value: Any) -> str | None:
    """The canonical text of an effective date: ``YYYY-MM-DD`` for UTC
    midnight, otherwise the UTC ISO datetime. Two spellings of one instant
    get one text, so they name one row. None when ``value`` is not a date."""
    instant = _instant(value)
    if instant is None:
        return None
    if instant.time() == time(0):
        return instant.date().isoformat()
    return instant.isoformat()


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
        if _instant(effective_to) <= _instant(key.effective_from):
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


def row_key(canonical_id: str, row: Mapping[str, Any]) -> PriceRowKey | None:
    """The identity of a catalog price row, or None when it has no readable
    channel or effective date."""
    channel = _text(row.get("channel"))
    effective_from = _iso_date(row.get("effective_from"))
    if channel is None or effective_from is None:
        return None
    return PriceRowKey(
        canonical_id=canonical_id,
        channel=channel,
        region=_text(row.get("region")) or "global",
        effective_from=effective_from,
    )


def _locate(
    document: Mapping[str, Any], operation: DeltaOperation
) -> tuple[int | None, list[int], str | None]:
    """The target model's index and the indexes of its rows with the
    operation's key, or the reason the target cannot be located."""
    key = operation.key
    models = [
        index
        for index, entry in enumerate(document.get("models") or [])
        if isinstance(entry, Mapping) and entry.get("canonical_id") == key.canonical_id
    ]
    if not models:
        return None, [], "unknown_model"
    if len(models) > 1:
        return None, [], "duplicate_model"
    entry = document["models"][models[0]]
    rows = [
        index
        for index, row in enumerate(entry.get("prices") or [])
        if isinstance(row, Mapping) and row_key(key.canonical_id, row) == key
    ]
    if len(rows) > 1:
        return models[0], rows, "duplicate_row"
    return models[0], rows, None


def _precondition(document: Mapping[str, Any], operation: DeltaOperation) -> str | None:
    """Why ``operation`` does not apply to ``document``, or None when it does."""
    model_index, rows, reason = _locate(document, operation)
    if reason is not None:
        return reason
    if operation.op == "add_price":
        return "row_exists" if rows else None
    if not rows:
        return "row_missing"
    row = document["models"][model_index]["prices"][rows[0]]
    if row.get("effective_to") is not None:
        return "row_not_open"
    if _canonical_json(row) != _canonical_json(operation.expect):
        return "expect_mismatch"
    if operation.op == "replace_price" and _instant(
        operation.price["effective_from"]
    ) <= _instant(operation.key.effective_from):
        return "not_after_current"
    return None


def apply_catalog_delta(
    document: Mapping[str, Any], delta_document: Mapping[str, Any]
) -> dict[str, Any]:
    """Return a new catalog document with ``delta_document`` applied.

    Every operation's precondition is checked against ``document`` first; if
    any fails, :class:`CatalogDeltaConflict` lists all of them and nothing is
    returned. A replaced or closed row gets an ``effective_to``; new rows are
    appended; no other row changes. Neither input is mutated. The top-level
    ``version`` and ``pricing_verified_at`` are preserved: publishing a new
    catalog version is not this function's job. The complete result is
    validated with :func:`load_catalog_document`.
    """
    delta = parse_catalog_delta(delta_document)
    if not isinstance(document, Mapping):
        raise CatalogDeltaError("catalog document must be a mapping")
    conflicts = tuple(
        DeltaConflict(operation.index, operation.op, operation.key, reason)
        for operation in delta.operations
        if (reason := _precondition(document, operation)) is not None
    )
    if conflicts:
        raise CatalogDeltaConflict(conflicts)

    result = copy.deepcopy(dict(document))
    for operation in delta.operations:
        model_index, rows, _ = _locate(result, operation)
        prices = result["models"][model_index].setdefault("prices", [])
        if operation.op == "add_price":
            prices.append(copy.deepcopy(dict(operation.price)))
        elif operation.op == "replace_price":
            prices[rows[0]]["effective_to"] = operation.price["effective_from"]
            prices.insert(rows[0] + 1, copy.deepcopy(dict(operation.price)))
        else:
            prices[rows[0]]["effective_to"] = operation.effective_to
    try:
        load_catalog_document(result)
    except CatalogError as exc:
        raise CatalogDeltaError(f"delta result is not a valid catalog: {exc}") from exc
    return result
