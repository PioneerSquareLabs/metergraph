"""The daily discovery script, run against fixture content with optional
stub adapters. No test touches the network or a real LLM."""

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest
import yaml

from metergraph_core.extractors import structural_fingerprint

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "discover_prices.py"
_spec = importlib.util.spec_from_file_location("discover_prices", _SCRIPT)
discover_prices = importlib.util.module_from_spec(_spec)
sys.modules["discover_prices"] = discover_prices
_spec.loader.exec_module(discover_prices)

FIXTURES = Path(__file__).parent / "fixtures" / "sources"
OPENAI_URL = "https://example.com/openai-pricing"
FIREWORKS_URL = "https://example.com/fireworks-pricing"
NVIDIA_URL = "https://example.com/nvidia-pricing"
BEDROCK_URL = "https://example.com/bedrock-pricing"


def _fixture(source_id):
    return (FIXTURES / f"{source_id}.html").read_text()


def _evidence_policy():
    return {"store_raw": False, "redistribute_raw": False, "excerpt_chars": 0, "reason": "Synthetic test source."}


def _openai():
    dimensions = ["input_per_mtok", "cache_read_per_mtok", "cache_write_5m_per_mtok", "output_per_mtok"]
    return {
        "id": "openai-api-pricing", "provider": "openai", "channel": "openai-api", "catalog_channel": True,
        "url": OPENAI_URL, "parser": "openai", "source_shape": "token_embedded_data", "clarity": "structured",
        "dimensions": dimensions, "fingerprint": structural_fingerprint("openai", _fixture("openai-api-pricing")),
        "low_risk": {"eligible": True, "max_relative_change": "0.5", "dimensions": dimensions},
        "evidence": _evidence_policy(),
    }


def _discovery_only(source_id, provider, channel, parser, url, **overrides):
    entry = {
        "id": source_id, "provider": provider, "channel": channel, "catalog_channel": True,
        "url": url, "parser": parser, "source_shape": "token_table", "clarity": "semi_structured",
        "dimensions": ["input_per_mtok", "output_per_mtok"], "fingerprint": None,
        "low_risk": {"eligible": False, "max_relative_change": None, "dimensions": []},
        "evidence": _evidence_policy(),
    }
    entry.update(overrides)
    return entry


def _sources():
    return [
        _openai(),
        _discovery_only("fireworks-api-pricing", "fireworks", "fireworks-api", "fireworks", FIREWORKS_URL),
        _discovery_only("aws-bedrock-pricing", "aws", "aws-bedrock", "aws-bedrock", BEDROCK_URL),
        _discovery_only("aws-bedrock-geo-pricing", "aws", "aws-bedrock-geo", "aws-bedrock", BEDROCK_URL),
        _discovery_only(
            "nvidia-nim-pricing", "nvidia", "nvidia-ai-enterprise", "nvidia", NVIDIA_URL,
            catalog_channel=False, source_shape="gpu_hour_or_license", clarity="prose",
            dimensions=["gpu_hour_usd", "license_per_gpu_year_usd"],
        ),
    ]


def _row(**rates):
    return dict({"channel": "openai-api", "region": "global", "effective_from": "2026-01-01", "source_url": "https://example.com/model"}, **rates)


def _catalog(gpt_x_input=2.0):
    return {
        "version": "2026-10-19", "currency": "USD", "pricing_verified_at": "2026-10-19",
        "models": [
            {"canonical_id": "example/gpt-x", "aliases": [{"provider": "openai", "alias": "gpt-x", "channel": "openai-api"}],
             "prices": [_row(input_per_mtok=gpt_x_input, cache_read_per_mtok=0.2, cache_write_5m_per_mtok=2.5, output_per_mtok=8.0)]},
            {"canonical_id": "example/gpt-x-mini", "aliases": [{"provider": "openai", "alias": "gpt-x-mini", "channel": "openai-api"}],
             "prices": [_row(input_per_mtok=0.4, cache_read_per_mtok=0.04, output_per_mtok=1.6)]},
        ],
    }


