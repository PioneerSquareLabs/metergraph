"""Row selection, source conversion, grading and rendering of the price
check, against inline fixtures. No test touches the network."""

import importlib.util
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_prices.py"
_spec = importlib.util.spec_from_file_location("check_prices", _SCRIPT)
check_prices = importlib.util.module_from_spec(_spec)
sys.modules["check_prices"] = check_prices
_spec.loader.exec_module(check_prices)

TODAY = date(2026, 10, 5)


def _document(**overrides):
    document = {
        "version": "test",
        "currency": "USD",
        "pricing_verified_at": "2026-10-01",
        "models": [
            {
                "canonical_id": "openai/gpt-x",
                "publisher": "openai",
                "aliases": [
                    {"provider": "openai", "alias": "gpt-x", "channel": "openai-api"},
                    {"provider": "litellm", "alias": "gpt-x", "channel": "openai-api"},
                    {"provider": "openai", "alias": "openai/gpt-x", "channel": "vercel-ai-gateway"},
                ],
                "prices": [
                    {
                        "channel": "openai-api",
                        "region": "global",
                        "effective_from": "2026-01-01",
                        "effective_to": "2026-06-01",
                        "input_per_mtok": 1.0,
                        "output_per_mtok": 4.0,
                        "source_url": "https://example.test/old",
                    },
                    {
                        "channel": "openai-api",
                        "region": "global",
                        "effective_from": "2026-06-01",
                        "input_per_mtok": 2.0,
                        "output_per_mtok": 8.0,
                        "cache_read_per_mtok": 0.2,
                        "source_url": "https://example.test/new",
                    },
                    {
                        "channel": "openai-api",
                        "region": "global",
                        "effective_from": "2027-01-01",
                        "input_per_mtok": 3.0,
                        "output_per_mtok": 9.0,
                        "source_url": "https://example.test/future",
                    },
                    {
                        "channel": "vercel-ai-gateway",
                        "region": "global",
                        "effective_from": "2026-06-01",
                        "input_per_mtok": 2.0,
                        "output_per_mtok": 8.0,
                        "source_url": "https://example.test/gateway",
                    },
                    {
                        "channel": "aws-bedrock",
                        "region": "us-east-1",
                        "effective_from": "2026-06-01",
                        "input_per_mtok": 2.2,
                        "output_per_mtok": 8.8,
                        "source_url": "https://example.test/bedrock",
                    },
                ],
            }
        ],
    }
    document.update(overrides)
    return document


def _rows():
    return check_prices.effective_rows(_document(), TODAY)


def _row(channel, region="global"):
    return next(r for r in _rows() if r.channel == channel and r.region == region)


# --- Row selection -----------------------------------------------------------


def test_effective_rows_pick_the_window_in_effect_today():
    row = _row("openai-api")
    assert row.effective_from == date(2026, 6, 1)
    assert row.rates == {
        "input": Decimal("2.0"),
        "output": Decimal("8.0"),
        "cache_read": Decimal("0.2"),
    }
    assert row.source_url == "https://example.test/new"


def test_effective_rows_keep_one_row_per_channel_and_region():
    keys = {(r.channel, r.region) for r in _rows()}
    assert keys == {
        ("openai-api", "global"),
        ("vercel-ai-gateway", "global"),
        ("aws-bedrock", "us-east-1"),
    }


def test_effective_rows_carry_the_channel_aliases():
    assert _row("openai-api").aliases == (("openai", "gpt-x"), ("litellm", "gpt-x"))
    assert _row("vercel-ai-gateway").aliases == (("openai", "openai/gpt-x"),)


# --- Source conversion -------------------------------------------------------


def test_vercel_quotes_convert_per_token_strings_to_per_million():
    data = {"data": [{"id": "openai/gpt-x", "pricing": {"input": "0.000002", "output": "0.000008", "input_cache_read": "0.0000002"}}]}
    quotes = check_prices._vercel_quotes(data, _rows())
    quote = quotes[_row("vercel-ai-gateway")]
    assert quote.source == "vercel"
    assert quote.rates == {"input": Decimal("2.000000"), "output": Decimal("8.000000"), "cache_read": Decimal("0.2000000")}
    assert _row("openai-api") not in quotes


