# Canonical Model Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a packaged, typed `models.yaml` registry to `metergraph-core` that exactly represents the current pipeline execution pools and internal-app offer groups without moving pricing policy out of `prices.yaml`.

**Architecture:** A new `models.py` module parses immutable model, route, and offer-group records from bundled YAML. Routes describe executable and priceable paths; offer groups separately define the ordered product fields exposed for each credential, preserving the existing difference between the pipeline's broad default pool and the app's narrower gateway field. Cross-catalog validation proves that every registry route resolves to its declared canonical model and pricing channel.

**Tech Stack:** Python 3.10+, frozen dataclasses, `MappingProxyType`, PyYAML, pytest, setuptools package data.

**Spec:** `docs/superpowers/specs/2026-09-25-model-registry-design.md`

## Global Constraints

- `models.yaml` owns identity, display, execution-route, execution-profile, credential, and offer-group metadata only.
- Token rates, effective dates, billing rules, and source URLs remain exclusively in `prices.yaml`.
- The initial registry reproduces current consumer order and membership exactly; it does not add or retire a candidate.
- Execution profiles are `default` and `bedrock`.
- Offer groups are `gateway`, `fireworks`, `openai-direct`, `anthropic-direct`, and `bedrock`.
- The registry version is independent from the price-catalog version and package version.
- Public values are immutable, and malformed or ambiguous registry documents fail closed.
- The core PR is additive; pipeline and internal migrations happen in subsequent PRs.

## Review Focus

- A route ID referenced by two models must be rejected rather than resolved by insertion order; Task 1 adds this parser test.
- A product offer group referencing a nonexistent or incompatible route must be rejected; Task 1 adds both cases.
- A direct and gateway route for the same canonical model must remain distinct and keep route-specific display names; Task 1 adds this projection test.
- A registry route whose declared canonical model or pricing channel disagrees with `prices.yaml` must fail cross-catalog validation; Task 2 adds both mismatch tests.
- Packaged wheels must load `models.yaml` without access to the source checkout; Task 4 extends the isolated-install artifact test.

---

### Task 1: Typed registry parser and projections

**Files:**
- Create: `core/src/metergraph_core/models.py`
- Create: `core/tests/test_models.py`

**Interfaces:**
- Consumes: plain Python mappings produced by `yaml.safe_load`.
- Produces: `ModelRegistryError`, `ModelRoute`, `ModelDefinition`, `OfferGroup`, `ModelRegistry`, and `parse_model_registry(document: Any) -> ModelRegistry`.
- `ModelRegistry.route(route_key: str) -> ModelRoute` returns one exact route or raises `KeyError`.
- `ModelRegistry.routes_for_execution_profile(profile: str) -> tuple[ModelRoute, ...]` preserves the explicit route order declared by that execution profile.
- `ModelRegistry.candidates(offer_group: str) -> tuple[ModelRoute, ...]` preserves the offer group's declared order.
- `ModelRegistry.reachable_candidates(credentials: Iterable[str]) -> tuple[ModelRoute, ...]` concatenates credential-backed offer groups in document order and deduplicates route IDs; groups without a credential are not inferred.

- [ ] **Step 1: Write parser and projection tests that fail because the module does not exist**

