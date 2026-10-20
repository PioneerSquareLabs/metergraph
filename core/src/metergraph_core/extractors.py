"""Deterministic extractors for official pricing pages.

Every function here takes source content the caller already has and returns
data; nothing fetches, reads files, or writes. An extractor is selected by
the parser id a source registry entry names.

Official parsers (OpenAI, Anthropic, Google, DeepSeek, Mistral, xAI and
Perplexity) turn a page into ``{model name: {field: Decimal}}`` tables keyed
by the page's own model spelling; :func:`extract` maps those to catalog
price columns. Discovery-only parsers (Fireworks, Z.AI, AWS Bedrock, Azure
Foundry and NVIDIA) record normalized evidence for a reviewer and never
produce applicable price rows. NVIDIA's evidence is GPU or license pricing,
never token rates.

:func:`structural_fingerprint` summarizes only the page structure a parser
relies on -- labels, headers and key paths -- never prices or the set of
models, so a price change or a newly listed model keeps the fingerprint
while a format change alters it.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Callable, Mapping

# Parser field -> catalog price column. An official page's single
# cache-write rate is the base (5 minute) write the catalog stores.
FIELD_COLUMNS = {
    "input": "input_per_mtok",
    "output": "output_per_mtok",
    "cache_read": "cache_read_per_mtok",
    "cache_write": "cache_write_5m_per_mtok",
}

Table = dict[str, dict[str, Decimal]]


@dataclass(frozen=True, slots=True)
class ExtractedRate:
    # The model as the page spells it, in page_key form.
    model_key: str
    region: str
    # Catalog price column -> rate per million tokens.
    rates: Mapping[str, Decimal]


@dataclass(frozen=True, slots=True)
class Extraction:
    parser: str
    fingerprint: str
    # Sorted by (model_key, region). Empty for discovery-only parsers.
    rates: tuple[ExtractedRate, ...]
    # Whether rates may become catalog rows at all.
    applicable: bool
    # Normalized, deterministic evidence for a reviewer.
    evidence: dict[str, Any]
    # Model keys the page gives two different candidate values.
    ambiguities: tuple[str, ...] = ()


# --- Shared page helpers ------------------------------------------------------


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except ArithmeticError:
        return None


def page_text(html_text: str) -> str:
    """A page as one line of cell-separated text, for pages that are prose."""
    text = re.sub(r"<script.*?</script>|<style.*?</style>", "", html_text, flags=re.S)
    text = re.sub(r"<[^>]+>", " | ", text)
    text = html.unescape(re.sub(r"\s+", " ", text))
    return re.sub(r"(\s*\|\s*)+", " | ", text)


def page_tables(html_text: str) -> list[list[list[str]]]:
    """Every HTML table as rows of cell text."""
    tables = []
    for table in re.findall(r"<table.*?</table>", html_text, flags=re.S):
        rows = []
        for row in re.findall(r"<tr.*?</tr>", table, flags=re.S):
            cells = re.findall(r"<t[hd].*?</t[hd]>", row, flags=re.S)
            rows.append([html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in cells])
        tables.append(rows)
    return tables


def dollars(text: str) -> Decimal | None:
    """The first dollar amount in a cell, e.g. "$0.75 (text / image)"."""
    match = re.search(r"\$([0-9][0-9,]*\.?[0-9]*)", text)
    return _decimal(match.group(1).replace(",", "")) if match else None


def regional_key(name: str, region: str) -> str:
    """A page table key for a regional price: the global key is bare."""
    return name if region == "global" else f"{name}@{region}"


def _keep_first(table: Table, key: str, rates: dict[str, Decimal], duplicates: list[str] | None) -> None:
    """Keep the first candidate for ``key``; note a differing second one."""
    if key in table:
        if duplicates is not None and table[key] != rates:
            duplicates.append(key)
        return
    table[key] = rates


# --- Official parsers ---------------------------------------------------------

# OpenAI's page carries its tables as data. In the Standard section a row is
# input, cached input, output, with a cache-write column on newer models.
OPENAI_ROW = re.compile(r'\[\[0,"([^"]+)"\]((?:,\[0,(?:[0-9.]+|"-")\]){3,4})\]')
OPENAI_CELL = re.compile(r'\[0,([0-9.]+|"-")\]')


def _openai_sections(text: str) -> list[str]:
    sections = []
    for match in re.finditer(r'\[0,"standard"\]', text):
        end = text.find('[0,"batch"]', match.end())
        if end > match.end():
            sections.append(text[match.end():end])
    return sections


def _openai_section(text: str) -> str:
    # The page repeats the "standard" label (text, image and audio tables);
    # the text-model table is the standard section with the most gpt rows.
    return max(
        _openai_sections(text),
        key=lambda sec: sum(n.startswith("gpt") for n, _ in OPENAI_ROW.findall(sec)),
        default=text,
    )


def parse_openai(html_text: str, *, duplicates: list[str] | None = None) -> Table:
    section = _openai_section(html.unescape(html_text))
    table: Table = {}
    for name, cells in OPENAI_ROW.findall(section):
        key = re.sub(r"\s*\(.*\)$", "", name).strip().lower()
        values = [_decimal(v) for v in OPENAI_CELL.findall(cells)]
        names = ("input", "cache_read", "cache_write", "output") if len(values) == 4 else ("input", "cache_read", "output")
        _keep_first(table, key, {k: v for k, v in zip(names, values) if v is not None}, duplicates)
    return table


def _anthropic_base_table(html_text: str) -> list[list[str]] | None:
    for rows_ in page_tables(html_text):
        if rows_ and "Base tokens" in " ".join(rows_[0]):
            return rows_
    return None


def parse_anthropic(html_text: str, *, duplicates: list[str] | None = None) -> Table:
    """The base-token table: name, input, output, 5m write, 1h write, hits.
    Keys are model ids such as claude-opus-5-5."""
    table: Table = {}
    rows_ = _anthropic_base_table(html_text) or []
    for cells in rows_[2:]:
        if len(cells) < 6:
            continue
        match = re.match(r"(Claude [A-Za-z]+ [0-9.]+)", cells[0])
        if not match:
            continue
        key = match.group(1).lower().replace(" ", "-").replace(".", "-")
        rates = {"input": dollars(cells[1]), "output": dollars(cells[2]), "cache_write": dollars(cells[3]), "cache_read": dollars(cells[5])}
        _keep_first(table, key, {k: v for k, v in rates.items() if v is not None}, duplicates)
    return table


_GOOGLE_LABELS = ("Input price", "Output price", "Context caching price")


def parse_google(html_text: str, *, duplicates: list[str] | None = None) -> Table:
    """The Gemini API page: each model's id precedes its "Try it" link, and
    its paid-tier prices follow. Where a price is dated the first amount is
    the one in effect; per-image output is not a token rate."""
    parts = page_text(html_text).split("Try it in Google AI Studio")
    tier = r"(?:Free of charge \| |Not available \| )?([^|]*\$[^|]*)"
    table: Table = {}
    for before, block in zip(parts, parts[1:]):
        cells = [c.strip() for c in before.split("|") if c.strip()]
        if not cells or not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", cells[-1]):
            continue
        inp = re.search(r"Input price \| " + tier, block)
        out = re.search(r"Output price[^|]*\| " + tier, block)
        if not (inp and out):
            continue
        cache = re.search(r"Context caching price \| " + tier, block)
        output = None if "per image" in out.group(1) else dollars(out.group(1))
        rates = {"input": dollars(inp.group(1)), "output": output, "cache_read": dollars(cache.group(1)) if cache else None}
        _keep_first(table, cells[-1], {k: v for k, v in rates.items() if v is not None}, duplicates)
    return table


def deepseek_name(name: str) -> str:
    # The page names the current Flash "deepseek-flash" and keeps the
    # versioned ids as legacy names for it.
    return re.sub(r"\(\d+\)", "", name).strip().lower().replace("deepseek-v4.1-", "deepseek-").replace("deepseek-v4-", "deepseek-")


def parse_deepseek(html_text: str, *, duplicates: list[str] | None = None) -> Table:
    """The pricing table, one column per model; the catalog carries the peak
    rate and an off-peak rule, so peak rows are read."""
    columns: list[str] = []
    table: Table = {}
    section = ""
    for rows_ in page_tables(html_text):
        for cells in rows_:
            if cells and cells[0].upper().startswith("MODEL") and len(cells) > 1:
                columns = [deepseek_name(c) for c in cells[1:]]
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
                    rate = dollars(value)
                    if rate is not None:
                        if duplicates is not None and table[column].get(section, rate) != rate:
                            duplicates.append(column)
                        table[column][section] = rate
    return table


_MISTRAL_FIELDS = {"input": "input", "cached input": "cache_read", "output": "output"}


def _mistral_block(html_text: str) -> str | None:
    text = html.unescape(html_text).replace(r'\"', '"')
    if "mistral-large-4" not in text:
        return None
    start = text.find('"pricing":{"type":"custom"')
    return text[start : start + 2000] if start >= 0 else None


def parse_mistral(html_text: str, *, duplicates: list[str] | None = None) -> Table:
    """Mistral Large 4's model card embeds the active price and, during a
    sale, the original price. Read the active ``price`` values by label."""
    block = _mistral_block(html_text)
    if block is None:
        return {}
    rates: dict[str, Decimal] = {}
    for price, label in re.findall(r'"price":([0-9.]+).*?"label":"([^"]+)"', block):
        field = _MISTRAL_FIELDS.get(label.lower())
        rate = _decimal(price)
        if field is not None and rate is not None:
            if duplicates is not None and rates.get(field, rate) != rate:
                duplicates.append("mistral-large-4")
            rates.setdefault(field, rate)
    return {"mistral-large-4": rates} if "input" in rates and "output" in rates else {}


# xAI's models page embeds each model once per cluster as JSON, with prices
# in units of $0.0000000001 per token, so 20000 is $2 per million. The
# us-central-1 cluster is the US regional endpoint, priced 10% above the
# global endpoint; the catalog carries it as region "us".
XAI_MODEL = re.compile(r'"name":"([^"]+)","version"')
XAI_UNIT = Decimal("0.0001")
XAI_CLUSTER_REGION = {"us-central-1": "us"}
_XAI_KEYS = ("promptTextTokenPrice", "cachedPromptTokenPrice", "completionTextTokenPrice", "cluster")


def parse_xai(html_text: str, *, duplicates: list[str] | None = None) -> Table:
    text = html.unescape(html_text)
    table: Table = {}
    for match in XAI_MODEL.finditer(text):
        body = text[match.end(): match.end() + 1500]
        cluster = re.search(r'"cluster":"([^"]+)"', body)
        key = regional_key(match.group(1).lower(), XAI_CLUSTER_REGION.get(cluster.group(1) if cluster else "", "global"))

        def price(field: str) -> Decimal | None:
            found = re.search(r'"' + field + r'":"?([0-9.]+)"?', body)
            return _decimal(found.group(1)) * XAI_UNIT if found else None

        rates = {"input": price("promptTextTokenPrice"), "cache_read": price("cachedPromptTokenPrice"), "output": price("completionTextTokenPrice")}
        if rates["input"] is not None and rates["output"] is not None:
            _keep_first(table, key, {k: v for k, v in rates.items() if v is not None}, duplicates)
    return table


def _perplexity_pricing(html_text: str) -> Mapping[str, Any] | None:
    text = html.unescape(html_text)
    start = text.find("const PRICING=")
    end = text.find("};const PricingCalculator", start)
    if start < 0 or end < 0:
        return None
    raw = text[start + len("const PRICING="): end + 1]
    raw = raw.replace('\\"', '"').replace("\\u0026", "&").replace("\\n", " ")
    raw = re.sub(r"`([^`]*)`", lambda m: json.dumps(m.group(1)), raw)
    raw = re.sub(r"(?<=[:\[,])\s*\.(\d)", r"0.\1", raw)
    try:
        pricing = json.loads(raw)
    except ValueError:
        return None
    return pricing if isinstance(pricing, dict) else None


def parse_perplexity(html_text: str, *, duplicates: list[str] | None = None) -> Table:
    """The page's cost calculator carries a PRICING object, which the page
    calls its single source of truth; its sonar models give token rates."""
    pricing = _perplexity_pricing(html_text)
    if pricing is None:
        return {}
    table: Table = {}
    for model in (pricing.get("sonar") or {}).get("models") or []:
        rates = {"input": _decimal(model.get("input")), "output": _decimal(model.get("output"))}
        if rates["input"] is not None:
            key = str(model.get("id")).lower()
            rates = {k: v for k, v in rates.items() if v is not None}
            if duplicates is not None and table.get(key, rates) != rates:
                duplicates.append(key)
            table[key] = rates
    return table


# --- Matching catalog names to page keys -------------------------------------


def page_key(parser_id: str, name: str) -> str:
    """A catalog alias in the spelling the provider page uses."""
    name = name.lower().split("/")[-1]
    if parser_id == "google":
        name = name.removeprefix("models/")
    if parser_id == "deepseek":
        name = deepseek_name(name)
    return name


def page_match(
    parser_id: str,
    table: Mapping[str, Mapping[str, Decimal]],
    name: str,
    region: str = "global",
) -> Mapping[str, Decimal] | None:
    key = regional_key(page_key(parser_id, name), region)
    if parser_id == "anthropic":
        # Ids match exactly or with a date suffix; a variant such as
        # claude-opus-5-fast never inherits the base model's price.
        return next((r for k, r in table.items() if re.fullmatch(re.escape(k) + r"(-\d{8})?", key)), None)
    return table.get(key)


# --- Structural features -------------------------------------------------------

_AMOUNT = re.compile(r"\$[0-9][0-9,]*\.?[0-9]*")


def _openai_features(content: str) -> Any:
    text = html.unescape(content)
    rows = OPENAI_ROW.findall(_openai_section(text))
    return {
        "standard_label": '[0,"standard"]' in text,
        "batch_label": '[0,"batch"]' in text,
        "text_section": bool(_openai_sections(text)),
        "row_widths": sorted({len(OPENAI_CELL.findall(cells)) for _, cells in rows}),
    }


def _anthropic_features(content: str) -> Any:
    rows_ = _anthropic_base_table(content)
    return {"base_table": rows_ is not None, "headers": rows_[:2] if rows_ else []}


def _google_features(content: str) -> Any:
    text = page_text(content)
    return {
        "try_it": "Try it in Google AI Studio" in text,
        "labels": [label in text for label in _GOOGLE_LABELS],
    }


def _deepseek_features(content: str) -> Any:
    labels = " ".join(
        " ".join(cells) for rows_ in page_tables(content) for cells in rows_
    ).upper()
    return {
        "model_header": any(
            cells and cells[0].upper().startswith("MODEL")
            for rows_ in page_tables(content)
            for cells in rows_
        ),
        "labels": [
            marker in labels
            for marker in ("1M INPUT TOKENS", "CACHE HIT", "CACHE MISS", "1M OUTPUT TOKENS", "OFF-PEAK", "PEAK")
        ],
    }


def _mistral_features(content: str) -> Any:
    block = _mistral_block(content)
    return {
        "custom_pricing": block is not None,
        "labels": sorted(set(re.findall(r'"label":"([^"]+)"', block or ""))),
        "original_price": '"originalPrice"' in (block or ""),
    }


def _xai_features(content: str) -> Any:
    text = html.unescape(content)
    return {
        "model_objects": XAI_MODEL.search(text) is not None,
        "keys": [f'"{key}"' in text for key in _XAI_KEYS],
    }


def _perplexity_features(content: str) -> Any:
    pricing = _perplexity_pricing(content)
    models = ((pricing or {}).get("sonar") or {}).get("models")
    return {
        "pricing_object": pricing is not None,
        "sonar_models": isinstance(models, list),
        "model_keys": sorted({key for model in models or [] if isinstance(model, dict) for key in model}),
    }


# --- Discovery-only evidence --------------------------------------------------


def _table_evidence(content: str) -> dict[str, Any]:
    return {
        "kind": "tables",
        "tables": [
            {"headers": rows_[0], "rows": rows_[1:]}
            for rows_ in page_tables(content)
            if rows_
        ],
    }


def _table_features(content: str) -> Any:
    headers = {
        tuple(_AMOUNT.sub("$#", cell) for cell in rows_[0])
        for rows_ in page_tables(content)
        if rows_
    }
    return {"headers": sorted(list(row) for row in headers)}


_NVIDIA_CANDIDATES = {
    "license_per_gpu_year_usd": re.compile(r"\$([0-9][0-9,]*(?:\.[0-9]+)?) per GPU per year"),
    "gpu_hour_usd": re.compile(r"\$([0-9][0-9,]*(?:\.[0-9]+)?) per GPU per hour"),
}


def _nvidia_statements(content: str) -> list[str]:
    statements = []
    for cell in page_text(content).split(" | "):
        for sentence in re.split(r"(?<=[.!?])\s+", cell.strip()):
            if "$" in sentence and "GPU" in sentence:
                statements.append(sentence)
    return statements


def _nvidia_evidence(content: str) -> dict[str, Any]:
    statements = _nvidia_statements(content)
    candidates = {}
    for dimension, pattern in _NVIDIA_CANDIDATES.items():
        for statement in statements:
            match = pattern.search(statement)
            if match:
                candidates[dimension] = match.group(1).replace(",", "")
                break
    return {
        "kind": "gpu_or_license_statements",
        "statements": statements,
        "candidates": candidates,
    }


def _nvidia_features(content: str) -> Any:
    text = page_text(content)
    return {
        "phrases": [phrase in text for phrase in ("per GPU per year", "per GPU per hour", "license")],
    }


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def redact_evidence(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Evidence reduced to normalized facts, for a source whose raw content
    may not be kept: table rows become a slug label and their parsed dollar
    amounts, and license statements become their numeric candidates. No
    page cell or sentence survives verbatim."""
    kind = evidence.get("kind")
    if kind == "tables":
        return {
            "kind": "tables",
            "redacted": True,
            "tables": [
                {
                    "columns": len(table.get("headers") or []),
                    "rows": [
                        {
                            "label": _slug(row[0]) if row else "",
                            "amounts": [
                                str(amount)
                                for cell in row[1:]
                                if (amount := dollars(cell)) is not None
                            ],
                        }
                        for row in table.get("rows") or []
                    ],
                }
                for table in evidence.get("tables") or []
            ],
        }
    if kind == "gpu_or_license_statements":
        return {
            "kind": kind,
            "redacted": True,
            "statement_count": len(evidence.get("statements") or []),
            "candidates": dict(evidence.get("candidates") or {}),
        }
    return {"kind": kind, "redacted": True}


