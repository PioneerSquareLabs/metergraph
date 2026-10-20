"""Deterministic extraction from supplied source content: rates, structural
fingerprints, and discovery-only evidence. Nothing here fetches."""

from decimal import Decimal
from pathlib import Path

import pytest

from metergraph_core.extractors import (
    ExtractedRate,
    extract,
    structural_fingerprint,
)

FIXTURES = Path(__file__).parent / "fixtures" / "sources"
GENERIC = Path(__file__).parent / "fixtures" / "discovery"
D = Decimal


def _fixture(source_id):
    return (FIXTURES / f"{source_id}.html").read_text()


def _generic(name):
    return (GENERIC / f"{name}.html").read_text()


def _rates(extraction):
    return {(r.model_key, r.region): dict(r.rates) for r in extraction.rates}


EXPECTED = {
    ("openai", "openai-api-pricing"): {
        ("gpt-legacy", "global"): {"input_per_mtok": D("2.5"), "cache_read_per_mtok": D("1.25"), "output_per_mtok": D("10")},
        ("gpt-x", "global"): {"input_per_mtok": D("2"), "cache_read_per_mtok": D("0.2"), "cache_write_5m_per_mtok": D("2.5"), "output_per_mtok": D("8")},
        ("gpt-x-mini", "global"): {"input_per_mtok": D("0.4"), "cache_read_per_mtok": D("0.04"), "output_per_mtok": D("1.6")},
    },
    ("anthropic", "anthropic-api-pricing"): {
        ("claude-example-5", "global"): {"input_per_mtok": D("5"), "output_per_mtok": D("25"), "cache_write_5m_per_mtok": D("6.25"), "cache_read_per_mtok": D("0.50")},
        ("claude-example-5-5", "global"): {"input_per_mtok": D("4"), "output_per_mtok": D("20"), "cache_write_5m_per_mtok": D("5"), "cache_read_per_mtok": D("0.40")},
    },
    ("google", "google-api-pricing"): {
        ("gemini-x", "global"): {"input_per_mtok": D("0.25"), "output_per_mtok": D("1.50"), "cache_read_per_mtok": D("0.025")},
        ("gemini-x-image", "global"): {"input_per_mtok": D("0.30")},
    },
    ("xai", "xai-api-pricing"): {
        ("grok-x", "global"): {"input_per_mtok": D("2"), "cache_read_per_mtok": D("0.5"), "output_per_mtok": D("6")},
        ("grok-x", "us"): {"input_per_mtok": D("2.2"), "cache_read_per_mtok": D("0.55"), "output_per_mtok": D("6.6")},
    },
}


@pytest.mark.parametrize(("parser", "source_id"), list(EXPECTED))
def test_each_official_fixture_extracts_its_catalog_rates(parser, source_id):
    extraction = extract(parser, _fixture(source_id))
    assert extraction.parser == parser
    assert extraction.applicable is True
    assert extraction.ambiguities == ()
    assert _rates(extraction) == EXPECTED[(parser, source_id)]


@pytest.mark.parametrize(("parser", "source_id"), list(EXPECTED))
def test_extraction_is_deterministic_and_sorted(parser, source_id):
    first = extract(parser, _fixture(source_id))
    second = extract(parser, _fixture(source_id))
    assert first == second
    keys = [(r.model_key, r.region) for r in first.rates]
    assert keys == sorted(keys)


def test_extracted_rates_are_immutable():
    [rate, *_] = extract("openai", _fixture("openai-api-pricing")).rates
    assert isinstance(rate, ExtractedRate)
    with pytest.raises(TypeError):
        rate.rates["input_per_mtok"] = D("0")