```python
from types import MappingProxyType

import pytest

from metergraph_core.models import ModelRegistryError, parse_model_registry


def document():
    return {
        "version": "2026-09-25",
        "models": [
            {
                "canonical_id": "openai/gpt-5.6-sol",
                "display_name": "GPT-5.6 Sol",
                "publisher": "openai",
                "routes": [
                    {
                        "id": "openai/gpt-5.6-sol",
                        "provider": "vercel",
                        "model_id": "openai/gpt-5.6-sol",
                        "pricing_channel": "vercel-ai-gateway",
                        "execution_profiles": ["default"],
                    },
                    {
                        "id": "openai:gpt-5.6",
                        "provider": "openai",
                        "model_id": "gpt-5.6",
                        "display_name": "GPT-5.6 Sol (direct)",
                        "pricing_channel": "openai-api",
                        "execution_profiles": ["default"],
                    },
                ],
            }
        ],
        "offer_groups": [
            {
                "id": "gateway",
                "credential": "AI_GATEWAY_API_KEY",
                "routes": ["openai/gpt-5.6-sol"],
            },
            {
                "id": "openai-direct",
                "credential": "OPENAI_API_KEY",
                "routes": ["openai:gpt-5.6"],
            },
        ],
    }


def test_parse_model_registry_keeps_routes_immutable_and_distinct():
    registry = parse_model_registry(document())
    gateway, direct = registry.routes_for_execution_profile("default")
    assert gateway.canonical_id == direct.canonical_id == "openai/gpt-5.6-sol"
    assert gateway.display_name == "GPT-5.6 Sol"
    assert direct.display_name == "GPT-5.6 Sol (direct)"
    assert isinstance(registry.models, MappingProxyType)


def test_offer_groups_and_credentials_preserve_declared_order():
    registry = parse_model_registry(document())
    assert [r.id for r in registry.candidates("gateway")] == ["openai/gpt-5.6-sol"]
    assert [r.id for r in registry.reachable_candidates({"OPENAI_API_KEY"})] == ["openai:gpt-5.6"]


@pytest.mark.parametrize("mutation, message", [
    ("duplicate_route", "duplicate route id"),
    ("unknown_offer_route", "unknown route"),
    ("incompatible_offer_route", "not available to an execution profile"),
])
def test_invalid_registry_references_fail_closed(mutation, message):
    value = document()
    if mutation == "duplicate_route":
        value["models"].append({**value["models"][0], "canonical_id": "other/model"})
    elif mutation == "unknown_offer_route":
        value["offer_groups"][0]["routes"] = ["missing/model"]
    else:
        value["models"][0]["routes"][0]["execution_profiles"] = []
    with pytest.raises(ModelRegistryError, match=message):
        parse_model_registry(value)
```

- [ ] **Step 2: Run the focused tests and verify the missing-module failure**

Run: `./.venv/bin/pytest core/tests/test_models.py -v`

Expected: collection fails with `ModuleNotFoundError: No module named 'metergraph_core.models'`.

- [ ] **Step 3: Implement immutable records and strict parsing**

```python
class ModelRegistryError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ModelRoute:
    id: str
    canonical_id: str
    display_name: str
    provider: str
    model_id: str
    pricing_channel: str
    execution_profiles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ModelDefinition:
    canonical_id: str
    display_name: str
    publisher: str
    routes: tuple[ModelRoute, ...]


@dataclass(frozen=True, slots=True)
class OfferGroup:
    id: str
    credential: str | None
    route_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ModelRegistry:
    version: str
    models: Mapping[str, ModelDefinition]
    routes: Mapping[str, ModelRoute]
    offer_groups: Mapping[str, OfferGroup]

    def route(self, candidate_id: str) -> ModelRoute:
        return self.routes[candidate_id]

    def routes_for_execution_profile(self, profile: str) -> tuple[ModelRoute, ...]:
        return tuple(route for route in self.routes.values() if profile in route.execution_profiles)

    def candidates(self, offer_group: str) -> tuple[ModelRoute, ...]:
        group = self.offer_groups[offer_group]
        return tuple(self.routes[route_id] for route_id in group.route_ids)

    def reachable_candidates(self, credentials: Iterable[str]) -> tuple[ModelRoute, ...]:
        present = frozenset(credentials)
        seen: set[str] = set()
        result: list[ModelRoute] = []
        for group in self.offer_groups.values():
            if group.credential is None or group.credential not in present:
                continue
            for route_id in group.route_ids:
                if route_id not in seen:
                    result.append(self.routes[route_id])
                    seen.add(route_id)
        return tuple(result)
```

