"""The low-risk refresh script, run against fixture content. No test touches
the network."""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

from metergraph_core.extractors import structural_fingerprint

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "refresh_prices.py"
_spec = importlib.util.spec_from_file_location("refresh_prices", _SCRIPT)
refresh_prices = importlib.util.module_from_spec(_spec)
sys.modules["refresh_prices"] = refresh_prices
_spec.loader.exec_module(refresh_prices)

FIXTURES = Path(__file__).parent / "fixtures" / "sources"
OPENAI_URL = "https://example.com/openai-pricing"
ANTHROPIC_URL = "https://example.com/anthropic-pricing"


def _source(source_id, parser, channel, url, *, store_raw=False):
    content = (FIXTURES / f"{source_id}.html").read_text()
    dimensions = ["input_per_mtok", "cache_read_per_mtok", "cache_write_5m_per_mtok", "output_per_mtok"]
    return {
        "id": source_id,
        "provider": parser,
        "channel": channel,
        "catalog_channel": True,
        "url": url,
        "parser": parser,
        "source_shape": "token_table",
        "clarity": "structured",
        "dimensions": dimensions,
        "fingerprint": structural_fingerprint(parser, content),
        "low_risk": {"eligible": True, "max_relative_change": "0.5", "dimensions": dimensions},
        "evidence": {
            "store_raw": store_raw,
            "redistribute_raw": False,
            "excerpt_chars": 0,
            "reason": "Synthetic test source.",
        },
    }


def _row(channel, **rates):
    return dict({"channel": channel, "region": "global", "effective_from": "2026-01-01", "source_url": "https://example.com/model"}, **rates)


def _catalog(gpt_x_output=8.0):
    return {
        "version": "2026-10-19",
        "currency": "USD",
        "pricing_verified_at": "2026-10-19",
        "models": [
            {"canonical_id": "example/gpt-x", "aliases": [{"provider": "openai", "alias": "gpt-x", "channel": "openai-api"}], "prices": [_row("openai-api", input_per_mtok=2.0, cache_read_per_mtok=0.2, cache_write_5m_per_mtok=2.5, output_per_mtok=gpt_x_output)]},
            {"canonical_id": "example/gpt-x-mini", "aliases": [{"provider": "openai", "alias": "gpt-x-mini", "channel": "openai-api"}], "prices": [_row("openai-api", input_per_mtok=0.4, cache_read_per_mtok=0.04, output_per_mtok=1.6)]},
            {"canonical_id": "example/gpt-legacy", "aliases": [{"provider": "openai", "alias": "gpt-legacy", "channel": "openai-api"}], "prices": [_row("openai-api", input_per_mtok=2.5, cache_read_per_mtok=1.25, output_per_mtok=10.0)]},
            {"canonical_id": "example/claude-example-5", "aliases": [{"provider": "anthropic", "alias": "claude-example-5", "channel": "anthropic-api"}], "prices": [_row("anthropic-api", input_per_mtok=5.0, output_per_mtok=25.0, cache_write_5m_per_mtok=6.25, cache_read_per_mtok=0.5)]},
            {"canonical_id": "example/claude-example-5-5", "aliases": [{"provider": "anthropic", "alias": "claude-example-5-5", "channel": "anthropic-api"}], "prices": [_row("anthropic-api", input_per_mtok=4.0, output_per_mtok=20.0, cache_write_5m_per_mtok=5.0, cache_read_per_mtok=0.4)]},
        ],
    }


@pytest.fixture
def setup(tmp_path):
    catalog = tmp_path / "prices.yaml"
    registry = tmp_path / "sources.yaml"
    content = tmp_path / "content"
    content.mkdir()
    for source_id in ("openai-api-pricing", "anthropic-api-pricing"):
        (content / f"{source_id}.html").write_text((FIXTURES / f"{source_id}.html").read_text())

    def write(catalog_document=None, sources=None):
        catalog.write_text(yaml.safe_dump(catalog_document or _catalog()))
        registry.write_text(
            yaml.safe_dump(
                {
                    "schema_version": 1,
                    "sources": sources
                    or [
                        _source("openai-api-pricing", "openai", "openai-api", OPENAI_URL),
                        _source("anthropic-api-pricing", "anthropic", "anthropic-api", ANTHROPIC_URL),
                    ],
                }
            )
        )
        return ["--catalog", str(catalog), "--registry", str(registry), "--observed-on", "2026-10-20"]

    return write, catalog, content, tmp_path


def _run(argv, capsys):
    status = refresh_prices.main(argv)
    return status, json.loads(capsys.readouterr().out)


def test_cli_reports_no_change_and_exits_zero(setup, capsys):
    write, _, content, _ = setup
    status, output = _run(write() + ["--content-dir", str(content)], capsys)
    assert status == 0
    assert output["delta"]["operations"] == []
    assert output["escalations"] == []
    assert [(o["source_id"], o["result"]) for o in output["observations"]] == [
        ("openai-api-pricing", "unchanged"),
        ("anthropic-api-pricing", "unchanged"),
    ]
    assert output["observed_on"] == "2026-10-20"


def test_cli_emits_a_delta_for_an_in_bounds_change(setup, capsys):
    write, _, content, _ = setup
    status, output = _run(write(_catalog(gpt_x_output=9.0)) + ["--content-dir", str(content)], capsys)
    assert status == 2
    [operation] = output["delta"]["operations"]
    assert operation["op"] == "replace_price"
    assert operation["key"]["canonical_id"] == "example/gpt-x"
    assert operation["price"]["output_per_mtok"] == "8"
    assert operation["price"]["effective_from"] == "2026-10-20"
    assert output["escalations"] == []


