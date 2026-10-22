"""The installed package stays a pure resolver: no network, browser or LLM
client, no new dependency, and no credentials in the public scripts."""

import ast
from pathlib import Path

import pytest

import metergraph_core

PACKAGE = Path(metergraph_core.__file__).parent
REPOSITORY = Path(__file__).resolve().parents[2]
FORBIDDEN_IMPORTS = {
    "aiohttp", "anthropic", "boto3", "botocore", "cohere", "google", "http", "httpx",
    "langchain", "litellm", "mistralai", "openai", "playwright", "pyppeteer",
    "requests", "selenium", "socket", "ssl", "subprocess", "urllib", "urllib3",
}


def _imports(path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module.split(".")[0]


def test_package_modules_import_no_network_browser_or_llm_client():
    modules = sorted(PACKAGE.glob("*.py"))
    assert {m.name for m in modules} >= {"catalog_delta.py", "extractors.py", "refresh.py", "sources.py"}
    found = {(m.name, name) for m in modules for name in _imports(m) if name in FORBIDDEN_IMPORTS}
    assert found == set()


def test_core_depends_only_on_pyyaml():
    tomllib = pytest.importorskip("tomllib")
    project = tomllib.loads((REPOSITORY / "core" / "pyproject.toml").read_text())["project"]
    assert project["dependencies"] == ["pyyaml>=6.0"]


def test_the_source_registry_ships_as_package_data():
    tomllib = pytest.importorskip("tomllib")
    config = tomllib.loads((REPOSITORY / "core" / "pyproject.toml").read_text())
    assert sorted(config["tool"]["setuptools"]["package-data"]["metergraph_core"]) == [
        "data/models.yaml", "data/prices.yaml", "data/sources.yaml",
    ]


README_OPENING = (
    "Use an open LLM model registry and effective-dated pricing catalog in Python. "
    "`metergraph-core` ships both datasets with deterministic price lookup and "
    "cost calculation. It works locally without an API key or runtime network "
    "request."
)


def test_core_readme_opens_with_the_model_and_pricing_pitch():
    readme = (REPOSITORY / "core" / "README.md").read_text()
    title, _, rest = readme.partition("\n\n")
    assert title == "# metergraph-core"
    opening = rest.partition("\n\n")[0]
    assert " ".join(opening.split()) == README_OPENING
    assert readme.index("## Install") < readme.index("## Usage")
    assert readme.index("## Usage") < readme.index("does not own HTTP routes")


def test_core_metadata_targets_model_and_pricing_discovery():
    tomllib = pytest.importorskip("tomllib")
    project = tomllib.loads((REPOSITORY / "core" / "pyproject.toml").read_text())["project"]
    assert project["description"] == (
        "Open LLM model registry and effective-dated pricing catalog with "
        "deterministic cost calculation across providers and gateways."
    )
    assert {
        "llm", "pricing", "cost", "token-pricing", "model-catalog",
        "llm-pricing", "model-pricing", "token-cost", "cost-calculation", "openai",
        "anthropic", "gemini", "bedrock", "openrouter", "vercel-ai-gateway", "finops",
    } <= set(project["keywords"])
    assert project["license"] == "Apache-2.0"
    assert project["authors"] == [{"name": "Metergraph"}]
    assert project["requires-python"] == ">=3.10"
    assert all(
        url.startswith("https://github.com/metergraph/metergraph")
        for url in project["urls"].values()
    )


PUBLIC_NAMES = [
    "CATALOG_DELTA_SCHEMA_VERSION", "CatalogDelta", "CatalogDeltaConflict", "CatalogDeltaError", "DeltaConflict",
    "Escalation", "ExtractedRate", "Extraction", "LowRiskPolicy",
    "Observation", "PriceRowKey", "PriceSource",
    "RefreshResult", "SOURCE_REGISTRY_SCHEMA_VERSION", "SourceRegistry",
    "SourceRegistryError", "apply_catalog_delta", "discovery_evidence", "extract",
    "load_catalog_document", "load_source_registry", "parse_catalog_delta",
    "parse_source_registry", "plan_low_risk_refresh", "structural_fingerprint",
    "validate_source_registry",
]


@pytest.mark.parametrize("name", PUBLIC_NAMES)
def test_the_new_public_api_is_exported(name):
    assert name in metergraph_core.__all__
    assert getattr(metergraph_core, name) is not None


def test_obsolete_pricing_delta_schema_constant_is_not_public():
    assert not hasattr(metergraph_core, "PRICING_DELTA_SCHEMA_VERSION")


@pytest.mark.parametrize(
    "script", ["refresh_prices.py", "discover_prices.py", "apply_price_delta.py"]
)
def test_public_refresh_scripts_read_no_environment_or_credentials(script):
    tree = ast.parse((REPOSITORY / "scripts" / script).read_text())
    names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    names |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert not names & {"environ", "getenv", "putenv"}
    assert "os" not in set(_imports(REPOSITORY / "scripts" / script))