The parser must require nonblank strings, accept only the two declared execution profiles and five offer-group IDs, apply the model display name when a route omits its override, preserve YAML order through dictionaries, freeze public mappings with `MappingProxyType`, and reject duplicate canonical IDs, route IDs, offer-group IDs, route references, and empty route/profile lists. Validate that the `bedrock` offer group references only `bedrock` routes and the other four offer groups reference only `default` routes.

- [ ] **Step 4: Run the focused tests and verify they pass**

Run: `./.venv/bin/pytest core/tests/test_models.py -v`

Expected: all parser and projection tests pass.

- [ ] **Step 5: Commit the parser**

```bash
git add core/src/metergraph_core/models.py core/tests/test_models.py
git commit -m "feat(core): add typed model registry"
```

### Task 2: Bundle the canonical data and validate it against pricing

**Files:**
- Create: `core/src/metergraph_core/data/models.yaml`
- Modify: `core/src/metergraph_core/models.py`
- Modify: `core/tests/test_models.py`

**Interfaces:**
- Consumes: Task 1's `parse_model_registry` and existing `LoadedCatalog` from `load_catalog()`.
- Produces: `DEFAULT_MODEL_REGISTRY_PATH`, `load_model_registry(path: str | Path | None = None) -> ModelRegistry`, and `validate_model_registry(registry: ModelRegistry, catalog: LoadedCatalog) -> None`.

- [ ] **Step 1: Add failing tests for bundled parity and cross-catalog mismatches**

```python
from metergraph_core import load_catalog
from metergraph_core.models import (
    load_model_registry,
    parse_model_registry,
    validate_model_registry,
)


def test_bundled_registry_preserves_current_product_fields():
    registry = load_model_registry()
    assert registry.version == "2026-09-25"
    assert [r.id for r in registry.candidates("gateway")] == [
        "anthropic/claude-sonnet-5",
        "openai/gpt-5.6-luna",
        "openai/gpt-5.6-terra",
        "openai/gpt-6-luna",
        "openai/gpt-6-sol",
        "openai/gpt-6-astra",
        "google/gemini-3.6-flash",
        "moonshotai/kimi-k3",
        "deepseek/deepseek-v4-pro",
        "deepseek/deepseek-v4-flash",
    ]
    assert len(registry.routes_for_execution_profile("default")) == 46
    assert len(registry.candidates("bedrock")) == 8


def test_bundled_registry_routes_resolve_in_pricing_catalog():
    validate_model_registry(load_model_registry(), load_catalog())


def test_validation_rejects_canonical_and_channel_drift():
    value = document()
    value["models"][0]["canonical_id"] = "other/model"
    with pytest.raises(ModelRegistryError, match="canonical model"):
        validate_model_registry(parse_model_registry(value), load_catalog())

    value = document()
    value["models"][0]["routes"][0]["pricing_channel"] = "anthropic-api"
    with pytest.raises(ModelRegistryError, match="pricing channel"):
        validate_model_registry(parse_model_registry(value), load_catalog())
```

- [ ] **Step 2: Run the focused tests and verify missing loader/data failures**

Run: `./.venv/bin/pytest core/tests/test_models.py -v`

Expected: failures name missing `load_model_registry` and bundled data.

- [ ] **Step 3: Populate `models.yaml` from current released consumer data**

Create version `2026-09-25` with every route from pipeline `origin/main` default and Bedrock evaluation configs. Define offer groups from internal `origin/main` constants:

- `gateway`: the 20 `DEFAULT_CANDIDATE_MODELS` entries.
- `fireworks`: the 2 `FIREWORKS_CANDIDATE_MODELS` entries.
- `openai-direct`: the 6 `OPENAI_CANDIDATE_MODELS` entries.
- `anthropic-direct`: the 4 `ANTHROPIC_CANDIDATE_MODELS` entries.
- `bedrock`: the 8 `BEDROCK_CANDIDATE_MODELS` entries.