@pytest.fixture
def paths(tmp_path):
    catalog = tmp_path / "prices.yaml"
    registry = tmp_path / "sources.yaml"
    content = tmp_path / "content"
    content.mkdir()
    for source_id in ("openai-api-pricing", "fireworks-api-pricing", "aws-bedrock-pricing", "aws-bedrock-geo-pricing", "nvidia-nim-pricing"):
        (content / f"{source_id}.html").write_text(_fixture(source_id))
    catalog.write_text(yaml.safe_dump(_catalog()))
    registry.write_text(yaml.safe_dump({"schema_version": 1, "sources": _sources()}))
    base = ["--catalog", str(catalog), "--registry", str(registry), "--observed-on", "2026-10-20"]
    return {"base": base, "catalog": catalog, "content": content, "tmp": tmp_path}


def _run(argv, capsys):
    status = discover_prices.main(argv)
    captured = capsys.readouterr()
    assert status == 0, captured.err
    return json.loads(captured.out)


def _proposal(output, source_id):
    [proposal] = [p for p in output["proposals"] if p["source_id"] == source_id]
    return proposal


def test_discovery_without_an_adapter_emits_deterministic_evidence(paths, capsys):
    argv = paths["base"] + ["--content-dir", str(paths["content"])]
    first = _run(argv, capsys)
    second = _run(argv, capsys)
    assert first == second
    assert [p["source_id"] for p in first["proposals"]] == [
        "fireworks-api-pricing", "aws-bedrock-pricing", "aws-bedrock-geo-pricing", "nvidia-nim-pricing",
    ]
    fireworks = _proposal(first, "fireworks-api-pricing")
    assert fireworks["requires_review"] is True
    assert fireworks["reasons"] == ["not_low_risk"]
    assert fireworks["evidence"]["evidence"]["kind"] == "tables"
    assert fireworks["proposed_delta"] is None
    assert fireworks["adapter"] is None
    nvidia = _proposal(first, "nvidia-nim-pricing")
    assert nvidia["evidence"]["evidence"]["candidates"] == {"license_per_gpu_year_usd": "1000", "gpu_hour_usd": "0.50"}
    assert nvidia["proposed_delta"] is None


def test_discovery_output_carries_no_applicable_delta(paths, capsys):
    output = _run(paths["base"] + ["--content-dir", str(paths["content"])], capsys)
    assert "delta" not in output
    assert all(p["requires_review"] is True for p in output["proposals"])


def test_discovery_fetches_only_registry_urls_once_each(paths, capsys, monkeypatch):
    fetched = []
    pages = {FIREWORKS_URL: "fireworks-api-pricing", BEDROCK_URL: "aws-bedrock-pricing", NVIDIA_URL: "nvidia-nim-pricing"}

    def fake_fetch(url, allowed):
        assert url in allowed
        fetched.append(url)
        return _fixture(pages[url])

    monkeypatch.setattr(discover_prices, "fetch_text", fake_fetch)
    _run(paths["base"], capsys)
    assert fetched == [FIREWORKS_URL, BEDROCK_URL, NVIDIA_URL]


def _escalation_report(tmp, reason="bound_exceeded"):
    report = tmp / "refresh.json"
    report.write_text(json.dumps({
        "schema_version": 1,
        "observed_on": "2026-10-20",
        "escalations": [{"result": "escalated", "source_id": "openai-api-pricing", "reason": reason, "key": None,
                         "model_key": "gpt-x", "region": "global", "dimension": "input_per_mtok",
                         "current": "1", "observed": "2", "detail": "relative change 1 exceeds 0.5"}],
    }))
    return report