# Each low-risk parser's fixture with a price changed, with a model added, and
# with a parser-relevant structural change. The other official parsers keep
# their focused inline cases in test_check_prices.py.
VARIANTS = {
    "openai": (
        "openai-api-pricing",
        lambda c: c.replace("[0,2],[0,0.2],[0,2.5],[0,8]", "[0,3],[0,0.3],[0,3.5],[0,9]"),
        lambda c: c.replace(
            "[1,[[0,&quot;gpt-x-mini&quot;]",
            "[1,[[0,&quot;gpt-z&quot;],[0,1],[0,0.1],[0,4]]][1,[[0,&quot;gpt-x-mini&quot;]",
        ),
        lambda c: c.replace("&quot;batch&quot;", "&quot;flex&quot;"),
    ),
    "anthropic": (
        "anthropic-api-pricing",
        lambda c: c.replace("$4 / MTok", "$4.50 / MTok"),
        lambda c: c.replace(
            "</table>",
            "<tr><td>Claude Example 6New</td><td>$6 / MTok</td><td>$30 / MTok</td><td>$7.5 / MTok</td><td>$12 / MTok</td><td>$0.60 / MTok</td></tr></table>",
        ),
        lambda c: c.replace("5m writes", "5-minute writes"),
    ),
    "google": (
        "google-api-pricing",
        lambda c: c.replace("$1.50", "$1.75"),
        lambda c: c.replace(
            "</body>",
            "<h2>Gemini Y</h2><p>gemini-y</p><a>Try it in Google AI Studio</a><table><tr><td>Input price</td><td>$1</td></tr><tr><td>Output price</td><td>$2</td></tr></table></body>",
        ),
        lambda c: c.replace("Context caching price", "Caching price"),
    ),
    "xai": (
        "xai-api-pricing",
        lambda c: c.replace("&quot;20000&quot;", "&quot;25000&quot;"),
        lambda c: c.replace(
            "</script>",
            "{&quot;name&quot;:&quot;grok-y&quot;,&quot;version&quot;:&quot;1.0&quot;,&quot;promptTextTokenPrice&quot;:&quot;1000&quot;,&quot;cachedPromptTokenPrice&quot;:&quot;100&quot;,&quot;completionTextTokenPrice&quot;:&quot;3000&quot;,&quot;cluster&quot;:&quot;us-east-1&quot;}</script>",
        ),
        lambda c: c.replace("cachedPromptTokenPrice", "cachedTokenPrice"),
    ),
}


@pytest.mark.parametrize("parser", list(VARIANTS))
def test_fingerprint_ignores_a_price_change(parser):
    source_id, change_price, _, _ = VARIANTS[parser]
    base = _fixture(source_id)
    changed = change_price(base)
    assert changed != base
    assert _rates(extract(parser, changed)) != _rates(extract(parser, base))
    assert structural_fingerprint(parser, changed) == structural_fingerprint(parser, base)


@pytest.mark.parametrize("parser", [p for p, v in VARIANTS.items() if v[2] is not None])
def test_fingerprint_ignores_a_newly_listed_model(parser):
    source_id, _, add_model, _ = VARIANTS[parser]
    base = _fixture(source_id)
    added = add_model(base)
    assert len(extract(parser, added).rates) > len(extract(parser, base).rates)
    assert structural_fingerprint(parser, added) == structural_fingerprint(parser, base)


@pytest.mark.parametrize("parser", list(VARIANTS))
def test_fingerprint_changes_with_parser_relevant_structure(parser):
    source_id, _, _, restructure = VARIANTS[parser]
    base = _fixture(source_id)
    assert structural_fingerprint(parser, restructure(base)) != structural_fingerprint(parser, base)


def _swap(text, first, second):
    return text.replace(first, "\0").replace(second, first).replace("\0", second)


def test_openai_fingerprint_changes_when_same_width_columns_are_reordered():
    # The embedded cells fill columns by position, so swapping two column
    # labels changes what each cell means without changing any row width.
    base = _fixture("openai-api-pricing")
    reordered = _swap(base, "<th>Cached input</th>", "<th>Cache writes</th>")
    assert reordered != base
    assert structural_fingerprint("openai", reordered) != structural_fingerprint("openai", base)


def test_openai_fingerprint_changes_when_the_column_header_disappears():
    base = _fixture("openai-api-pricing")
    without = base.replace("<th>Short context</th>", "<th>Context</th>")
    assert structural_fingerprint("openai", without) != structural_fingerprint("openai", base)