Every default pipeline route receives `execution_profiles: [default]`; every Bedrock route receives `execution_profiles: [bedrock]`. Declare ordered top-level execution-profile route lists so grouping routes under canonical models cannot change the pipeline config's order. Preserve the app constants' exact offer-group order. Use route-level display overrides only where the current display differs from the canonical model name, notably the direct-provider routes. When a canonical model appears in both execution profiles, store both route records under the same model definition and keep their route keys unique.

- [ ] **Step 4: Implement loading and price-catalog validation**

```python
DEFAULT_MODEL_REGISTRY_PATH = Path(__file__).parent / "data" / "models.yaml"


def load_model_registry(path: str | Path | None = None) -> ModelRegistry:
    resolved = Path(path) if path is not None else DEFAULT_MODEL_REGISTRY_PATH
    return parse_model_registry(yaml.safe_load(resolved.read_bytes()))


def validate_model_registry(registry: ModelRegistry, catalog: LoadedCatalog) -> None:
    for route in registry.routes.values():
        resolved = catalog.snapshot.resolve_price(
            model=route.id,
            channel=route.pricing_channel,
            at=datetime.combine(
                date.fromisoformat(registry.version),
                time.min,
                tzinfo=timezone.utc,
            ),
        )
        if resolved is None:
            raise ModelRegistryError(
                f"{route.id}: no price for pricing channel {route.pricing_channel}"
            )
        if resolved.canonical_model != route.canonical_id:
            raise ModelRegistryError(
                f"{route.id}: pricing resolves canonical model "
                f"{resolved.canonical_model}, expected {route.canonical_id}"
            )
```

The registry version is an ISO date and supplies the validation instant. Every initial route must resolve on that date; this first schema has no pricing-exemption escape hatch.

- [ ] **Step 5: Run focused tests and the full core suite**

Run: `./.venv/bin/pytest core/tests/test_models.py -v`

Expected: all registry tests pass.

Run: `./.venv/bin/pytest core/tests -q`

Expected: full core suite passes.

- [ ] **Step 6: Commit registry data and validation**

```bash
git add core/src/metergraph_core/data/models.yaml core/src/metergraph_core/models.py core/tests/test_models.py
git commit -m "feat(core): bundle canonical model registry"
```

### Task 3: Publish and document the core API

**Files:**
- Modify: `core/src/metergraph_core/__init__.py`
- Modify: `core/README.md`
- Modify: `README.md`
- Modify: `docs/prices.md`
- Test: `core/tests/test_models.py`

**Interfaces:**
- Consumes: Task 2's registry classes and functions.
- Produces: stable top-level imports for downstream pipeline and internal PRs.

- [ ] **Step 1: Add a failing public-import test**

```python
def test_registry_api_is_public():
    from metergraph_core import (
        ModelDefinition,
        ModelRegistry,
        ModelRegistryError,
        ModelRoute,
        OfferGroup,
        load_model_registry,
        parse_model_registry,
        validate_model_registry,
    )
    assert load_model_registry().route("openai/gpt-6-luna").display_name == "GPT-6 Luna"
```

- [ ] **Step 2: Run the test and verify imports fail**

Run: `./.venv/bin/pytest core/tests/test_models.py::test_registry_api_is_public -v`

Expected: import failure for the new top-level names.

- [ ] **Step 3: Export the registry API and document the ownership boundary**

Update `metergraph_core.__all__` and imports with every name in the test. Update core's description from only a pricing engine to a reusable model registry and deterministic billing engine. Add a README example:

```python
from metergraph_core import load_model_registry

models = load_model_registry()
for candidate in models.candidates("gateway"):
    print(candidate.id, candidate.display_name, candidate.pricing_channel)
```

Document that `models.yaml` is the manually maintained identity and execution registry, while `prices.yaml` remains the sole pricing source. In `docs/prices.md`, add the required cross-check when a pricing change adds or removes a route used by `models.yaml`.