def test_litellm_quotes_match_through_litellm_aliases_and_flag_other_channels():
    data = {
        "gpt-x": {
            "input_cost_per_token": 2e-06,
            "output_cost_per_token": 8e-06,
            "cache_read_input_token_cost": 2e-07,
            "cache_creation_input_token_cost": 2.5e-06,
            "litellm_provider": "vertex_ai-language-models",
        }
    }
    quote = check_prices._litellm_quotes(data, _rows())[_row("openai-api")]
    assert quote.key == "gpt-x"
    assert quote.rates["cache_write"] == Decimal("2.5e-06") * check_prices.MTOK
    assert quote.source_channel == "google-vertex-ai"


def test_modelsdev_quotes_use_the_provider_map_and_direct_alias():
    data = {"openai": {"models": {"gpt-x": {"cost": {"input": 2, "output": 8, "cache_read": 0.2}}}}}
    quote = check_prices._modelsdev_quotes(data, _rows())[_row("openai-api")]
    assert quote.key == "openai/gpt-x"
    assert quote.rates == {"input": Decimal("2"), "output": Decimal("8"), "cache_read": Decimal("0.2")}


def test_region_blind_sources_skip_regional_rows():
    data = {"amazon-bedrock": {"models": {"gpt-x": {"cost": {"input": 2, "output": 8}}}}}
    assert _row("aws-bedrock", "us-east-1") not in check_prices._modelsdev_quotes(data, _rows())


def test_portkey_quotes_convert_cents_per_token_and_pause_between_requests():
    seen = []

    def fetch(url):
        seen.append(url)
        if url.endswith("/openai/gpt-x"):
            return {"pay_as_you_go": {"request_token": {"price": 0.0002}, "response_token": {"price": 0.0008}}}
        return None

    quotes = check_prices._portkey_quotes(fetch, _rows(), pause=0)
    quote = quotes[_row("openai-api")]
    assert quote.rates == {"input": Decimal("2.0000"), "output": Decimal("8.0000")}
    assert seen == ["https://api.portkey.ai/model-configs/pricing/openai/gpt-x"]


# --- Grading -----------------------------------------------------------------


def _quote(source, **rates):
    return check_prices.Quote(source, "k", {k: Decimal(str(v)) for k, v in rates.items()})


def test_a_row_no_source_lists_is_unchecked():
    [finding] = check_prices.compare([_row("openai-api")], {"litellm": {}})
    assert finding.verdict == "unchecked"


def test_agreement_within_tolerance_confirms():
    row = _row("openai-api")
    [finding] = check_prices.compare([row], {"litellm": {row: _quote("litellm", input=2.005, output=8.0)}})
    assert finding.verdict == "confirmed"
    assert finding.differences["litellm"] == []


def test_one_dissenting_source_is_disputed():
    row = _row("openai-api")
    [finding] = check_prices.compare(
        [row],
        {"litellm": {row: _quote("litellm", input=1.5, output=8.0)}, "modelsdev": {row: _quote("modelsdev", input=2.0, output=8.0)}},
    )
    assert finding.verdict == "disputed"
    [diff] = finding.differences["litellm"]
    assert (diff.field, diff.ours, diff.theirs) == ("input", Decimal("2.0"), Decimal("1.5"))


def test_two_sources_agreeing_against_us_is_an_update():
    row = _row("openai-api")
    [finding] = check_prices.compare(
        [row],
        {"litellm": {row: _quote("litellm", input=1.5)}, "modelsdev": {row: _quote("modelsdev", input=1.5)}},
    )
    assert finding.verdict == "update"


def test_two_sources_disagreeing_differently_stay_disputed():
    row = _row("openai-api")
    [finding] = check_prices.compare(
        [row],
        {"litellm": {row: _quote("litellm", input=1.5)}, "modelsdev": {row: _quote("modelsdev", input=1.0)}},
    )
    assert finding.verdict == "disputed"


def test_the_authoritative_source_alone_forces_an_update():
    row = _row("vercel-ai-gateway")
    [finding] = check_prices.compare([row], {"vercel": {row: _quote("vercel", input=1.0, output=8.0)}})
    assert finding.verdict == "update"


