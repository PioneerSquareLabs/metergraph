"""Compare the catalog rows in effect today with public price sources.

Each row is graded: confirmed, disputed, update or unchecked. Where the
provider's own price list is readable it decides: it agrees (confirmed) or it
does not (update). Elsewhere one dissenting second opinion is disputed and
two that agree with each other is an update. The script reads prices only
and never edits the catalog.

Provider sources, read from the provider's own page: ``openai``,
``anthropic``, ``google`` (Gemini API), ``deepseek``, ``xai`` and
``perplexity``; ``vercel`` is the AI Gateway's own list. Second opinions,
compared only where no provider price is readable: ``litellm`` (matched
through ``provider: litellm`` aliases), ``modelsdev`` (matched by provider
and model id) and ``portkey`` (one request per model, so opt-in). None needs
credentials. Each provider page also yields the models it lists that the
catalog lacks.

Usage:

    python scripts/check_prices.py [--json report.json] [--cache-dir DIR]
    python scripts/check_prices.py --sources openai,anthropic,google,deepseek,xai,perplexity,vercel,litellm,modelsdev,portkey

Exit status: 0 nothing to do, 2 an update signal, 1 with ``--strict`` when any
source disagrees.
"""

from __future__ import annotations

import argparse
import html
import json
import re
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
AUTHORITATIVE = {
    "openai-api": "openai",
    "anthropic-api": "anthropic",
    "google-api": "google",
    "deepseek-api": "deepseek",
    "xai-api": "xai",
    "perplexity-api": "perplexity",
    "vercel-ai-gateway": "vercel",
}
PROVIDER_SOURCES = ("openai", "anthropic", "google", "deepseek", "xai", "perplexity", "vercel")
SECOND_OPINIONS = ("litellm", "modelsdev", "portkey")
ALL_SOURCES = PROVIDER_SOURCES + SECOND_OPINIONS
DEFAULT_SOURCES = ",".join(PROVIDER_SOURCES + ("litellm", "modelsdev"))

# The catalog files LiteLLM map keys under provider `litellm` and the
# provider-less fallback spellings under `unknown`. Neither is a provider
# whose page can be read, so they never select a page lookup name.
ALIAS_ONLY_PROVIDERS = {"litellm", "unknown"}

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
OPENAI_URL = "https://developers.openai.com/api/docs/pricing"
ANTHROPIC_URL = "https://platform.claude.com/docs/en/about-claude/pricing"
GOOGLE_URL = "https://ai.google.dev/gemini-api/docs/pricing"
DEEPSEEK_URL = "https://api-docs.deepseek.com/quick_start/pricing"
XAI_URL = "https://docs.x.ai/developers/models"
PERPLEXITY_URL = "https://docs.perplexity.ai/docs/getting-started/pricing"
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
            quotes[row] = _closest_variant(row, "vercel", alias, _vercel_variants(pricing))
            break
    return quotes


def _vercel_rates(pricing: Mapping[str, Any], multiplier: Decimal = Decimal(1)) -> dict[str, Decimal]:
    rates = {
        "input": _per_token_to_mtok(pricing.get("input")),
        "output": _per_token_to_mtok(pricing.get("output")),
        "cache_read": _per_token_to_mtok(pricing.get("input_cache_read")),
        "cache_write": _per_token_to_mtok(pricing.get("input_cache_write")),
    }
    return {k: v * multiplier for k, v in rates.items() if v is not None}


def _vercel_variants(pricing: Mapping[str, Any]) -> list[tuple[str, dict[str, Decimal]]]:
    """The gateway's base price and its regional and peak-hour variants, which
    a catalog row may legitimately carry instead of the base."""
    variants = [("", _vercel_rates(pricing))]
    for region, regional in (pricing.get("regional") or {}).items():
        if isinstance(regional, Mapping):
            variants.append((f" regional.{region}", _vercel_rates(regional)))
    multiplier = _decimal((pricing.get("peak_pricing") or {}).get("multiplier"))
    if multiplier:
        variants.append((" peak", _vercel_rates(pricing, multiplier)))
    return variants