def test_openai_fingerprint_ignores_labels_outside_the_text_table_columns():
    base = _fixture("openai-api-pricing")
    other_table = base.replace(
        "<script>", "<table><tr><th>Model</th><th>Price per minute</th></tr></table><script>"
    )
    assert structural_fingerprint("openai", other_table) == structural_fingerprint("openai", base)


def test_extraction_carries_the_structural_fingerprint():
    content = _fixture("anthropic-api-pricing")
    fingerprint = structural_fingerprint("anthropic", content)
    assert extract("anthropic", content).fingerprint == fingerprint
    assert fingerprint.startswith("sha256:") and len(fingerprint) == 71


def test_fingerprints_differ_between_parsers_for_the_same_content():
    content = _generic("token-table")
    assert structural_fingerprint("html-tables", content) != structural_fingerprint("gpu-license-prose", content)


def test_two_candidate_values_for_one_model_are_ambiguous():
    content = _fixture("openai-api-pricing").replace(
        "[1,[[0,&quot;gpt-x-mini&quot;]",
        "[1,[[0,&quot;gpt-x&quot;],[0,2.2],[0,0.2],[0,2.5],[0,8.8]]][1,[[0,&quot;gpt-x-mini&quot;]",
    )
    extraction = extract("openai", content)
    assert extraction.ambiguities == ("gpt-x",)
    # The first candidate is still what the price checker reads.
    assert _rates(extraction)[("gpt-x", "global")]["input_per_mtok"] == D("2")


def test_a_repeated_model_with_equal_values_is_not_ambiguous():
    content = _fixture("anthropic-api-pricing").replace(
        "</table>",
        "<tr><td>Claude Example 5For agentic work</td><td>$5 / MTok</td><td>$25 / MTok</td><td>$6.25 / MTok</td><td>$10 / MTok</td><td>$0.50 / MTok</td></tr></table>",
    )
    assert extract("anthropic", content).ambiguities == ()


# Discovery-only parsers are generic: they read any page's tables or GPU and
# license prose, and are tested on generic synthetic pages, never on a mock of
# one provider's site.


def test_the_table_parser_emits_evidence_and_no_rates():
    extraction = extract("html-tables", _generic("token-table"))
    assert extraction.applicable is False
    assert extraction.rates == ()
    assert extraction.evidence == {
        "kind": "tables",
        "tables": [
            {
                "headers": ["Model", "Input (per 1M tokens)", "Cached input (per 1M tokens)", "Output (per 1M tokens)"],
                "rows": [
                    ["Example Model Large", "$2.00", "$0.20", "$8.00"],
                    ["Example Model Small", "$0.40", "Free", "$1.60"],
                ],
            },
            {"headers": ["Accelerator", "Price per hour"], "rows": [["Example Accelerator", "$2.90"]]},
        ],
    }


def test_the_table_parser_reads_a_page_without_tables_as_empty_evidence():
    extraction = extract("html-tables", "<p>No tables here.</p>")
    assert extraction.evidence == {"kind": "tables", "tables": []}


def test_the_table_fingerprint_ignores_prices_but_not_headers():
    base = _generic("token-table")
    assert structural_fingerprint("html-tables", base.replace("$2.00", "$2.50")) == structural_fingerprint("html-tables", base)
    assert structural_fingerprint("html-tables", base.replace("Cached input (per", "Cache hit (per")) != structural_fingerprint("html-tables", base)


def test_the_license_parser_records_license_pricing_and_never_token_rates():
    extraction = extract("gpu-license-prose", _generic("gpu-license-prose"))
    assert extraction.applicable is False
    assert extraction.rates == ()
    assert extraction.evidence["kind"] == "gpu_or_license_statements"
    assert extraction.evidence["candidates"] == {
        "license_per_gpu_year_usd": "1000",
        "gpu_hour_usd": "0.50",
    }
    assert extraction.evidence["statements"] == [
        "Licenses start at $1000 per GPU per year, or about $0.50 per GPU per hour in the cloud."
    ]