def test_fields_only_one_side_quotes_are_not_compared():
    row = _row("openai-api")
    [finding] = check_prices.compare([row], {"litellm": {row: _quote("litellm", input=2.0, output=8.0, cache_write=9.0)}})
    assert finding.verdict == "confirmed"


# --- Report and exit status --------------------------------------------------


def test_markdown_report_lists_updates_with_both_values():
    row = _row("vercel-ai-gateway")
    findings = check_prices.compare([row], {"vercel": {row: _quote("vercel", input=1.0, output=8.0)}})
    text = check_prices.render_markdown(findings, ["vercel"], TODAY)
    assert "- update: 1" in text
    assert "| openai/gpt-x | vercel-ai-gateway | input | 2.0 | vercel 1.0 |" in text


def test_json_report_carries_verdicts_and_differences():
    row = _row("vercel-ai-gateway")
    findings = check_prices.compare([row], {"vercel": {row: _quote("vercel", input=1.0, output=8.0)}})
    payload = check_prices.to_json(findings, ["vercel"], TODAY)
    [entry] = payload["rows"]
    assert entry["verdict"] == "update"
    assert entry["differences"] == {"vercel": [{"field": "input", "ours": "2.0", "theirs": "1.0"}]}


def _seed_cache(cache_dir, url, payload):
    cache_dir.mkdir(parents=True, exist_ok=True)
    name = url.replace("://", "_").replace("/", "_") + ".json"
    (cache_dir / name).write_text(json.dumps(payload))


def test_main_exits_two_on_an_update_signal(tmp_path, capsys):
    catalog = tmp_path / "prices.yaml"
    catalog.write_text(json.dumps(_document()))  # JSON is valid YAML
    cache = tmp_path / "cache"
    _seed_cache(cache, check_prices.VERCEL_URL, {"data": [{"id": "openai/gpt-x", "pricing": {"input": "0.000001", "output": "0.000008"}}]})
    report = tmp_path / "report.json"
    status = check_prices.main(
        ["--catalog", str(catalog), "--sources", "vercel", "--cache-dir", str(cache), "--json", str(report), "--today", "2026-10-05"]
    )
    assert status == 2
    assert "Needs an update" in capsys.readouterr().out
    rows = {r["channel"]: r["verdict"] for r in json.loads(report.read_text())["rows"]}
    assert rows["vercel-ai-gateway"] == "update"


def test_main_exits_zero_when_sources_agree(tmp_path, capsys):
    catalog = tmp_path / "prices.yaml"
    catalog.write_text(json.dumps(_document()))
    cache = tmp_path / "cache"
    _seed_cache(cache, check_prices.VERCEL_URL, {"data": [{"id": "openai/gpt-x", "pricing": {"input": "0.000002", "output": "0.000008"}}]})
    status = check_prices.main(["--catalog", str(catalog), "--sources", "vercel", "--cache-dir", str(cache), "--today", "2026-10-05"])
    assert status == 0
    assert "- confirmed: 1" in capsys.readouterr().out


def test_main_rejects_an_unknown_source(tmp_path):
    with pytest.raises(SystemExit):
        check_prices.main(["--sources", "nope", "--today", "2026-10-05"])


# --- Provider pages ----------------------------------------------------------


def test_openai_quotes_read_the_standard_text_table():
    page = (
        '[0,"standard"]'
        '[1,[[0,"Image"],[0,5],[0,1.25],[0,"-"]]]'  # an image table also labelled standard
        '[0,"batch"]'
        '[0,"standard"]'
        '[1,[[0,"gpt-6-sol"],[0,2],[0,0.2],[0,2.5],[0,10]]]'
        '[1,[[0,"gpt-x"],[0,2],[0,0.2],[0,2.5],[0,8]]]'
        '[1,[[0,"gpt-4o (legacy)"],[0,2.5],[0,1.25],[0,10]]]'
        '[0,"batch"]'
        '[1,[[0,"gpt-x"],[0,1],[0,0.1],[0,1.25],[0,4]]]'
    ).replace('"', "&quot;")
    quote = check_prices._openai_quotes(page, _rows())[_row("openai-api")]
    assert quote.source == "openai"
    assert quote.rates == {"input": Decimal("2"), "cache_read": Decimal("0.2"), "cache_write": Decimal("2.5"), "output": Decimal("8")}