# --- Registry of parsers --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParserSpec:
    # Whether the parser's output may become catalog price rows.
    emits_rows: bool
    features: Callable[[str], Any]
    table: Callable[..., Table] | None = None
    evidence: Callable[[str], dict[str, Any]] | None = None


PARSERS: Mapping[str, ParserSpec] = MappingProxyType(
    {
        "openai": ParserSpec(True, _openai_features, table=parse_openai),
        "anthropic": ParserSpec(True, _anthropic_features, table=parse_anthropic),
        "google": ParserSpec(True, _google_features, table=parse_google),
        "deepseek": ParserSpec(True, _deepseek_features, table=parse_deepseek),
        "mistral": ParserSpec(True, _mistral_features, table=parse_mistral),
        "xai": ParserSpec(True, _xai_features, table=parse_xai),
        "perplexity": ParserSpec(True, _perplexity_features, table=parse_perplexity),
        "fireworks": ParserSpec(False, _table_features, evidence=_table_evidence),
        "zai": ParserSpec(False, _table_features, evidence=_table_evidence),
        "aws-bedrock": ParserSpec(False, _table_features, evidence=_table_evidence),
        "azure-foundry": ParserSpec(False, _table_features, evidence=_table_evidence),
        "nvidia": ParserSpec(False, _nvidia_features, evidence=_nvidia_evidence),
    }
)