- [ ] **Step 4: Run focused and full tests**

Run: `./.venv/bin/pytest core/tests/test_models.py -v`

Expected: public import and all registry tests pass.

Run: `./.venv/bin/pytest core/tests -q`

Expected: full core suite passes.

- [ ] **Step 5: Commit the public API and documentation**

```bash
git add core/src/metergraph_core/__init__.py core/tests/test_models.py core/README.md README.md docs/prices.md
git commit -m "docs(core): publish model registry contract"
```

### Task 4: Package and release the registry

**Files:**
- Modify: `core/pyproject.toml`
- Modify: `core/tests/package/verify_artifacts.py`
- Test: `core/tests/package/verify_artifacts.py`

**Interfaces:**
- Consumes: Task 3's public API and bundled resource.
- Produces: `metergraph-core` 0.2.33 wheel and sdist containing exactly one `models.yaml`, loadable in an isolated environment.

- [ ] **Step 1: Extend artifact expectations before package configuration**

Add `models.py` to `REQUIRED_MODULES`, require exactly one `metergraph_core/data/models.yaml` beside `prices.yaml` in both wheel and sdist, set `EXPECTED_VERSION = "0.2.33"`, and extend the isolated script:

```python
from metergraph_core import load_model_registry, validate_model_registry

registry = load_model_registry()
assert registry.version == "2026-09-25", registry.version
assert [route.id for route in registry.candidates("gateway")][:2] == [
    "anthropic/claude-sonnet-5",
    "openai/gpt-5.6-luna",
]
validate_model_registry(registry, loaded)
```

- [ ] **Step 2: Run artifact verification and verify it fails**

Run: `./.venv/bin/python core/tests/package/verify_artifacts.py`

Expected: failure because package version remains 0.2.32 or `models.yaml` is absent from the built artifact.

- [ ] **Step 3: Update package data and patch version**

```toml
[project]
version = "0.2.33"

[tool.setuptools.package-data]
metergraph_core = ["data/prices.yaml", "data/models.yaml"]
```

- [ ] **Step 4: Run release verification**

Run: `./.venv/bin/pytest core/tests -q`

Expected: full core suite passes.

Run: `./.venv/bin/python core/tests/package/verify_artifacts.py`

Expected: wheel and sdist checks pass, including the isolated registry load and cross-catalog validation.

- [ ] **Step 5: Commit release metadata**

```bash
git add core/pyproject.toml core/tests/package/verify_artifacts.py
git commit -m "chore(core): release model registry in 0.2.33"
```

### Task 5: Final scope and compatibility verification

**Files:**
- Verify only; no planned source changes.

**Interfaces:**
- Consumes: complete branch.
- Produces: evidence that the additive core PR is ready for independent review.

- [ ] **Step 1: Confirm diff scope and whitespace**

Run: `git diff --check origin/main...HEAD`

Expected: no output.

Run: `git diff --stat origin/main...HEAD`

Expected: changes are limited to the design/plan, core registry code and data, core tests, core package metadata, and registry/pricing documentation.

- [ ] **Step 2: Run the complete core verification again from a clean tree**

Run: `./.venv/bin/pytest core/tests -q`

Expected: full core suite passes.

Run: `./.venv/bin/python core/tests/package/verify_artifacts.py`

Expected: artifact verification passes.

- [ ] **Step 3: Inspect the initial registry against consumer sources**

Run a read-only comparison script that loads core's registry, pipeline `origin/main` default and Bedrock config files, and internal `origin/main` candidate constants. Assert exact ordered equality for both pipeline execution profiles and all five internal offer groups. Save no generated files.

Expected: every comparison prints `match` and exits zero.

- [ ] **Step 4: Check worktree cleanliness and review commits**

Run: `git status --short --branch`

Expected: clean `codex/models-registry` branch.

Run: `git log --oneline origin/main..HEAD`

Expected: the design plus small parser, data, public API, and release commits.