def _closest_variant(row: CatalogRow, source: str, key: str, variants: list[tuple[str, dict[str, Decimal]]]) -> Quote:
    """Quote the variant with the fewest differences from the row."""
    def differences(rates: Mapping[str, Decimal]) -> int:
        return sum(
            1 for name in FIELDS
            if (ours := row.rates.get(name)) is not None
            and (theirs := rates.get(name)) is not None
            and not _close(ours, theirs)
        )
    label, rates = min(variants, key=lambda v: differences(v[1]))
    return Quote(source, key + label, rates)


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
    names = [alias for provider, alias in row.aliases if provider not in ALIAS_ONLY_PROVIDERS]
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


def _page_text(html_text: str) -> str:
    """A page as one line of cell-separated text, for pages that are prose."""
    text = re.sub(r"<script.*?</script>|<style.*?</style>", "", html_text, flags=re.S)
    text = re.sub(r"<[^>]+>", " | ", text)
    text = html.unescape(re.sub(r"\s+", " ", text))
    return re.sub(r"(\s*\|\s*)+", " | ", text)


def _page_tables(html_text: str) -> list[list[list[str]]]:
    """Every HTML table as rows of cell text."""
    tables = []
    for table in re.findall(r"<table.*?</table>", html_text, flags=re.S):
        rows = []
        for row in re.findall(r"<tr.*?</tr>", table, flags=re.S):
            cells = re.findall(r"<t[hd].*?</t[hd]>", row, flags=re.S)
            rows.append([html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in cells])
        tables.append(rows)
    return tables


def _dollars(text: str) -> Decimal | None:
    """The first dollar amount in a cell, e.g. "$0.75 (text / image)"."""
    match = re.search(r"\$([0-9][0-9,]*\.?[0-9]*)", text)
    return _decimal(match.group(1).replace(",", "")) if match else None


def _rates_for(row: CatalogRow, lookup: Callable[[str], Mapping[str, Decimal] | None]) -> tuple[str, Mapping[str, Decimal]] | None:
    for name in _direct_aliases(row):
        rates = lookup(name)
        if rates:
            return name, rates
    return None


# Provider pages. Each parser turns a page into {model name: rates}; the
# matcher then pairs catalog rows on the page's channel with those names.

# OpenAI's page carries its tables as data. In the Standard section a row is
# input, cached input, output, with a cache-write column on newer models.
_OPENAI_ROW = re.compile(r'\[\[0,"([^"]+)"\]((?:,\[0,(?:[0-9.]+|"-")\]){3,4})\]')
_OPENAI_CELL = re.compile(r'\[0,([0-9.]+|"-")\]')


def _parse_openai(html_text: str) -> dict[str, dict[str, Decimal]]:
    text = html.unescape(html_text)
    # The page repeats the "standard" label (text, image and audio tables);
    # the text-model table is the standard section with the most gpt rows.
    sections = []
    for match in re.finditer(r'\[0,"standard"\]', text):
        end = text.find('[0,"batch"]', match.end())
        if end > match.end():
            sections.append(text[match.end():end])
    section = max(sections, key=lambda sec: sum(n.startswith("gpt") for n, _ in _OPENAI_ROW.findall(sec)), default=text)
    table: dict[str, dict[str, Decimal]] = {}
    for name, cells in _OPENAI_ROW.findall(section):
        key = re.sub(r"\s*\(.*\)$", "", name).strip().lower()
        if key in table:
            continue
        values = [_decimal(v) for v in _OPENAI_CELL.findall(cells)]
        names = ("input", "cache_read", "cache_write", "output") if len(values) == 4 else ("input", "cache_read", "output")
        table[key] = {k: v for k, v in zip(names, values) if v is not None}
    return table


def _parse_anthropic(html_text: str) -> dict[str, dict[str, Decimal]]:
    """The base-token table: name, input, output, 5m write, 1h write, hits.
    Keys are model ids such as claude-opus-5-5."""
    table: dict[str, dict[str, Decimal]] = {}
    for rows_ in _page_tables(html_text):
        if not rows_ or "Base tokens" not in " ".join(rows_[0]):
            continue
        for cells in rows_[2:]:
            if len(cells) < 6:
                continue
            match = re.match(r"(Claude [A-Za-z]+ [0-9.]+)", cells[0])
            if not match:
                continue
            key = match.group(1).lower().replace(" ", "-").replace(".", "-")
            rates = {"input": _dollars(cells[1]), "output": _dollars(cells[2]), "cache_write": _dollars(cells[3]), "cache_read": _dollars(cells[5])}
            table.setdefault(key, {k: v for k, v in rates.items() if v is not None})
        break
    return table