def _spec(parser_id: str, content: Any) -> ParserSpec:
    spec = PARSERS.get(parser_id)
    if spec is None:
        raise ValueError(f"unknown parser {parser_id!r}")
    if not isinstance(content, str):
        raise TypeError("content must be text; decode fetched bytes first")
    return spec


def structural_fingerprint(parser_id: str, content: str) -> str:
    """``sha256:<hex>`` of the parser-relevant structure of ``content``."""
    spec = _spec(parser_id, content)
    payload = json.dumps(
        {"parser": parser_id, "features": spec.features(content)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def extract(parser_id: str, content: str) -> Extraction:
    """Extract rates (or, for a discovery-only parser, evidence) from
    supplied content."""
    spec = _spec(parser_id, content)
    fingerprint = structural_fingerprint(parser_id, content)
    if spec.table is None:
        return Extraction(parser_id, fingerprint, (), False, spec.evidence(content))
    duplicates: list[str] = []
    table = spec.table(content, duplicates=duplicates)
    rates = []
    for key, fields in table.items():
        columns = {FIELD_COLUMNS[f]: v for f, v in fields.items() if f in FIELD_COLUMNS}
        if not columns:
            continue
        name, _, region = key.partition("@")
        rates.append(ExtractedRate(name, region or "global", MappingProxyType(columns)))
    rates.sort(key=lambda rate: (rate.model_key, rate.region))
    return Extraction(
        parser_id,
        fingerprint,
        tuple(rates),
        True,
        {"kind": "rates"},
        tuple(sorted(set(duplicates))),
    )