def test_escalated_low_risk_sources_get_reviewable_proposed_deltas(paths, capsys):
    paths["catalog"].write_text(yaml.safe_dump(_catalog(gpt_x_input=1.0)))
    report = _escalation_report(paths["tmp"])
    output = _run(paths["base"] + ["--content-dir", str(paths["content"]), "--sources", "", "--escalations", str(report)], capsys)
    [proposal] = output["proposals"]
    assert proposal["source_id"] == "openai-api-pricing"
    assert proposal["reasons"] == ["escalation:bound_exceeded"]
    assert proposal["escalations"][0]["model_key"] == "gpt-x"
    delta = proposal["proposed_delta"]
    assert delta["provenance"]["requires_review"] is True
    assert delta["provenance"]["observations"] == ["openai-api-pricing@2026-10-20"]
    [operation] = delta["operations"]
    assert operation["key"]["canonical_id"] == "example/gpt-x"
    assert operation["price"]["input_per_mtok"] == "2"


def test_a_new_model_is_evidence_and_never_an_operation(paths, capsys):
    report = _escalation_report(paths["tmp"], reason="unknown_model")
    output = _run(paths["base"] + ["--content-dir", str(paths["content"]), "--sources", "", "--escalations", str(report)], capsys)
    proposal = _proposal(output, "openai-api-pricing")
    assert [c["model_key"] for c in proposal["evidence"]["candidates"] if c["reason"] == "unknown_model"] == ["gpt-legacy"]
    assert proposal["proposed_delta"] is None


def test_a_new_source_is_read_from_a_file(paths, capsys):
    page = paths["tmp"] / "new-provider.html"
    page.write_text("<table><tr><th>Model</th><th>Input</th></tr><tr><td>New X</td><td>$1</td></tr></table>")
    output = _run(paths["base"] + ["--content-dir", str(paths["content"]), "--sources", "", "--new-source", str(page)], capsys)
    [new_source] = output["new_sources"]
    assert new_source["requires_review"] is True
    assert new_source["label"] == "new-provider.html"
    # A new source has no reviewed retention policy, so only normalized facts
    # are reported, never its cells.
    assert new_source["evidence"] == {
        "kind": "tables",
        "redacted": True,
        "tables": [{"columns": 2, "rows": [{"label": "new-x", "amounts": ["1"]}]}],
    }
    assert "New X" not in json.dumps(output)
    assert len(new_source["sha256"]) == 64


def test_a_new_source_is_read_from_stdin(paths, capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("<table><tr><th>Plan</th></tr></table>"))
    output = _run(paths["base"] + ["--content-dir", str(paths["content"]), "--sources", "", "--new-source", "-"], capsys)
    [new_source] = output["new_sources"]
    assert new_source["label"] == "stdin"
    assert new_source["evidence"]["tables"] == [{"columns": 1, "rows": []}]


def test_the_report_repeats_no_page_cells_the_registry_forbids_keeping(paths, capsys):
    output = _run(paths["base"] + ["--content-dir", str(paths["content"])], capsys)
    text = json.dumps(output)
    for cell in ("Example model 70B", "$ / 1M input tokens", "Example Provider models", "These licenses start at"):
        assert cell not in text
    assert _proposal(output, "fireworks-api-pricing")["evidence"]["evidence"]["tables"][0]["rows"][0] == {
        "label": "example-model-70b",
        "amounts": ["0.90", "0.45", "0.90"],
    }


@pytest.mark.parametrize("value", ["https://example.com/new-pricing", "http://example.com/x", "file:///tmp/page.html"])
def test_a_new_source_url_is_refused(paths, capsys, monkeypatch, value):
    monkeypatch.setattr(discover_prices, "fetch_text", lambda url, allowed: pytest.fail("fetched"))
    assert discover_prices.main(paths["base"] + ["--sources", "", "--new-source", value]) == 1
    assert "never a URL" in capsys.readouterr().err


def _adapter(tmp, monkeypatch, body):
    module = tmp / "stub_pricing_adapter.py"
    module.write_text(body)
    monkeypatch.syspath_prepend(str(tmp))
    sys.modules.pop("stub_pricing_adapter", None)
    return "stub_pricing_adapter:propose"


RECORDING_ADAPTER = '''
CALLS = []

def propose(*args, **kwargs):
    CALLS.append((args, kwargs))
    return []
'''


