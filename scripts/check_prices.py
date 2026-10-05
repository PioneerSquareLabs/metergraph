"""Compare the catalog rows in effect today with public price sources.

Each row is graded: confirmed, disputed (one source disagrees), update (the
channel's own list disagrees, or two sources agree against the catalog) or
unchecked. The script reads prices only and never edits the catalog.

Sources, none needing credentials: ``vercel`` (the AI Gateway list, the
gateway's own price), ``litellm`` (matched through ``provider: litellm``
aliases), ``modelsdev`` (matched by provider and model id) and ``portkey``
(one request per model, so opt-in).

Usage:

    python scripts/check_prices.py [--json report.json] [--cache-dir DIR]
    python scripts/check_prices.py --sources vercel,litellm,modelsdev,portkey

Exit status: 0 nothing to do, 2 an update signal, 1 with ``--strict`` when any
source disagrees.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG_PATH = (
    REPOSITORY_ROOT / "core" / "src" / "metergraph_core" / "data" / "prices.yaml"
)

FIELDS = ("input", "output", "cache_read", "cache_write")
CATALOG_FIELDS = {
    "input": "input_per_mtok",
    "output": "output_per_mtok",
    "cache_read": "cache_read_per_mtok",
    "cache_write": "cache_write_5m_per_mtok",
}
MTOK = Decimal(1_000_000)
# A source agrees when within this share of our rate or this many dollars per
# million tokens, whichever is looser.
RELATIVE_TOLERANCE = Decimal("0.01")
ABSOLUTE_TOLERANCE = Decimal("0.001")

# The source that is the channel's own price list.
AUTHORITATIVE = {"vercel-ai-gateway": "vercel"}

# Aliases recorded under these providers are source keys, not provider ids.
SYNTHETIC_PROVIDERS = {"litellm", "unknown"}

LITELLM_PROVIDER_CHANNEL = {
    "openai": "openai-api",
    "anthropic": "anthropic-api",
    "gemini": "google-api",
    "vertex_ai": "google-vertex-ai",
    "vertex_ai-language-models": "google-vertex-ai",
    "vertex_ai-anthropic_models": "google-vertex-ai",
    "deepseek": "deepseek-api",
    "xai": "xai-api",
    "perplexity": "perplexity-api",
    "mistral": "mistral-api",
    "fireworks_ai": "fireworks-api",
    "bedrock": "aws-bedrock",
    "bedrock_converse": "aws-bedrock",
    "amazon_nova": "aws-bedrock",
    "moonshot": "moonshot-api",
    "minimax": "minimax-api",
}

MODELSDEV_PROVIDER = {
    "openai-api": "openai",
    "anthropic-api": "anthropic",
    "google-api": "google",
    "google-vertex-ai": "google-vertex",
    "deepseek-api": "deepseek",
    "xai-api": "xai",
    "perplexity-api": "perplexity",
    "mistral-api": "mistral",
    "fireworks-api": "fireworks-ai",
    "aws-bedrock": "amazon-bedrock",
    "vercel-ai-gateway": "vercel",
    "alibaba-api": "alibaba",
    "moonshot-api": "moonshotai",
    "minimax-api": "minimax",
    "zai-api": "zai",
    "meta-api": "meta",
}

PORTKEY_PROVIDER = {
    "openai-api": "openai",
    "anthropic-api": "anthropic",
    "google-api": "google",
    "google-vertex-ai": "vertex-ai",
    "deepseek-api": "deepseek",
    "xai-api": "x-ai",
    "perplexity-api": "perplexity-ai",
    "mistral-api": "mistral-ai",
    "fireworks-api": "fireworks-ai",
    "aws-bedrock": "bedrock",
}

VERCEL_URL = "https://ai-gateway.vercel.sh/v1/models"
LITELLM_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
MODELSDEV_URL = "https://models.dev/api.json"
PORTKEY_URL = "https://api.portkey.ai/model-configs/pricing/{provider}/{model}"
USER_AGENT = "metergraph-check-prices/1 (+https://github.com/PioneerSquareLabs/metergraph)"


# --- Catalog -----------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class CatalogRow:
    canonical_id: str
    channel: str
    region: str
    effective_from: date
    rates: Mapping[str, Decimal]
    source_url: str
    # (provider, alias) pairs the catalog carries for this model on this channel.
    aliases: tuple[tuple[str, str], ...]

    # Quotes are keyed by model, channel and region.
    @property
    def key(self) -> tuple[str, str, str]:
        return (self.canonical_id, self.channel, self.region)

    def __hash__(self) -> int:
        return hash(self.key)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, CatalogRow) and self.key == other.key


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except ArithmeticError:
        return None


def _date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def effective_rows(document: Mapping[str, Any], today: date) -> list[CatalogRow]:
    """One row per (model, channel, region): the price in effect on ``today``."""
    rows: list[CatalogRow] = []
    for model in document.get("models") or []:
        canonical_id = str(model["canonical_id"])
        aliases_by_channel: dict[str, list[tuple[str, str]]] = {}
        for alias in model.get("aliases") or []:
            aliases_by_channel.setdefault(str(alias["channel"]), []).append(
                (str(alias["provider"]), str(alias["alias"]))
            )
        current: dict[tuple[str, str], Mapping[str, Any]] = {}
        for price in model.get("prices") or []:
            starts = _date(price["effective_from"])
            ends = price.get("effective_to")
            if starts > today or (ends is not None and _date(ends) <= today):
                continue
            key = (str(price["channel"]), str(price.get("region") or "global"))
            if key not in current or starts > _date(current[key]["effective_from"]):
                current[key] = price
        for (channel, region), price in sorted(current.items()):
            rates = {
                name: value
                for name, column in CATALOG_FIELDS.items()
                if (value := _decimal(price.get(column))) is not None
            }
            rows.append(
                CatalogRow(
                    canonical_id=canonical_id,
                    channel=channel,
                    region=region,
                    effective_from=_date(price["effective_from"]),
                    rates=rates,
                    source_url=str(price.get("source_url") or ""),
                    aliases=tuple(aliases_by_channel.get(channel, ())),
                )
            )
    return rows


# --- Sources -----------------------------------------------------------------


@dataclass(frozen=True)
class Quote:
    source: str
    key: str
    rates: Mapping[str, Decimal]
    # The source's channel, when it differs from the catalog row's.
    source_channel: str | None = None


def _per_token_to_mtok(value: Any) -> Decimal | None:
    rate = _decimal(value)
    return None if rate is None else rate * MTOK


def _vercel_quotes(data: Mapping[str, Any], rows: list[CatalogRow]) -> dict[CatalogRow, Quote]:
    models = {str(m.get("id")): m for m in data.get("data") or []}
    quotes: dict[CatalogRow, Quote] = {}
    for row in rows:
        if row.channel != "vercel-ai-gateway":
            continue
        for _, alias in row.aliases:
            model = models.get(alias)
            if model is None:
                continue
            pricing = model.get("pricing") or {}
            rates = {
                "input": _per_token_to_mtok(pricing.get("input")),
                "output": _per_token_to_mtok(pricing.get("output")),
                "cache_read": _per_token_to_mtok(pricing.get("input_cache_read")),
                "cache_write": _per_token_to_mtok(pricing.get("input_cache_write")),
            }
            quotes[row] = Quote("vercel", alias, {k: v for k, v in rates.items() if v is not None})
            break
    return quotes


def _litellm_quotes(data: Mapping[str, Any], rows: list[CatalogRow]) -> dict[CatalogRow, Quote]:
    quotes: dict[CatalogRow, Quote] = {}
    for row in _global_only(rows):
        for provider, alias in row.aliases:
            if provider != "litellm":
                continue
            entry = data.get(alias)
            if not isinstance(entry, Mapping):
                continue
            rates = {
                "input": _per_token_to_mtok(entry.get("input_cost_per_token")),
                "output": _per_token_to_mtok(entry.get("output_cost_per_token")),
                "cache_read": _per_token_to_mtok(entry.get("cache_read_input_token_cost")),
                "cache_write": _per_token_to_mtok(entry.get("cache_creation_input_token_cost")),
            }
            source_channel = LITELLM_PROVIDER_CHANNEL.get(str(entry.get("litellm_provider") or ""))
            quotes[row] = Quote(
                "litellm",
                alias,
                {k: v for k, v in rates.items() if v is not None},
                source_channel if source_channel != row.channel else None,
            )
            break
    return quotes


def _global_only(rows: list[CatalogRow]) -> list[CatalogRow]:
    """Rows a region-blind source can compare: regional rows carry an uplift
    that a one-price-per-model list would flag every day."""
    return [row for row in rows if row.region == "global"]


def _direct_aliases(row: CatalogRow) -> list[str]:
    """Model ids a provider-facing source might use, most specific first."""
    names = [alias for provider, alias in row.aliases if provider not in SYNTHETIC_PROVIDERS]
    tail = row.canonical_id.split("/", 1)[-1]
    if tail not in names:
        names.append(tail)
    return names


def _modelsdev_quotes(data: Mapping[str, Any], rows: list[CatalogRow]) -> dict[CatalogRow, Quote]:
    quotes: dict[CatalogRow, Quote] = {}
    for row in _global_only(rows):
        provider = MODELSDEV_PROVIDER.get(row.channel)
        models = ((data.get(provider) or {}).get("models") or {}) if provider else {}
        if not models:
            continue
        for name in _direct_aliases(row):
            entry = models.get(name)
            if not isinstance(entry, Mapping):
                continue
            cost = entry.get("cost") or {}
            rates = {k: _decimal(cost.get(k)) for k in FIELDS}
            quotes[row] = Quote("modelsdev", f"{provider}/{name}", {k: v for k, v in rates.items() if v is not None})
            break
    return quotes


def _portkey_quotes(
    fetch: Callable[[str], Mapping[str, Any] | None], rows: list[CatalogRow], pause: float = 0.2
) -> dict[CatalogRow, Quote]:
    quotes: dict[CatalogRow, Quote] = {}
    for row in _global_only(rows):
        provider = PORTKEY_PROVIDER.get(row.channel)
        if provider is None:
            continue
        for name in _direct_aliases(row):
            data = fetch(PORTKEY_URL.format(provider=provider, model=name))
            if pause:
                time.sleep(pause)
            if not isinstance(data, Mapping):
                continue
            tier = data.get("pay_as_you_go") or {}
            if not tier:
                continue

            def cents_per_token(key: str) -> Decimal | None:
                price = _decimal((tier.get(key) or {}).get("price"))
                return None if price is None else price * MTOK / Decimal(100)

            rates = {
                "input": cents_per_token("request_token"),
                "output": cents_per_token("response_token"),
                "cache_read": cents_per_token("cache_read_input_token"),
                "cache_write": cents_per_token("cache_write_input_token"),
            }
            quotes[row] = Quote("portkey", f"{provider}/{name}", {k: v for k, v in rates.items() if v is not None})
            break
    return quotes


# --- Comparison --------------------------------------------------------------


@dataclass
class Difference:
    field: str
    ours: Decimal
    theirs: Decimal


@dataclass
class Finding:
    row: CatalogRow
    quotes: dict[str, Quote] = field(default_factory=dict)
    differences: dict[str, list[Difference]] = field(default_factory=dict)

    @property
    def agreeing(self) -> list[str]:
        return [s for s in self.quotes if not self.differences.get(s)]

    @property
    def disagreeing(self) -> list[str]:
        return [s for s in self.quotes if self.differences.get(s)]

    @property
    def strong(self) -> bool:
        """An update signal that needs no further corroboration."""
        authority = AUTHORITATIVE.get(self.row.channel)
        if authority and authority in self.disagreeing:
            return True
        # Two dissenting sources that agree with each other on some field.
        sources = self.disagreeing
        for i, left in enumerate(sources):
            for right in sources[i + 1 :]:
                for diff in self.differences[left]:
                    match = next((d for d in self.differences[right] if d.field == diff.field), None)
                    if match is not None and _close(diff.theirs, match.theirs):
                        return True
        return False

    @property
    def verdict(self) -> str:
        if not self.quotes:
            return "unchecked"
        if self.strong:
            return "update"
        if self.disagreeing:
            return "disputed"
        return "confirmed"


def _close(ours: Decimal, theirs: Decimal) -> bool:
    gap = abs(ours - theirs)
    return gap <= ABSOLUTE_TOLERANCE or gap <= abs(ours) * RELATIVE_TOLERANCE


def compare(rows: list[CatalogRow], quotes_by_source: Mapping[str, Mapping[CatalogRow, Quote]]) -> list[Finding]:
    findings: list[Finding] = []
    for row in rows:
        finding = Finding(row=row)
        for source, quotes in quotes_by_source.items():
            quote = quotes.get(row)
            if quote is None:
                continue
            finding.quotes[source] = quote
            finding.differences[source] = [
                Difference(name, ours, theirs)
                for name in FIELDS
                if (ours := row.rates.get(name)) is not None
                and (theirs := quote.rates.get(name)) is not None
                and not _close(ours, theirs)
            ]
        findings.append(finding)
    return findings


# --- Report ------------------------------------------------------------------


def _money(value: Decimal) -> str:
    text = f"{value.normalize():f}"
    return text if "." in text else text + ".0"


def render_markdown(findings: list[Finding], sources: list[str], today: date) -> str:
    counts = {v: 0 for v in ("update", "disputed", "confirmed", "unchecked")}
    for finding in findings:
        counts[finding.verdict] += 1
    lines = [
        f"# Price check, {today.isoformat()}",
        "",
        f"Sources: {', '.join(sources)}. Rows in effect: {len(findings)}.",
        "",
        f"- update: {counts['update']} (authoritative source disagrees, or two sources agree against us)",
        f"- disputed: {counts['disputed']} (one source disagrees)",
        f"- confirmed: {counts['confirmed']}",
        f"- unchecked: {counts['unchecked']} (no source lists the model on that channel; regional rows are compared only with region-aware sources)",
        "",
    ]
    for verdict, title in (("update", "Needs an update"), ("disputed", "Disputed")):
        rows = [f for f in findings if f.verdict == verdict]
        if not rows:
            continue
        lines += [f"## {title}", "", "| Model | Channel | Field | Ours | Sources |", "| --- | --- | --- | --- | --- |"]
        for finding in rows:
            by_field: dict[str, list[str]] = {}
            for source, diffs in finding.differences.items():
                for diff in diffs:
                    by_field.setdefault(diff.field, []).append(f"{source} {_money(diff.theirs)}")
            agree = finding.agreeing
            for name, values in by_field.items():
                ours = finding.row.rates[name]
                extra = f"; agree: {', '.join(agree)}" if agree else ""
                region = "" if finding.row.region == "global" else f" ({finding.row.region})"
                lines.append(
                    f"| {finding.row.canonical_id} | {finding.row.channel}{region} | {name} | {_money(ours)} | {', '.join(values)}{extra} |"
                )
        lines.append("")
    mismatched = [
        f for f in findings if any(q.source_channel for q in f.quotes.values())
    ]
    if mismatched:
        lines += ["## Keys that point at another channel", ""]
        for finding in mismatched:
            for quote in finding.quotes.values():
                if quote.source_channel:
                    lines.append(
                        f"- {finding.row.canonical_id} on {finding.row.channel}: {quote.source} key `{quote.key}` is filed under {quote.source_channel}"
                    )
        lines.append("")
    unchecked: dict[str, int] = {}
    for finding in findings:
        if finding.verdict == "unchecked":
            unchecked[finding.row.channel] = unchecked.get(finding.row.channel, 0) + 1
    if unchecked:
        lines += ["## Unchecked rows by channel", ""]
        lines += [f"- {channel}: {count}" for channel, count in sorted(unchecked.items())]
        lines.append("")
    return "\n".join(lines)


def to_json(findings: list[Finding], sources: list[str], today: date) -> dict[str, Any]:
    return {
        "checked_at": today.isoformat(),
        "sources": sources,
        "rows": [
            {
                "canonical_id": f.row.canonical_id,
                "channel": f.row.channel,
                "region": f.row.region,
                "effective_from": f.row.effective_from.isoformat(),
                "verdict": f.verdict,
                "ours": {k: _money(v) for k, v in f.row.rates.items()},
                "quotes": {
                    s: {"key": q.key, "rates": {k: _money(v) for k, v in q.rates.items()}, "source_channel": q.source_channel}
                    for s, q in f.quotes.items()
                },
                "differences": {
                    s: [{"field": d.field, "ours": _money(d.ours), "theirs": _money(d.theirs)} for d in diffs]
                    for s, diffs in f.differences.items()
                    if diffs
                },
            }
            for f in findings
        ],
    }


# --- Fetching ----------------------------------------------------------------


def fetch_json(url: str, cache_dir: Path | None, timeout: float = 30.0) -> Mapping[str, Any] | None:
    cache_path = None
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / (url.replace("://", "_").replace("/", "_") + ".json")
        if cache_path.exists():
            return json.loads(cache_path.read_text())
    try:
        with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=timeout) as response:
            body = response.read()
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    except URLError as exc:
        raise SystemExit(f"could not fetch {url}: {exc.reason}") from exc
    data = json.loads(body)
    if cache_path is not None:
        cache_path.write_bytes(body)
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    parser.add_argument("--sources", default="vercel,litellm,modelsdev", help="comma-separated: vercel, litellm, modelsdev, portkey")
    parser.add_argument("--json", type=Path, help="also write the findings as JSON")
    parser.add_argument("--cache-dir", type=Path, help="reuse downloaded source files from this directory")
    parser.add_argument("--today", type=date.fromisoformat, default=datetime.now(timezone.utc).date())
    parser.add_argument("--strict", action="store_true", help="exit 1 when any source disagrees")
    args = parser.parse_args(argv)

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    unknown = set(sources) - {"vercel", "litellm", "modelsdev", "portkey"}
    if unknown:
        parser.error(f"unknown sources: {', '.join(sorted(unknown))}")

    document = yaml.safe_load(args.catalog.read_text())
    rows = effective_rows(document, args.today)

    quotes_by_source: dict[str, Mapping[CatalogRow, Quote]] = {}
    if "vercel" in sources:
        quotes_by_source["vercel"] = _vercel_quotes(fetch_json(VERCEL_URL, args.cache_dir) or {}, rows)
    if "litellm" in sources:
        quotes_by_source["litellm"] = _litellm_quotes(fetch_json(LITELLM_URL, args.cache_dir) or {}, rows)
    if "modelsdev" in sources:
        quotes_by_source["modelsdev"] = _modelsdev_quotes(fetch_json(MODELSDEV_URL, args.cache_dir) or {}, rows)
    if "portkey" in sources:
        quotes_by_source["portkey"] = _portkey_quotes(lambda url: fetch_json(url, args.cache_dir), rows)

    findings = compare(rows, quotes_by_source)
    print(render_markdown(findings, sources, args.today))
    if args.json:
        args.json.write_text(json.dumps(to_json(findings, sources, args.today), indent=2) + "\n")

    if any(f.verdict == "update" for f in findings):
        return 2
    if args.strict and any(f.verdict == "disputed" for f in findings):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