def _parse_google(html_text: str) -> dict[str, dict[str, Decimal]]:
    """The Gemini API page: each model's id precedes its "Try it" link, and
    its paid-tier prices follow. Where a price is dated the first amount is
    the one in effect; per-image output is not a token rate."""
    parts = _page_text(html_text).split("Try it in Google AI Studio")
    tier = r"(?:Free of charge \| |Not available \| )?([^|]*\$[^|]*)"
    table: dict[str, dict[str, Decimal]] = {}
    for before, block in zip(parts, parts[1:]):
        cells = [c.strip() for c in before.split("|") if c.strip()]
        if not cells or not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", cells[-1]):
            continue
        inp = re.search(r"Input price \| " + tier, block)
        out = re.search(r"Output price[^|]*\| " + tier, block)
        if not (inp and out):
            continue
        cache = re.search(r"Context caching price \| " + tier, block)
        output = None if "per image" in out.group(1) else _dollars(out.group(1))
        rates = {"input": _dollars(inp.group(1)), "output": output, "cache_read": _dollars(cache.group(1)) if cache else None}
        table.setdefault(cells[-1], {k: v for k, v in rates.items() if v is not None})
    return table


def _deepseek_name(name: str) -> str:
    # The page names the current Flash "deepseek-flash" and keeps the
    # versioned ids as legacy names for it.
    return re.sub(r"\(\d+\)", "", name).strip().lower().replace("deepseek-v4.1-", "deepseek-").replace("deepseek-v4-", "deepseek-")


def _parse_deepseek(html_text: str) -> dict[str, dict[str, Decimal]]:
    """The pricing table, one column per model; the catalog carries the peak
    rate and an off-peak rule, so peak rows are read."""
    columns: list[str] = []
    table: dict[str, dict[str, Decimal]] = {}
    section = ""
    for rows_ in _page_tables(html_text):
        for cells in rows_:
            if cells and cells[0].upper().startswith("MODEL") and len(cells) > 1:
                columns = [_deepseek_name(c) for c in cells[1:]]
                for column in columns:
                    table[column] = {}
                continue
            if not columns:
                continue
            label = " ".join(cells).upper()
            if "CACHE HIT" in label:
                section = "cache_read"
            elif "CACHE MISS" in label:
                section = "input"
            elif "OUTPUT" in label:
                section = "output"
            if section and any(c.upper() == "PEAK" for c in cells):
                values = [c for c in cells if c.startswith("$")]
                for column, value in zip(columns, values):
                    rate = _dollars(value)
                    if rate is not None:
                        table[column][section] = rate
    return table


# xAI's models page embeds each model as JSON with prices in units of
# $0.0000000001 per token, so 20000 is $2 per million.
_XAI_MODEL = re.compile(r'"name":"([^"]+)","version"')
_XAI_UNIT = Decimal("0.0001")


def _parse_xai(html_text: str) -> dict[str, dict[str, Decimal]]:
    text = html.unescape(html_text)
    table: dict[str, dict[str, Decimal]] = {}
    for match in _XAI_MODEL.finditer(text):
        name = match.group(1).lower()
        if name in table:
            continue
        body = text[match.end(): match.end() + 1500]

        def price(key: str) -> Decimal | None:
            found = re.search(r'"' + key + r'":"?([0-9.]+)"?', body)
            return _decimal(found.group(1)) * _XAI_UNIT if found else None

        rates = {"input": price("promptTextTokenPrice"), "cache_read": price("cachedPromptTokenPrice"), "output": price("completionTextTokenPrice")}
        if rates["input"] is not None and rates["output"] is not None:
            table[name] = {k: v for k, v in rates.items() if v is not None}
    return table