def test_the_adapter_receives_only_the_source_its_content_and_current_rows(paths, capsys, monkeypatch):
    spec = _adapter(paths["tmp"], monkeypatch, RECORDING_ADAPTER)
    _run(paths["base"] + ["--content-dir", str(paths["content"]), "--sources", "fireworks-api-pricing", "--llm-adapter", spec], capsys)
    import stub_pricing_adapter

    [(args, kwargs)] = stub_pricing_adapter.CALLS
    assert kwargs == {}
    source, content, current_rows = args
    assert source == {
        "id": "fireworks-api-pricing", "provider": "fireworks", "channel": "fireworks-api", "url": FIREWORKS_URL,
        "parser": "fireworks", "source_shape": "token_table", "clarity": "semi_structured",
        "dimensions": ["input_per_mtok", "output_per_mtok"],
    }
    assert content == _fixture("fireworks-api-pricing")
    assert current_rows == []


def test_the_adapter_sees_current_rows_on_its_channel(paths, capsys, monkeypatch):
    spec = _adapter(paths["tmp"], monkeypatch, RECORDING_ADAPTER)
    report = _escalation_report(paths["tmp"])
    _run(paths["base"] + ["--content-dir", str(paths["content"]), "--sources", "", "--escalations", str(report), "--llm-adapter", spec], capsys)
    import stub_pricing_adapter

    [(args, _)] = stub_pricing_adapter.CALLS
    assert [(r["canonical_id"], r["input_per_mtok"]) for r in args[2]] == [("example/gpt-x", 2.0), ("example/gpt-x-mini", 0.4)]


PROPOSING_ADAPTER = '''
ROW = {"channel": "fireworks-api", "region": "global", "effective_from": "2026-10-20",
       "input_per_mtok": "0.9", "output_per_mtok": "0.9", "source_url": "https://example.com/fireworks-pricing"}
KEY = {"canonical_id": "example/gpt-x", "channel": "fireworks-api", "region": "global", "effective_from": "2026-10-20"}

def propose(source, content, current_rows):
    return [
        {"op": "add_price", "key": KEY, "expect": None, "price": ROW},
        {"op": "add_price", "key": KEY, "expect": None, "price": dict(ROW, input_per_mtok=0.9)},
        {"op": "add_price", "key": dict(KEY, channel="openai-api"), "expect": None, "price": dict(ROW, channel="openai-api")},
        {"op": "add_price", "key": KEY, "expect": None, "price": ROW, "low_risk": True},
        "not an operation",
    ]
'''


def test_adapter_output_is_schema_validated_and_invalid_items_are_rejected_with_reasons(paths, capsys, monkeypatch):
    spec = _adapter(paths["tmp"], monkeypatch, PROPOSING_ADAPTER)
    output = _run(paths["base"] + ["--content-dir", str(paths["content"]), "--sources", "fireworks-api-pricing", "--llm-adapter", spec], capsys)
    proposal = _proposal(output, "fireworks-api-pricing")
    adapter = proposal["adapter"]
    assert adapter["name"] == "stub_pricing_adapter:propose"
    assert [op["key"]["channel"] for op in adapter["operations"]] == ["fireworks-api"]
    assert [(r["index"], r["error"]) for r in adapter["rejected"]] == [
        (1, "operation 0: price input_per_mtok must be a non-negative decimal string"),
        (2, "an adapter may only propose rows on the source's channel fireworks-api"),
        (3, "unknown operation field 'low_risk'"),
        (4, "operation 0: must be a mapping"),
    ]
    assert proposal["requires_review"] is True
    assert proposal["proposed_delta"] is None


def test_an_adapter_cannot_mark_anything_low_risk_or_applicable(paths, capsys, monkeypatch):
    spec = _adapter(paths["tmp"], monkeypatch, PROPOSING_ADAPTER)
    output = _run(paths["base"] + ["--content-dir", str(paths["content"]), "--sources", "fireworks-api-pricing", "--llm-adapter", spec], capsys)
    text = json.dumps(output)
    assert '"low_risk"' not in text
    assert "delta" not in output
    for proposal in output["proposals"]:
        assert proposal["requires_review"] is True


