# metergraph-core

Reusable model registry, price catalog, and deterministic billing engine for
MeterGraph.

`metergraph-core` owns two separate public datasets. `models.yaml` records
canonical model identity, customer-facing names, executable provider routes,
and the ordered candidate fields MeterGraph products offer. `prices.yaml`
records effective-dated rates and billing rules. Core parses and validates both
datasets, resolves provider aliases and routes, calculates deterministic costs,
and selects a qualified gateway-reported charge or catalog estimate as the
effective call cost.

It does not own HTTP routes, database access, migrations, authentication,
tenancy, ingest, dashboard code, or any hosted-only concern, and it never reads
server environment variables.

## Install

```bash
python -m pip install metergraph-core
```

## Usage

```python
from datetime import datetime, timezone

from metergraph_core import load_catalog

catalog = load_catalog(region="global")
result = catalog.snapshot.cost(
    provider="openai",
    model="gpt-5.4-mini",
    at=datetime(2026, 8, 17, tzinfo=timezone.utc),
    input_tokens=1000,
    output_tokens=200,
)
print(result.canonical_model, result.price_id, result.cost_usd, result.status)
```

`load_catalog()` loads the catalog bundled in the installed package. Pass an
explicit `path` for tests or a self-hosted catalog replacement. The returned
`LoadedCatalog` exposes the declared catalog `version`, the SHA-256
`content_hash` of the loaded bytes, the parsed `document`, and the immutable
`snapshot` used for pricing.

Planning and evaluation systems that know the deployment channel before making
a call can resolve the exact effective price without emulating a provider
response:

```python
price = catalog.snapshot.resolve_price(
    model="openai/gpt-5.6-luna",
    channel="vercel-ai-gateway",
    at=datetime(2026, 8, 17, tzinfo=timezone.utc),
)
if price is not None:
    print(
        price.canonical_model,
        price.price.id,
        price.price.input_per_mtok,
        price.price.source_url,
    )
```

Resolution accepts canonical IDs and channel-scoped aliases, normalizes case
and surrounding whitespace, applies the configured region fallback and
effective-date windows, and returns `None` when no exact model/channel price
exists. It never substitutes a direct-provider price for a gateway price.

`LoadedCatalog.currency` is currently always `USD`, and
`LoadedCatalog.pricing_verified_at` records when the bundled catalog was last
checked against its linked provider sources.

## Model registry

```python
from metergraph_core import load_model_registry

models = load_model_registry()
for candidate in models.candidates("gateway"):
    print(candidate.id, candidate.display_name, candidate.pricing_channel)
```

`load_model_registry()` reads the packaged `models.yaml`. Execution profiles
describe every route the pipeline can run, while offer groups describe the
smaller ordered fields a product exposes for configured credentials. Candidate
IDs may intentionally repeat across execution profiles when gateway and
Bedrock use different physical routes; each route therefore also has a unique
internal `key`.

Use `validate_model_registry(models, load_catalog())` to verify that every
route's provider-facing model ID is declared on its exact pricing channel,
resolves to its canonical model, and has an active price on the registry
version date. The parser also rejects unknown route providers, provider/channel
mismatches on any route, mismatched offer-group credentials/providers, and duplicate physical routes. Pricing
remains exclusively in `prices.yaml`; being priceable does not automatically
make a model an approved analysis candidate.

## Billing evidence

Servers can pass content-blind, already-extracted gateway fields through the
shared trust boundary and combine them with a catalog result:

```python
from metergraph_core import normalize_gateway_evidence, resolve_billing

evidence = normalize_gateway_evidence({
    "gateway": "openrouter",
    "endpoint": "chat.completions",
    "reported_cost_usd": "0.00482",
    "reported_cost_source": "openrouter.usage.cost",
})
decision = resolve_billing(result, evidence)
print(decision.cost_usd, decision.cost_provenance)
```

The initial qualified contract is OpenRouter Chat Completions. A finite,
non-negative `openrouter.usage.cost` value, including zero, takes precedence
over a catalog estimate. The decision retains both values and never adds the
separately reported upstream inference cost to the OpenRouter account charge.
Unsupported or malformed evidence falls back to the catalog result.

The billing module validates only gateway, endpoint, fixed source names, and
decimal cost values. It does not inspect provider response content or own SDK
capture, HTTP, timestamps, trace context, persistence, or tenant behavior.

## Public API

```python
from metergraph_core import (
    Alias,
    BillingDecision,
    CatalogError,
    CatalogSnapshot,
    CostResult,
    GatewayBillingEvidence,
    LoadedCatalog,
    ModelRegistry,
    ModelRoute,
    Price,
    ResolvedPrice,
    load_catalog,
    load_model_registry,
    normalize_gateway_evidence,
    parse_catalog,
    resolve_billing,
    validate_model_registry,
)
```

## Catalog maintenance

The two manually maintained public datasets live under
`src/metergraph_core/data/`. Update `models.yaml` when model identity, display,
routing, or managed candidate policy changes. Update `prices.yaml` when rates
or billing rules change. Price corrections close or add effective windows;
they never rewrite historical prices in place. Either data change increments
its own declared version and produces a patch release of `metergraph-core`.
Software and data versions remain separate because they have different
lifecycles.

The model registry version is an ISO date. When more than one registry ships on
the same date, append a positive revision such as `2026-09-25.2` so consumers
can distinguish the policies while pricing validation still uses that date.