def _parse_perplexity(html_text: str) -> dict[str, dict[str, Decimal]]:
    """The page's cost calculator carries a PRICING object, which the page
    calls its single source of truth; its sonar models give token rates."""
    text = html.unescape(html_text)
    start = text.find("const PRICING=")
    end = text.find("};const PricingCalculator", start)
    if start < 0 or end < 0:
        return {}
    raw = text[start + len("const PRICING="): end + 1]
    raw = raw.replace('\\"', '"').replace("\\u0026", "&").replace("\\n", " ")
    raw = re.sub(r"`([^`]*)`", lambda m: json.dumps(m.group(1)), raw)
    raw = re.sub(r"(?<=[:\[,])\s*\.(\d)", r"0.\1", raw)
    try:
        pricing = json.loads(raw)
    except ValueError:
        return {}
    table: dict[str, dict[str, Decimal]] = {}
    for model in (pricing.get("sonar") or {}).get("models") or []:
        rates = {"input": _decimal(model.get("input")), "output": _decimal(model.get("output"))}
        if rates["input"] is not None:
            table[str(model.get("id")).lower()] = {k: v for k, v in rates.items() if v is not None}
    return table


PROVIDER_PAGES: dict[str, tuple[str, str, Callable[[str], dict[str, dict[str, Decimal]]]]] = {
    "openai": (OPENAI_URL, "openai-api", _parse_openai),
    "anthropic": (ANTHROPIC_URL, "anthropic-api", _parse_anthropic),
    "google": (GOOGLE_URL, "google-api", _parse_google),
    "deepseek": (DEEPSEEK_URL, "deepseek-api", _parse_deepseek),
    "xai": (XAI_URL, "xai-api", _parse_xai),
    "perplexity": (PERPLEXITY_URL, "perplexity-api", _parse_perplexity),
}


def _page_key(source: str, name: str) -> str:
    """A catalog alias in the spelling the provider page uses."""
    name = name.lower().split("/")[-1]
    if source == "google":
        name = name.removeprefix("models/")
    if source == "deepseek":
        name = _deepseek_name(name)
    return name


def _page_match(source: str, table: Mapping[str, Mapping[str, Decimal]], name: str) -> Mapping[str, Decimal] | None:
    key = _page_key(source, name)
    if source == "anthropic":
        # Ids match exactly or with a date suffix; a variant such as
        # claude-opus-5-fast never inherits the base model's price.
        return next((r for k, r in table.items() if re.fullmatch(re.escape(k) + r"(-\d{8})?", key)), None)
    return table.get(key)


def _page_quotes(source: str, table: Mapping[str, Mapping[str, Decimal]], rows: list[CatalogRow]) -> dict[CatalogRow, Quote]:
    channel = PROVIDER_PAGES[source][1]
    quotes: dict[CatalogRow, Quote] = {}
    for row in _global_only(rows):
        if row.channel != channel:
            continue
        found = _rates_for(row, lambda name: _page_match(source, table, name))
        if found:
            quotes[row] = Quote(source, found[0], found[1])
    return quotes


def _missing_models(source: str, table: Mapping[str, Mapping[str, Decimal]], rows: list[CatalogRow]) -> list[str]:
    """Model names the provider page lists that no catalog alias on that
    channel spells."""
    channel = PROVIDER_PAGES[source][1]
    known = {_page_key(source, alias) for row in rows if row.channel == channel for _, alias in row.aliases}
    known |= {_page_key(source, row.canonical_id) for row in rows if row.channel == channel}
    if source == "anthropic":
        return sorted(k for k in table if not any(re.fullmatch(re.escape(k) + r"(-\d{8})?", n) for n in known))
    return sorted(k for k in table if k not in known)


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
    def authority(self) -> str | None:
        """The provider's own source, when it quoted this row."""
        source = AUTHORITATIVE.get(self.row.channel)
        return source if source in self.quotes else None

    @property
    def strong(self) -> bool:
        """An update signal that needs no further corroboration."""
        if self.authority:
            return self.authority in self.disagreeing
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
        if self.authority:
            return "confirmed"
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
        authority = AUTHORITATIVE.get(row.channel)
        provider_quoted = authority in quotes_by_source and row in quotes_by_source[authority]
        for source, quotes in quotes_by_source.items():
            quote = quotes.get(row)
            if quote is None:
                continue
            # Second opinions are compared only where no provider price is
            # readable; otherwise they only lend the catalog model names.
            if provider_quoted and source != authority:
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