def test_a_failing_adapter_is_reported_without_stopping_discovery(paths, capsys, monkeypatch):
    spec = _adapter(paths["tmp"], monkeypatch, "def propose(source, content, rows):\n    raise RuntimeError('model unavailable')\n")
    output = _run(paths["base"] + ["--content-dir", str(paths["content"]), "--sources", "fireworks-api-pricing", "--llm-adapter", spec], capsys)
    adapter = _proposal(output, "fireworks-api-pricing")["adapter"]
    assert adapter["operations"] == []
    assert adapter["error"] == "RuntimeError: model unavailable"


def test_an_adapter_that_returns_a_non_list_is_reported(paths, capsys, monkeypatch):
    spec = _adapter(paths["tmp"], monkeypatch, "def propose(source, content, rows):\n    return {'op': 'add_price'}\n")
    output = _run(paths["base"] + ["--content-dir", str(paths["content"]), "--sources", "fireworks-api-pricing", "--llm-adapter", spec], capsys)
    assert _proposal(output, "fireworks-api-pricing")["adapter"]["error"] == "the adapter must return a list"


# --- The report never overwrites an input -----------------------------------------


def _refused(argv, capsys, *inputs):
    before = {path: path.read_bytes() for path in inputs}
    status = discover_prices.main(argv)
    captured = capsys.readouterr()
    assert status == 1
    assert "refusing to write" in captured.err
    assert captured.out == ""
    assert {path: path.read_bytes() for path in inputs} == before


def test_the_report_may_not_overwrite_the_catalog(paths, capsys):
    catalog = paths["catalog"]
    _refused(paths["base"] + ["--content-dir", str(paths["content"]), "--json", str(catalog)], capsys, catalog)


def test_the_report_may_not_overwrite_the_catalog_through_a_symlink(paths, capsys):
    link = paths["tmp"] / "report.json"
    link.symlink_to(paths["catalog"])
    _refused(paths["base"] + ["--content-dir", str(paths["content"]), "--json", str(link)], capsys, paths["catalog"])


def test_the_report_may_not_overwrite_the_registry(paths, capsys):
    registry = paths["tmp"] / "sources.yaml"
    _refused(paths["base"] + ["--content-dir", str(paths["content"]), "--json", str(registry)], capsys, registry)


def test_the_report_may_not_overwrite_the_escalations_it_read(paths, capsys):
    report = _escalation_report(paths["tmp"])
    _refused(
        paths["base"] + ["--content-dir", str(paths["content"]), "--sources", "", "--escalations", str(report), "--json", str(report)],
        capsys,
        report,
    )


def test_the_report_may_not_overwrite_the_new_source_file_through_a_symlink(paths, capsys):
    page = paths["tmp"] / "new-provider.html"
    page.write_text("<table><tr><th>Model</th></tr></table>")
    link = paths["tmp"] / "out.json"
    link.symlink_to(page)
    _refused(
        paths["base"] + ["--content-dir", str(paths["content"]), "--sources", "", "--new-source", str(page), "--json", str(link)],
        capsys,
        page,
    )


def test_the_report_may_not_overwrite_a_content_file_it_read(paths, capsys):
    page = paths["content"] / "fireworks-api-pricing.html"
    _refused(
        paths["base"] + ["--content-dir", str(paths["content"]), "--sources", "fireworks-api-pricing", "--json", str(page)],
        capsys,
        page,
    )


def test_a_report_beside_the_inputs_is_written(paths, capsys):
    report = paths["tmp"] / "discovery.json"
    output = _run(paths["base"] + ["--content-dir", str(paths["content"]), "--json", str(report)], capsys)
    assert json.loads(report.read_text()) == output


def test_an_unloadable_adapter_is_an_error(paths, capsys):
    assert discover_prices.main(paths["base"] + ["--content-dir", str(paths["content"]), "--llm-adapter", "no_such_module:propose"]) == 1
    assert "cannot load adapter" in capsys.readouterr().err