def test_cli_escalates_a_format_change(setup, capsys):
    write, _, content, _ = setup
    page = content / "openai-api-pricing.html"
    page.write_text(page.read_text().replace("&quot;batch&quot;", "&quot;flex&quot;"))
    status, output = _run(write(_catalog(gpt_x_output=9.0)) + ["--content-dir", str(content)], capsys)
    assert status == 2
    assert output["delta"]["operations"] == []
    assert [(e["source_id"], e["reason"], e["result"]) for e in output["escalations"]] == [
        ("openai-api-pricing", "fingerprint_mismatch", "escalated")
    ]


def test_cli_writes_the_json_report_when_asked(setup, capsys, tmp_path):
    write, _, content, _ = setup
    report = tmp_path / "report.json"
    status, output = _run(write() + ["--content-dir", str(content), "--json", str(report)], capsys)
    assert json.loads(report.read_text()) == output


def test_cli_limits_the_run_to_named_sources(setup, capsys):
    write, _, content, _ = setup
    status, output = _run(write() + ["--content-dir", str(content), "--sources", "anthropic-api-pricing"], capsys)
    assert [o["source_id"] for o in output["observations"]] == ["anthropic-api-pricing"]


def test_cli_rejects_an_unregistered_source_with_exit_one(setup, capsys):
    write, _, content, _ = setup
    status = refresh_prices.main(write() + ["--content-dir", str(content), "--sources", "unknown-pricing"])
    assert status == 1
    assert "unknown source 'unknown-pricing'" in capsys.readouterr().err


def test_cli_reports_an_invalid_catalog_with_exit_one(setup, capsys):
    write, catalog, content, _ = setup
    argv = write()
    catalog.write_text(yaml.safe_dump(dict(_catalog(), currency="EUR")))
    assert refresh_prices.main(argv + ["--content-dir", str(content)]) == 1
    assert "currency must be USD" in capsys.readouterr().err


def test_cli_fetches_exactly_the_registered_urls(setup, capsys, monkeypatch):
    write, _, _, _ = setup
    fetched = []

    def fake_fetch(url, allowed):
        fetched.append(url)
        source_id = "openai-api-pricing" if url == OPENAI_URL else "anthropic-api-pricing"
        return (FIXTURES / f"{source_id}.html").read_text()

    monkeypatch.setattr(refresh_prices, "fetch_text", fake_fetch)
    status, output = _run(write(), capsys)
    assert status == 0
    assert fetched == [OPENAI_URL, ANTHROPIC_URL]


def test_fetch_refuses_a_url_outside_the_registry():
    with pytest.raises(ValueError, match="not a registered source URL"):
        refresh_prices.fetch_text("https://example.com/elsewhere", frozenset({OPENAI_URL}))


def test_a_failed_fetch_is_observed_and_escalated(setup, capsys, monkeypatch):
    write, _, _, _ = setup

    def failing_fetch(url, allowed):
        if url == ANTHROPIC_URL:
            raise OSError("connection reset")
        return (FIXTURES / "openai-api-pricing.html").read_text()

    monkeypatch.setattr(refresh_prices, "fetch_text", failing_fetch)
    status, output = _run(write(), capsys)
    assert status == 2
    assert [(o["source_id"], o["result"]) for o in output["observations"]] == [
        ("openai-api-pricing", "unchanged"),
        ("anthropic-api-pricing", "missing"),
    ]
    assert output["escalations"][0]["reason"] == "content_missing"
    assert "connection reset" in output["escalations"][0]["detail"]


def test_cli_never_writes_the_catalog(setup, capsys):
    write, catalog, content, _ = setup
    argv = write(_catalog(gpt_x_output=9.0))
    before = hashlib.sha256(catalog.read_bytes()).hexdigest()
    refresh_prices.main(argv + ["--content-dir", str(content)])
    capsys.readouterr()
    assert hashlib.sha256(catalog.read_bytes()).hexdigest() == before


def test_evidence_keeps_raw_content_only_where_the_registry_allows(setup, capsys):
    write, _, content, tmp_path = setup
    evidence_dir = tmp_path / "evidence"
    argv = write(
        sources=[
            _source("openai-api-pricing", "openai", "openai-api", OPENAI_URL, store_raw=True),
            _source("anthropic-api-pricing", "anthropic", "anthropic-api", ANTHROPIC_URL),
        ]
    )
    refresh_prices.main(argv + ["--content-dir", str(content), "--evidence-dir", str(evidence_dir)])
    capsys.readouterr()
    openai = json.loads((evidence_dir / "openai-api-pricing.json").read_text())
    anthropic = json.loads((evidence_dir / "anthropic-api-pricing.json").read_text())
    assert openai["raw_content"] == (FIXTURES / "openai-api-pricing.html").read_text()
    assert "raw_content" not in anthropic
    assert anthropic["fingerprint"] == structural_fingerprint(
        "anthropic", (FIXTURES / "anthropic-api-pricing.html").read_text()
    )
    assert anthropic["retention"]["reason"] == "Synthetic test source."
    assert sorted(p.name for p in evidence_dir.iterdir()) == [
        "anthropic-api-pricing.json",
        "openai-api-pricing.json",
    ]