def render_markdown(findings: list[Finding], sources: list[str], today: date, missing: Mapping[str, list[str]] | None = None) -> str:
    counts = {v: 0 for v in ("update", "disputed", "confirmed", "unchecked")}
    for finding in findings:
        counts[finding.verdict] += 1
    lines = [
        f"# Price check, {today.isoformat()}",
        "",
        f"Sources: {', '.join(sources)}. Rows in effect: {len(findings)}.",
        "",
        f"- update: {counts['update']} (the provider's own price disagrees, or, where none is readable, two second opinions agree against us)",
        f"- disputed: {counts['disputed']} (no provider price is readable and a second opinion disagrees)",
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
            ordered = sorted(finding.differences, key=lambda s: s != finding.authority)
            for source in ordered:
                for diff in finding.differences[source]:
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
    if missing:
        lines += ["## Models the provider lists that the catalog does not", ""]
        for source, names in sorted(missing.items()):
            if names:
                shown = ", ".join(f"`{n}`" for n in names[:40]) + (f" and {len(names) - 40} more" if len(names) > 40 else "")
                lines.append(f"- {source} ({len(names)}): {shown}")
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


def to_json(findings: list[Finding], sources: list[str], today: date, missing: Mapping[str, list[str]] | None = None) -> dict[str, Any]:
    return {
        "checked_at": today.isoformat(),
        "sources": sources,
        "missing_models": dict(missing or {}),
        "rows": [
            {
                "canonical_id": f.row.canonical_id,
                "channel": f.row.channel,
                "region": f.row.region,
                "effective_from": f.row.effective_from.isoformat(),
                "verdict": f.verdict,
                "authority": f.authority,
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


def fetch_text(url: str, cache_dir: Path | None, timeout: float = 30.0) -> str:
    cache_path = None
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / (url.replace("://", "_").replace("/", "_") + ".html")
        if cache_path.exists():
            return cache_path.read_text(errors="ignore")
    try:
        with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=timeout) as response:
            body = response.read()
    except (HTTPError, URLError) as exc:
        raise SystemExit(f"could not fetch {url}: {exc}") from exc
    if cache_path is not None:
        cache_path.write_bytes(body)
    return body.decode("utf-8", errors="ignore")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    parser.add_argument("--sources", default=DEFAULT_SOURCES, help="comma-separated subset of " + ", ".join(ALL_SOURCES))
    parser.add_argument("--json", type=Path, help="also write the findings as JSON")
    parser.add_argument("--cache-dir", type=Path, help="reuse downloaded source files from this directory")
    parser.add_argument("--today", type=date.fromisoformat, default=datetime.now(timezone.utc).date())
    parser.add_argument("--strict", action="store_true", help="exit 1 when any source disagrees")
    args = parser.parse_args(argv)

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    unknown = set(sources) - set(ALL_SOURCES)
    if unknown:
        parser.error(f"unknown sources: {', '.join(sorted(unknown))}")

    document = yaml.safe_load(args.catalog.read_text())
    rows = effective_rows(document, args.today)

    quotes_by_source: dict[str, Mapping[CatalogRow, Quote]] = {}
    missing: dict[str, list[str]] = {}
    for source, (url, _, parser) in PROVIDER_PAGES.items():
        if source in sources:
            table = parser(fetch_text(url, args.cache_dir))
            quotes_by_source[source] = _page_quotes(source, table, rows)
            missing[source] = _missing_models(source, table, rows)
    if "vercel" in sources:
        quotes_by_source["vercel"] = _vercel_quotes(fetch_json(VERCEL_URL, args.cache_dir) or {}, rows)
    if "litellm" in sources:
        quotes_by_source["litellm"] = _litellm_quotes(fetch_json(LITELLM_URL, args.cache_dir) or {}, rows)
    if "modelsdev" in sources:
        quotes_by_source["modelsdev"] = _modelsdev_quotes(fetch_json(MODELSDEV_URL, args.cache_dir) or {}, rows)
    if "portkey" in sources:
        quotes_by_source["portkey"] = _portkey_quotes(lambda url: fetch_json(url, args.cache_dir), rows)

    findings = compare(rows, quotes_by_source)
    print(render_markdown(findings, sources, args.today, missing))
    if args.json:
        args.json.write_text(json.dumps(to_json(findings, sources, args.today, missing), indent=2) + "\n")

    if any(f.verdict == "update" for f in findings):
        return 2
    if args.strict and any(f.verdict == "disputed" for f in findings):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
