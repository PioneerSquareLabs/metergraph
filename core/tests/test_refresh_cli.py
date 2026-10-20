"""The low-risk refresh script, run against fixture content. No test touches
the network."""

import email.message
import hashlib
import importlib.util
import io
import json
import sys
import urllib.request
import urllib.response
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


def _source(source_id, parser, channel, url):
    content = (FIXTURES / f"{source_id}.html").read_text()
    dimensions = ["input_per_mtok", "cache_read_per_mtok", "cache_write_5m_per_mtok", "output_per_mtok"]
    return {
        "id": source_id,
        "provider": parser,
        "channel": channel,
        "catalog_channel": True,
        "url": url,
        "parser": parser,
        "dimensions": dimensions,
        "fingerprint": structural_fingerprint(parser, content),
        "low_risk": {"eligible": True, "dimensions": dimensions},
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


def test_cli_emits_a_delta_for_a_change(setup, capsys):
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


def _not_low_risk(entry):
    return dict(entry, low_risk={"eligible": False, "dimensions": []})


def test_an_explicitly_selected_source_without_a_content_file_is_reported(setup, capsys):
    write, _, content, _ = setup
    sources = [
        _source("openai-api-pricing", "openai", "openai-api", OPENAI_URL),
        _not_low_risk(dict(_source("anthropic-api-pricing", "anthropic", "anthropic-api", ANTHROPIC_URL), id="extra-pricing")),
    ]
    status, output = _run(write(sources=sources) + ["--content-dir", str(content), "--sources", "extra-pricing"], capsys)
    assert status == 2
    assert [(o["source_id"], o["result"]) for o in output["observations"]] == [("extra-pricing", "missing")]
    [escalation] = output["escalations"]
    assert escalation["reason"] == "content_missing"
    assert escalation["detail"] == f"no extra-pricing.* file in {content}"


def test_an_explicitly_selected_source_whose_fetch_fails_is_reported(setup, capsys, monkeypatch):
    write, _, _, _ = setup
    sources = [
        _source("openai-api-pricing", "openai", "openai-api", OPENAI_URL),
        _not_low_risk(_source("anthropic-api-pricing", "anthropic", "anthropic-api", ANTHROPIC_URL)),
    ]

    def failing_fetch(url, allowed):
        raise OSError("connection refused")

    monkeypatch.setattr(refresh_prices, "fetch_text", failing_fetch)
    status, output = _run(write(sources=sources) + ["--sources", "anthropic-api-pricing"], capsys)
    assert status == 2
    assert [(o["source_id"], o["result"]) for o in output["observations"]] == [("anthropic-api-pricing", "missing")]
    assert output["escalations"][0]["detail"] == "fetch failed: connection refused"


def test_a_source_named_twice_is_planned_once(setup, capsys):
    write, _, content, _ = setup
    names = "anthropic-api-pricing,openai-api-pricing,anthropic-api-pricing, openai-api-pricing"
    status, output = _run(write(_catalog(gpt_x_output=9.0)) + ["--content-dir", str(content), "--sources", names], capsys)
    assert status == 2
    assert [o["source_id"] for o in output["observations"]] == ["anthropic-api-pricing", "openai-api-pricing"]
    assert [op["key"]["canonical_id"] for op in output["delta"]["operations"]] == ["example/gpt-x"]
    assert output["delta"]["provenance"]["observations"] == [
        "anthropic-api-pricing@2026-10-20",
        "openai-api-pricing@2026-10-20",
    ]


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


class _FakeSite(urllib.request.BaseHandler):
    """Serves https URLs from memory: a page body, or a redirect."""

    handler_order = 100  # ahead of the real HTTPS handler

    def __init__(self, pages):
        self.pages = pages
        self.opened = []

    def https_open(self, request):
        self.opened.append(request.full_url)
        kind, value = self.pages[request.full_url]
        headers = email.message.Message()
        if kind == "redirect":
            headers["Location"] = value
            response = urllib.response.addinfourl(io.BytesIO(b""), headers, request.full_url, 302)
            response.msg = "Found"
        else:
            response = urllib.response.addinfourl(io.BytesIO(value.encode()), headers, request.full_url, 200)
            response.msg = "OK"
        return response


def _serve(monkeypatch, pages):
    site = _FakeSite(pages)
    real = refresh_prices.build_allowlisted_opener

    def opener_with_site(allowed):
        opener = real(allowed)
        opener.add_handler(site)
        return opener

    monkeypatch.setattr(refresh_prices, "build_allowlisted_opener", opener_with_site)
    return site


SECOND_URL = "https://example.com/openai-pricing-moved"


def test_fetch_reads_a_registered_page_without_redirects(monkeypatch):
    site = _serve(monkeypatch, {OPENAI_URL: ("page", "<p>prices</p>")})
    assert refresh_prices.fetch_text(OPENAI_URL, frozenset({OPENAI_URL})) == "<p>prices</p>"
    assert site.opened == [OPENAI_URL]


def test_fetch_follows_a_redirect_to_another_registered_url(monkeypatch):
    site = _serve(monkeypatch, {OPENAI_URL: ("redirect", SECOND_URL), SECOND_URL: ("page", "<p>moved</p>")})
    assert refresh_prices.fetch_text(OPENAI_URL, frozenset({OPENAI_URL, SECOND_URL})) == "<p>moved</p>"
    assert site.opened == [OPENAI_URL, SECOND_URL]


def test_fetch_refuses_a_redirect_outside_the_registry(monkeypatch):
    elsewhere = "https://example.org/somewhere-else"
    site = _serve(monkeypatch, {OPENAI_URL: ("redirect", elsewhere), elsewhere: ("page", "<p>other</p>")})
    with pytest.raises(ValueError, match="redirect to https://example.org/somewhere-else is not a registered source URL"):
        refresh_prices.fetch_text(OPENAI_URL, frozenset({OPENAI_URL}))
    assert site.opened == [OPENAI_URL]


def test_fetch_checks_every_redirect_hop(monkeypatch):
    elsewhere = "https://example.org/third"
    site = _serve(
        monkeypatch,
        {OPENAI_URL: ("redirect", SECOND_URL), SECOND_URL: ("redirect", elsewhere), elsewhere: ("page", "x")},
    )
    with pytest.raises(ValueError, match="redirect to https://example.org/third"):
        refresh_prices.fetch_text(OPENAI_URL, frozenset({OPENAI_URL, SECOND_URL}))
    assert site.opened == [OPENAI_URL, SECOND_URL]


def test_a_refused_redirect_is_observed_and_escalated(setup, capsys, monkeypatch):
    write, _, _, _ = setup
    _serve(
        monkeypatch,
        {
            OPENAI_URL: ("page", (FIXTURES / "openai-api-pricing.html").read_text()),
            ANTHROPIC_URL: ("redirect", "https://example.org/login"),
        },
    )
    status, output = _run(write(), capsys)
    assert status == 2
    [escalation] = output["escalations"]
    assert (escalation["source_id"], escalation["reason"]) == ("anthropic-api-pricing", "content_missing")
    assert "https://example.org/login is not a registered source URL" in escalation["detail"]


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


# --- Outputs never overwrite inputs ------------------------------------------------


def _snapshot(*paths):
    return {path: path.read_bytes() for path in paths}


def _refused(argv, capsys, *inputs):
    before = _snapshot(*inputs)
    status = refresh_prices.main(argv)
    captured = capsys.readouterr()
    assert status == 1
    assert "refusing to write" in captured.err
    assert captured.out == ""
    assert _snapshot(*inputs) == before
    return captured.err


def test_the_report_may_not_overwrite_the_catalog(setup, capsys):
    write, catalog, content, tmp_path = setup
    argv = write(_catalog(gpt_x_output=9.0))
    _refused(argv + ["--content-dir", str(content), "--json", str(catalog)], capsys, catalog)


def test_the_report_may_not_overwrite_the_registry_through_a_symlink(setup, capsys):
    write, _, content, tmp_path = setup
    argv = write()
    registry = tmp_path / "sources.yaml"
    link = tmp_path / "report.json"
    link.symlink_to(registry)
    _refused(argv + ["--content-dir", str(content), "--json", str(link)], capsys, registry)


def test_the_report_may_not_overwrite_the_catalog_through_a_symlinked_directory(setup, capsys):
    write, catalog, content, tmp_path = setup
    argv = write()
    linked_dir = tmp_path / "linked"
    linked_dir.symlink_to(tmp_path, target_is_directory=True)
    _refused(argv + ["--content-dir", str(content), "--json", str(linked_dir / "prices.yaml")], capsys, catalog)


def test_the_report_may_not_overwrite_a_content_file_it_read(setup, capsys):
    write, _, content, _ = setup
    page = content / "openai-api-pricing.html"
    _refused(write() + ["--content-dir", str(content), "--json", str(page)], capsys, page)


def test_evidence_files_may_not_overwrite_content_files_they_read(setup, capsys, tmp_path):
    write, _, content, _ = setup
    # A content file named like the evidence file the run would write.
    (content / "openai-api-pricing.html").rename(content / "openai-api-pricing.json")
    page = content / "openai-api-pricing.json"
    report = tmp_path / "report.json"
    err = _refused(
        write() + ["--content-dir", str(content), "--evidence-dir", str(content), "--json", str(report)],
        capsys,
        page,
    )
    assert "openai-api-pricing.json" in err
    assert not report.exists()


def test_the_report_may_not_overwrite_an_evidence_file(setup, capsys, tmp_path):
    write, _, content, _ = setup
    evidence = tmp_path / "evidence"
    status = refresh_prices.main(
        write() + ["--content-dir", str(content), "--evidence-dir", str(evidence), "--json", str(evidence / "openai-api-pricing.json")]
    )
    assert status == 1
    assert "refusing to write" in capsys.readouterr().err
    assert not evidence.exists()


def test_a_new_report_path_beside_the_inputs_is_written(setup, capsys, tmp_path):
    write, _, content, _ = setup
    report = tmp_path / "fresh-report.json"
    status, output = _run(write() + ["--content-dir", str(content), "--json", str(report)], capsys)
    assert status == 0
    assert json.loads(report.read_text()) == output


# --- Output failures ---------------------------------------------------------------


def test_an_unwritable_report_path_exits_one_without_a_report(setup, capsys, tmp_path):
    write, _, content, _ = setup
    report = tmp_path / "missing-directory" / "report.json"
    status = refresh_prices.main(write() + ["--content-dir", str(content), "--json", str(report)])
    captured = capsys.readouterr()
    assert status == 1
    assert captured.out == ""
    assert f"refresh_prices: cannot write {report}" in captured.err
    assert "Claude Example" not in captured.err


def test_an_unwritable_evidence_directory_exits_one_without_a_report(setup, capsys, tmp_path):
    write, _, content, _ = setup
    not_a_directory = tmp_path / "evidence"
    not_a_directory.write_text("occupied")
    status = refresh_prices.main(write() + ["--content-dir", str(content), "--evidence-dir", str(not_a_directory)])
    captured = capsys.readouterr()
    assert status == 1
    assert captured.out == ""
    assert "refresh_prices: cannot write" in captured.err
    assert not_a_directory.read_text() == "occupied"


def test_cli_never_writes_the_catalog(setup, capsys):
    write, catalog, content, _ = setup
    argv = write(_catalog(gpt_x_output=9.0))
    before = hashlib.sha256(catalog.read_bytes()).hexdigest()
    refresh_prices.main(argv + ["--content-dir", str(content)])
    capsys.readouterr()
    assert hashlib.sha256(catalog.read_bytes()).hexdigest() == before


def test_evidence_files_never_contain_raw_page_content(setup, capsys):
    write, _, content, tmp_path = setup
    evidence_dir = tmp_path / "evidence"
    refresh_prices.main(write() + ["--content-dir", str(content), "--evidence-dir", str(evidence_dir)])
    capsys.readouterr()
    for source_id in ("openai-api-pricing", "anthropic-api-pricing"):
        text = (evidence_dir / f"{source_id}.json").read_text()
        evidence = json.loads(text)
        assert not {"raw_content", "retention", "excerpt"} & set(evidence)
        assert evidence["evidence"]["redacted"] is True
    anthropic = json.loads((evidence_dir / "anthropic-api-pricing.json").read_text())
    assert anthropic["fingerprint"] == structural_fingerprint(
        "anthropic", (FIXTURES / "anthropic-api-pricing.html").read_text()
    )
    # No page text survives: the fixture's table header and model labels.
    text = (evidence_dir / "anthropic-api-pricing.json").read_text()
    assert "Base tokens" not in text and "Claude Example" not in text
    assert sorted(p.name for p in evidence_dir.iterdir()) == [
        "anthropic-api-pricing.json",
        "openai-api-pricing.json",
    ]