def _anthropic_page():
    return (
        "<table><tr><th>Model</th><th>Base tokens</th><th>Prompt caching</th></tr>"
        "<tr><th>Name</th><th>Input</th><th>Output</th><th>5m writes</th><th>1h writes</th><th>Hits and refreshes</th></tr>"
        "<tr><td>Claude Opus 5.5For long-running work</td><td>$4 / MTok</td><td>$20 / MTok</td><td>$5 / MTok</td><td>$8 / MTok</td><td>$0.20 / MTok</td></tr>"
        "<tr><td>Claude Opus 5For agentic work</td><td>$5 / MTok</td><td>$25 / MTok</td><td>$6.25 / MTok</td><td>$10 / MTok</td><td>$0.50 / MTok</td></tr>"
        "</table>"
    )


@pytest.mark.parametrize(
    ("alias", "expected_input"),
    [("claude-opus-5-5", "4"), ("claude-opus-5-5-20260922", "4"), ("claude-opus-5", "5")],
)
def test_anthropic_quotes_match_model_ids_exactly_or_with_a_date(alias, expected_input):
    doc = _document()
    doc["models"][0]["aliases"] = [{"provider": "anthropic", "alias": alias, "channel": "anthropic-api"}]
    doc["models"][0]["prices"] = [{"channel": "anthropic-api", "effective_from": "2026-06-01", "input_per_mtok": 1, "output_per_mtok": 1, "source_url": "https://example.test"}]
    rows = check_prices.effective_rows(doc, TODAY)
    quote = check_prices._anthropic_quotes(_anthropic_page(), rows)[rows[0]]
    assert quote.rates["input"] == Decimal(expected_input)
    assert quote.rates["cache_read"] == (Decimal("0.20") if expected_input == "4" else Decimal("0.50"))


def test_anthropic_quotes_do_not_match_a_variant_to_its_base_model():
    doc = _document()
    doc["models"][0]["aliases"] = [{"provider": "anthropic", "alias": "claude-opus-5-fast", "channel": "anthropic-api"}]
    doc["models"][0]["prices"] = [{"channel": "anthropic-api", "effective_from": "2026-06-01", "input_per_mtok": 10, "output_per_mtok": 50, "source_url": "https://example.test"}]
    rows = check_prices.effective_rows(doc, TODAY)
    assert check_prices._anthropic_quotes(_anthropic_page(), rows) == {}


def test_google_quotes_read_each_models_block_and_skip_per_image_output():
    page = (
        "<h2>Gemini X</h2><p>gemini-x</p><a>Try it in Google AI Studio</a>"
        "<td>Input price</td><td>Free of charge</td><td>$0.25 (text / image / video)</td><td>$0.50 (audio)</td>"
        "<td>Output price (including thinking tokens)</td><td>Free of charge</td><td>$1.50</td>"
        "<td>Context caching price</td><td>Not available</td><td>$0.025</td>"
        "<h2>Gemini X Image</h2><p>gemini-x-image</p><a>Try it in Google AI Studio</a>"
        "<td>Input price</td><td>Not available</td><td>$0.30 (text / image)</td>"
        "<td>Output price</td><td>Not available</td><td>$0.039 per image*</td>"
    )
    doc = _document()
    doc["models"] = [
        {"canonical_id": "google/gemini-x", "aliases": [{"provider": "google", "alias": "gemini-x", "channel": "google-api"}],
         "prices": [{"channel": "google-api", "effective_from": "2026-06-01", "input_per_mtok": 0.25, "output_per_mtok": 1.5, "source_url": "https://example.test"}]},
        {"canonical_id": "google/gemini-x-image", "aliases": [{"provider": "google", "alias": "gemini-x-image", "channel": "google-api"}],
         "prices": [{"channel": "google-api", "effective_from": "2026-06-01", "input_per_mtok": 0.3, "output_per_mtok": 30, "source_url": "https://example.test"}]},
    ]
    rows = check_prices.effective_rows(doc, TODAY)
    quotes = check_prices._google_quotes(page, rows)
    assert quotes[rows[0]].rates == {"input": Decimal("0.25"), "output": Decimal("1.50"), "cache_read": Decimal("0.025")}
    assert quotes[rows[1]].rates == {"input": Decimal("0.30")}


