"""Pure low-risk refresh planning and discovery evidence.

:func:`plan_low_risk_refresh` compares supplied source content with a
supplied catalog document and returns a pricing delta for the changes a
low-risk source may make, one observation per source (including runs with
no change, so a missing run is detectable), and an escalation for every
change it leaves out. It never fetches, applies the delta, or writes.

A low-risk change updates an existing rate on an existing row of an existing
model, within the source's relative bound, starting on the UTC observation
date. New models, new regions, new dimensions, models a page no longer
lists, ambiguous parses, format changes, invalid rates and backdated or
same-day effective dates escalate instead.

:func:`discovery_evidence` builds the deterministic evidence envelope a
reviewer needs for a source that is not low-risk or for an escalation.
"""

from __future__ import annotations

import copy
from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Mapping

from .catalog_delta import (
    PRICING_DELTA_KIND,
    PRICING_DELTA_SCHEMA_VERSION,
    RATE_FIELDS,
    PriceRowKey,
    _instant,
    _iso_date,
    row_key,
)
from .extractors import (
    ExtractedRate,
    Extraction,
    extract,
    page_match,
    page_text,
    redact_evidence,
    regional_key,
)
from .loader import _canonical_value, load_catalog_document
from .sources import PriceSource, SourceRegistry

# Provider names that are not a page's own spelling of a model.
_ALIAS_ONLY_PROVIDERS = {"litellm", "unknown"}


@dataclass(frozen=True, slots=True)
class Observation:
    """One source's result for one run."""

    id: str
    source_id: str
    url: str
    # unchanged | changed | escalated | changed_and_escalated | missing
    result: str
    fingerprint: str | None
    expected_fingerprint: str | None
    rows_compared: int
    changes: int
    escalations: int

    def as_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_id": self.source_id,
            "url": self.url,
            "result": self.result,
            "fingerprint": self.fingerprint,
            "expected_fingerprint": self.expected_fingerprint,
            "rows_compared": self.rows_compared,
            "changes": self.changes,
            "escalations": self.escalations,
        }