def test_the_license_parser_ignores_token_wording():
    content = _generic("gpu-license-prose").replace(
        "</body>", "<p>Hosted trials cost $0.10 per 1M tokens.</p></body>"
    )
    extraction = extract("gpu-license-prose", content)
    assert extraction.rates == ()
    assert set(extraction.evidence["candidates"]) == {"license_per_gpu_year_usd", "gpu_hour_usd"}


def _xai_object(name, **fields):
    body = "".join(f",&quot;{key}&quot;:&quot;{value}&quot;" for key, value in fields.items())
    return "{&quot;name&quot;:&quot;" + name + "&quot;,&quot;version&quot;:&quot;1.0&quot;" + body + "}"


def test_xai_reads_each_model_only_from_its_own_object():
    # grok-a omits its cluster and cached price; grok-b, right after, has both.
    page = (
        "<script>"
        + _xai_object("grok-a", promptTextTokenPrice="10000", completionTextTokenPrice="30000")
        + _xai_object(
            "grok-b",
            promptTextTokenPrice="20000",
            cachedPromptTokenPrice="5000",
            completionTextTokenPrice="60000",
            cluster="us-central-1",
        )
        + "</script>"
    )
    assert _rates(extract("xai", page)) == {
        ("grok-a", "global"): {"input_per_mtok": D("1"), "output_per_mtok": D("3")},
        ("grok-b", "us"): {"input_per_mtok": D("2"), "cache_read_per_mtok": D("0.5"), "output_per_mtok": D("6")},
    }


def test_xai_skips_a_model_whose_own_object_lacks_a_required_price():
    # grok-a has no output price of its own; grok-b's must not be borrowed.
    page = (
        "<script>"
        + _xai_object("grok-a", promptTextTokenPrice="10000", cluster="us-east-1")
        + _xai_object("grok-b", promptTextTokenPrice="20000", completionTextTokenPrice="60000", cluster="us-east-1")
        + "</script>"
    )
    assert _rates(extract("xai", page)) == {
        ("grok-b", "global"): {"input_per_mtok": D("2"), "output_per_mtok": D("6")},
    }


def test_xai_reads_prices_nested_inside_a_model_object():
    page = (
        "<script>{&quot;name&quot;:&quot;grok-c&quot;,&quot;version&quot;:&quot;1.0&quot;,"
        "&quot;limits&quot;:{&quot;rpm&quot;:&quot;60&quot;},"
        "&quot;promptTextTokenPrice&quot;:&quot;10000&quot;,&quot;completionTextTokenPrice&quot;:&quot;30000&quot;}</script>"
    )
    assert _rates(extract("xai", page)) == {
        ("grok-c", "global"): {"input_per_mtok": D("1"), "output_per_mtok": D("3")},
    }


def test_xai_finds_the_enclosing_object_when_a_nested_one_precedes_the_name():
    page = (
        "<script>{&quot;limits&quot;:{&quot;rpm&quot;:&quot;60&quot;},&quot;name&quot;:&quot;grok-d&quot;,&quot;version&quot;:&quot;1.0&quot;,"
        "&quot;promptTextTokenPrice&quot;:&quot;10000&quot;,&quot;completionTextTokenPrice&quot;:&quot;30000&quot;}</script>"
    )
    assert _rates(extract("xai", page)) == {
        ("grok-d", "global"): {"input_per_mtok": D("1"), "output_per_mtok": D("3")},
    }


def test_unknown_parser_is_rejected():
    with pytest.raises(ValueError, match="unknown parser 'scraper'"):
        extract("scraper", "<html></html>")
    with pytest.raises(ValueError, match="unknown parser 'scraper'"):
        structural_fingerprint("scraper", "<html></html>")


@pytest.mark.parametrize("content", [b"<html></html>", None, 3])
def test_extractors_accept_only_text(content):
    with pytest.raises(TypeError, match="content must be text"):
        extract("openai", content)


def test_an_unrecognised_page_extracts_nothing_and_changes_the_fingerprint():
    page = "<html><body><p>Pricing moved.</p></body></html>"
    extraction = extract("openai", page)
    assert extraction.rates == ()
    assert extraction.fingerprint != structural_fingerprint("openai", _fixture("openai-api-pricing"))