def test_deepseek_quotes_take_the_peak_column_per_model():
    page = (
        "<table>"
        "<tr><th>MODEL</th><th>deepseek-flash(1)</th><th>deepseek-v4-pro</th></tr>"
        "<tr><td>PRICING(2)</td><td>1M INPUT TOKENS(CACHE HIT)</td><td>OFF-PEAK</td><td>$0.003</td><td>$0.022</td></tr>"
        "<tr><td>PEAK</td><td>$0.006</td><td>$0.044</td></tr>"
        "<tr><td>1M INPUT TOKENS(CACHE MISS)</td><td>OFF-PEAK</td><td>$0.15</td><td>$0.66</td></tr>"
        "<tr><td>PEAK</td><td>$0.3</td><td>$1.32</td></tr>"
        "<tr><td>1M OUTPUT TOKENS</td><td>OFF-PEAK</td><td>$0.6</td><td>$1.98</td></tr>"
        "<tr><td>PEAK</td><td>$1.2</td><td>$3.96</td></tr>"
        "</table>"
    )
    doc = _document()
    doc["models"] = [
        {"canonical_id": "deepseek/v4-flash", "aliases": [{"provider": "deepseek", "alias": "deepseek-v4-flash", "channel": "deepseek-api"}],
         "prices": [{"channel": "deepseek-api", "effective_from": "2026-06-01", "input_per_mtok": 0.3, "output_per_mtok": 1.2, "source_url": "https://example.test"}]},
        {"canonical_id": "deepseek/v4-pro", "aliases": [{"provider": "deepseek", "alias": "deepseek-v4-pro", "channel": "deepseek-api"}],
         "prices": [{"channel": "deepseek-api", "effective_from": "2026-06-01", "input_per_mtok": 1.32, "output_per_mtok": 3.96, "source_url": "https://example.test"}]},
    ]
    rows = check_prices.effective_rows(doc, TODAY)
    quotes = check_prices._deepseek_quotes(page, rows)
    assert quotes[rows[0]].rates == {"cache_read": Decimal("0.006"), "input": Decimal("0.3"), "output": Decimal("1.2")}
    assert quotes[rows[1]].rates == {"cache_read": Decimal("0.044"), "input": Decimal("1.32"), "output": Decimal("3.96")}


def test_vercel_quotes_prefer_the_regional_or_peak_variant_the_row_carries():
    data = {"data": [{"id": "openai/gpt-x", "pricing": {
        "input": "0.000001", "output": "0.000004",
        "peak_pricing": {"multiplier": 2},
        "regional": {"us": {"input": "0.000003", "output": "0.000009"}},
    }}]}
    quote = check_prices._vercel_quotes(data, _rows())[_row("vercel-ai-gateway")]  # row is 2.0 / 8.0
    assert quote.key == "openai/gpt-x peak"
    assert quote.rates == {"input": Decimal("2.000000"), "output": Decimal("8.000000")}


# --- Provider price decides ---------------------------------------------------


def test_the_providers_own_price_confirms_over_dissenting_second_opinions():
    row = _row("openai-api")
    [finding] = check_prices.compare(
        [row],
        {"openai": {row: _quote("openai", input=2.0, output=8.0)}, "litellm": {row: _quote("litellm", input=1.0)}, "modelsdev": {row: _quote("modelsdev", input=1.0)}},
    )
    assert finding.authority == "openai"
    assert finding.verdict == "confirmed"
    text = check_prices.render_markdown([finding], ["openai", "litellm", "modelsdev"], TODAY)
    assert "overrules" in text and "litellm says input 1.0" in text


def test_the_providers_own_price_alone_forces_an_update():
    row = _row("openai-api")
    [finding] = check_prices.compare(
        [row],
        {"openai": {row: _quote("openai", input=1.0, output=8.0)}, "litellm": {row: _quote("litellm", input=2.0, output=8.0)}},
    )
    assert finding.verdict == "update"
    text = check_prices.render_markdown([finding], ["openai", "litellm"], TODAY)
    assert "| openai/gpt-x | openai-api | input | 2.0 | openai 1.0; agree: litellm |" in text