@dataclass(frozen=True, slots=True)
class Escalation:
    """A change left out of the delta for review."""

    source_id: str
    reason: str
    key: PriceRowKey | None = None
    model_key: str | None = None
    region: str | None = None
    dimension: str | None = None
    current: str | None = None
    observed: str | None = None
    detail: str = ""
    result: str = "escalated"

    def as_json(self) -> dict[str, Any]:
        return {
            "result": self.result,
            "source_id": self.source_id,
            "reason": self.reason,
            "key": _key_json(self.key) if self.key else None,
            "model_key": self.model_key,
            "region": self.region,
            "dimension": self.dimension,
            "current": self.current,
            "observed": self.observed,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class RefreshResult:
    delta: dict[str, Any]
    observations: tuple[Observation, ...]
    escalations: tuple[Escalation, ...]

    def as_json(self) -> dict[str, Any]:
        return {
            # Rows may carry date or datetime values; report them as ISO text.
            "delta": _canonical_value(self.delta),
            "observations": [o.as_json() for o in self.observations],
            "escalations": [e.as_json() for e in self.escalations],
        }


def _key_json(key: PriceRowKey) -> dict[str, str]:
    return {
        "canonical_id": key.canonical_id,
        "channel": key.channel,
        "region": key.region,
        "effective_from": key.effective_from,
    }


def _money(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _rate(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except ArithmeticError:
        return None


def _day(value: Any) -> date:
    """The UTC date of an effective date given as a date, a naive or aware
    datetime, or an ISO string with or without an offset."""
    instant = _instant(value)
    if instant is None:
        raise ValueError(f"invalid effective date {value!r}")
    return instant.date()


def _in_effect(item: Mapping[str, Any], day: date, *, dated: bool) -> bool:
    starts = item.get("effective_from")
    ends = item.get("effective_to")
    if starts is None and dated:
        return False
    return (starts is None or _day(starts) <= day) and (ends is None or day < _day(ends))


# --- Matching catalog rows to extracted rates ------------------------------------


@dataclass(frozen=True, slots=True)
class _RowMatch:
    canonical_id: str
    key: PriceRowKey
    row: Mapping[str, Any]
    rate: ExtractedRate | None
    duplicate_model: bool


def _names(entry: Mapping[str, Any], channel: str, day: date) -> list[str]:
    """Model ids a page might use for ``entry`` on ``channel``, most
    specific first."""
    names = [
        str(alias["alias"])
        for alias in entry.get("aliases") or []
        if alias.get("channel") == channel
        and str(alias.get("provider") or "").lower() not in _ALIAS_ONLY_PROVIDERS
        and _in_effect(alias, day, dated=False)
    ]
    tail = str(entry["canonical_id"]).split("/", 1)[-1]
    if tail not in names:
        names.append(tail)
    return names


def _match(
    source: PriceSource, extraction: Extraction, document: Mapping[str, Any], day: date
) -> tuple[list[_RowMatch], list[tuple[ExtractedRate, str]]]:
    """Each current catalog row on the source's channel with its extracted
    rate (or None), and every extracted rate no row claimed, with whether it
    is an unknown model or a new region of a known one."""
    by_key = {regional_key(r.model_key, r.region): r for r in extraction.rates}
    tables = {key: rate.rates for key, rate in by_key.items()}
    entries = [e for e in document.get("models") or [] if isinstance(e, Mapping)]
    counts = Counter(str(e.get("canonical_id")) for e in entries)
    matches: list[_RowMatch] = []
    claimed: set[str] = set()
    matched_models: set[str] = set()
    for entry in entries:
        canonical_id = str(entry["canonical_id"])
        names = _names(entry, source.channel, day)
        for row in entry.get("prices") or []:
            if row.get("channel") != source.channel or not _in_effect(row, day, dated=True):
                continue
            region = str(row.get("region") or "global")
            found = None
            for name in names:
                rates = page_match(source.parser, tables, name, region)
                if rates is not None:
                    found = next(r for k, r in by_key.items() if tables[k] is rates)
                    break
            if found is not None:
                claimed.add(regional_key(found.model_key, found.region))
                matched_models.add(found.model_key)
            matches.append(
                _RowMatch(canonical_id, row_key(canonical_id, row), row, found, counts[canonical_id] > 1)
            )
    unclaimed = [
        (rate, "new_region" if rate.model_key in matched_models else "unknown_model")
        for key, rate in sorted(by_key.items())
        if key not in claimed
    ]
    return matches, unclaimed


# --- Low-risk refresh ----------------------------------------------------------------


def _compare_row(source: PriceSource, match: _RowMatch, ambiguous: Iterable[str], observed_on: date) -> tuple[dict | None, list[Escalation]]:
    """The replace_price operation for one matched row, or its escalations."""
    rate = match.rate
    row = match.row

    def escalate(reason: str, **fields: Any) -> Escalation:
        return Escalation(
            source.id, reason, key=match.key, model_key=rate.model_key, region=match.key.region, **fields
        )

    # Ambiguities carry the extraction's own regional key, so a duplicate in
    # one region never blocks, or hides behind, another region's row.
    if regional_key(rate.model_key, rate.region) in ambiguous:
        return None, [escalate("ambiguous_parse", detail="the page gives two candidate values")]
    escalations: list[Escalation] = []
    # Every represented rate the row carries must still be on the page;
    # otherwise copying the row forward would keep a stale value.
    for dimension in source.dimensions:
        if row.get(dimension) is not None and dimension not in rate.rates:
            escalations.append(escalate("missing_rate", dimension=dimension, current=_money(_rate(row[dimension]))))
    changed: dict[str, Decimal] = {}
    for dimension in source.dimensions:
        observed = rate.rates.get(dimension)
        if observed is None:
            continue
        current = _rate(row.get(dimension))
        values = {"dimension": dimension, "current": _money(current) if current is not None else None, "observed": _money(observed)}
        if observed <= 0:
            escalations.append(escalate("non_positive_rate", **values))
        elif current is None:
            escalations.append(escalate("new_dimension", **values))
        elif observed == current:
            continue
        elif dimension not in source.low_risk.dimensions:
            escalations.append(escalate("dimension_not_allowed", **values))
        elif current == 0:
            escalations.append(escalate("bound_exceeded", detail="the current rate is zero", **values))
        else:
            relative = abs(observed - current) / current
            if relative > source.low_risk.max_relative_change:
                detail = f"relative change {_money(relative)} exceeds {_money(source.low_risk.max_relative_change)}"
                escalations.append(escalate("bound_exceeded", detail=detail, **values))
            else:
                changed[dimension] = observed
    if escalations or not changed:
        return None, escalations
    if row.get("effective_to") is not None:
        # A replacement would need to fit a window that already ends; that
        # schedule is a reviewer's decision, not an automated one.
        return None, [escalate("scheduled_window", detail=f"the current row already ends {_iso_date(row['effective_to'])}")]
    unrepresented = [f for f in RATE_FIELDS if row.get(f) is not None and f not in source.dimensions]
    if unrepresented:
        return None, [escalate("unrepresented_dimension", dimension=unrepresented[0], detail="the source cannot update this rate with the others")]
    if _day(row["effective_from"]) >= observed_on:
        return None, [escalate("not_after_current", detail=f"the current row starts {match.key.effective_from}")]
    return _replace_operation(source, match, changed, observed_on), []


def _replace_operation(
    source: PriceSource, match: _RowMatch, changed: Mapping[str, Decimal], observed_on: date
) -> dict[str, Any]:
    """Close ``match``'s row on ``observed_on`` and continue it with the
    ``changed`` rates, citing the source."""
    row = match.row
    price: dict[str, Any] = {
        "channel": match.key.channel,
        "region": match.key.region,
        "effective_from": observed_on.isoformat(),
    }
    for field in RATE_FIELDS:
        value = changed.get(field, _rate(row.get(field)))
        if value is not None:
            price[field] = _money(value)
    if row.get("rules"):
        price["rules"] = copy.deepcopy(row["rules"])
    price["source_url"] = source.url
    return {
        "op": "replace_price",
        "key": _key_json(match.key),
        "expect": copy.deepcopy(dict(row)),
        "price": price,
    }


def _refresh_source(
    source: PriceSource, content: str | None, document: Mapping[str, Any], observed_on: date
) -> tuple[Observation, list[dict], list[Escalation]]:
    observation_id = f"{source.id}@{observed_on.isoformat()}"

    def observe(result: str, fingerprint: str | None, rows: int, operations: list, escalations: list) -> tuple:
        observation = Observation(
            observation_id, source.id, source.url, result, fingerprint, source.fingerprint,
            rows, len(operations), len(escalations),
        )
        return observation, operations, escalations

    if content is None:
        return observe("missing", None, 0, [], [Escalation(source.id, "content_missing", detail="no content was supplied for this source")])
    extraction = extract(source.parser, content)
    fingerprint = extraction.fingerprint
    if not source.low_risk.eligible:
        return observe("escalated", fingerprint, 0, [], [Escalation(source.id, "not_low_risk")])
    if source.fingerprint is None:
        return observe("escalated", fingerprint, 0, [], [Escalation(source.id, "fingerprint_missing", detail="the registry records no expected fingerprint")])
    if fingerprint != source.fingerprint:
        return observe("escalated", fingerprint, 0, [], [Escalation(source.id, "fingerprint_mismatch", detail=f"expected {source.fingerprint}")])
    if not extraction.rates:
        return observe("escalated", fingerprint, 0, [], [Escalation(source.id, "extraction_empty")])

    matches, unclaimed = _match(source, extraction, document, observed_on)
    operations: list[dict] = []
    escalations: list[Escalation] = []
    reported_duplicates: set[str] = set()
    compared = 0
    for match in matches:
        if match.duplicate_model:
            if match.canonical_id not in reported_duplicates:
                reported_duplicates.add(match.canonical_id)
                escalations.append(Escalation(source.id, "duplicate_model_row", key=match.key, region=match.key.region, detail="the catalog has more than one entry for this model"))
            continue
        if match.rate is None:
            escalations.append(Escalation(source.id, "model_not_listed", key=match.key, region=match.key.region, detail="the page no longer lists this row"))
            continue
        compared += 1
        operation, row_escalations = _compare_row(source, match, extraction.ambiguities, observed_on)
        escalations.extend(row_escalations)
        if operation is not None:
            operations.append(operation)
    for rate, reason in unclaimed:
        escalations.append(
            Escalation(
                source.id, reason, model_key=rate.model_key, region=rate.region,
                detail="the catalog has no row for this model" if reason == "unknown_model" else "the catalog has no row for this region",
            )
        )
    if operations and escalations:
        result = "changed_and_escalated"
    elif operations:
        result = "changed"
    elif escalations:
        result = "escalated"
    else:
        result = "unchanged"
    return observe(result, fingerprint, compared, operations, escalations)


def plan_low_risk_refresh(
    document: Mapping[str, Any],
    registry: SourceRegistry,
    contents: Mapping[str, str],
    *,
    observed_on: date,
) -> RefreshResult:
    """Plan a low-risk refresh of ``document`` from ``contents`` (source id
    -> fetched text). Every low-risk source in ``registry`` gets an
    observation; a supplied source that is not low-risk is escalated. The
    document is validated first and never modified."""
    load_catalog_document(document)
    sources = [s for s in registry.sources if s.low_risk.eligible or s.id in contents]
    for source_id in contents:
        registry.get(source_id)
    observations: list[Observation] = []
    operations: list[dict] = []
    escalations: list[Escalation] = []
    evidence: list[dict[str, Any]] = []
    for source in sources:
        observation, source_operations, source_escalations = _refresh_source(
            source, contents.get(source.id), document, observed_on
        )
        observations.append(observation)
        operations.extend(source_operations)
        escalations.extend(source_escalations)
        if observation.fingerprint is not None:
            evidence.append({"source_id": source.id, "url": source.url, "fingerprint": observation.fingerprint})
    delta = {
        "schema_version": PRICING_DELTA_SCHEMA_VERSION,
        "kind": PRICING_DELTA_KIND,
        "provenance": {
            "generator": "metergraph-core low-risk refresh",
            "observed_on": observed_on.isoformat(),
            "observations": [o.id for o in observations],
            "evidence": evidence,
        },
        "operations": operations,
    }
    return RefreshResult(delta, tuple(observations), tuple(escalations))


# --- Discovery evidence ------------------------------------------------------------


def _rates_json(rates: Mapping[str, Any], dimensions: Iterable[str]) -> dict[str, str]:
    return {
        dimension: _money(_rate(rates[dimension]))
        for dimension in dimensions
        if rates.get(dimension) is not None
    }


def discovery_evidence(
    source: PriceSource,
    content: str,
    document: Mapping[str, Any],
    *,
    observed_on: date,
) -> dict[str, Any]:
    """The evidence a reviewer needs for ``source``: its fingerprint against
    the registry's, the normalized extraction, and -- where the parser yields
    rates -- each candidate value with the prior catalog value, the reason it
    needs review, and a proposed operation for each changed catalog row. It
    is deterministic; its operations are proposals that need review."""
    extraction = extract(source.parser, content)
    candidates: list[dict[str, Any]] = []
    operations: list[dict[str, Any]] = []
    if extraction.applicable:
        matches, unclaimed = _match(source, extraction, document, observed_on)
        for match in matches:
            if match.rate is None:
                continue
            candidate = _rates_json(match.rate.rates, source.dimensions)
            prior = _rates_json(match.row, source.dimensions)
            if any(prior.get(d) != v for d, v in candidate.items()):
                candidates.append(
                    {"model_key": match.rate.model_key, "region": match.key.region, "canonical_id": match.canonical_id, "reason": "changed", "prior": prior, "candidate": candidate}
                )
                complete = all(
                    d in match.rate.rates for d in source.dimensions if match.row.get(d) is not None
                )
                ambiguous = regional_key(match.rate.model_key, match.rate.region) in extraction.ambiguities
                if (
                    complete
                    and match.row.get("effective_to") is None
                    and not ambiguous
                    and not match.duplicate_model
                    and _day(match.row["effective_from"]) < observed_on
                ):
                    changed = {d: v for d, v in match.rate.rates.items() if d in source.dimensions}
                    operations.append(_replace_operation(source, match, changed, observed_on))
        for rate, reason in unclaimed:
            candidates.append(
                {"model_key": rate.model_key, "region": rate.region, "canonical_id": None, "reason": reason, "prior": None, "candidate": _rates_json(rate.rates, source.dimensions)}
            )
        candidates.sort(key=lambda c: (c["model_key"], c["region"]))
    excerpt = None
    if source.evidence.excerpt_chars:
        excerpt = page_text(content).strip(" |")[: source.evidence.excerpt_chars]
    return {
        "source_id": source.id,
        "provider": source.provider,
        "channel": source.channel,
        "url": source.url,
        "parser": source.parser,
        "observed_on": observed_on.isoformat(),
        "fingerprint": extraction.fingerprint,
        "expected_fingerprint": source.fingerprint,
        "fingerprint_matches": extraction.fingerprint == source.fingerprint,
        "applicable": extraction.applicable,
        "ambiguities": list(extraction.ambiguities),
        "candidates": candidates,
        # Reviewable replace_price operations for changed catalog rows; new
        # models and regions stay evidence only. Dates are reported as text.
        "operations": _canonical_value(operations),
        # Raw page cells and statements appear only where the registry
        # allows storing raw content; otherwise only normalized facts.
        "evidence": (
            dict(copy.deepcopy(extraction.evidence), redacted=False)
            if source.evidence.store_raw
            else redact_evidence(extraction.evidence)
        ),
        "retention": {
            "store_raw": source.evidence.store_raw,
            "redistribute_raw": source.evidence.redistribute_raw,
            "excerpt_chars": source.evidence.excerpt_chars,
            "reason": source.evidence.reason,
        },
        "excerpt": excerpt,
    }
