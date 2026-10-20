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

On Bedrock only (provider `bedrock` for `cost`, channel `aws-bedrock` or
`aws-bedrock-geo` for `resolve_price`), an id with no exact alias falls back to
the id without its model-version suffix (`anthropic.claude-sonnet-5-v1:0` to
`anthropic.claude-sonnet-5`) and then without a `global.` inference-profile
prefix, the latter only onto an `aws-bedrock` alias. Geographic prefixes such as
`us.` or `eu.` are never stripped, because they bill on `aws-bedrock-geo`.

`LoadedCatalog.currency` is currently always `USD`, and
`LoadedCatalog.pricing_verified_at` records when the bundled catalog was last
checked against its linked provider sources.

### Direct lookup and usage cost

`lookup_price` returns the price a call is billed at, and `calculate_cost`
costs a call's usage at that price:

```python
from metergraph_core import ModelUsage

found = catalog.lookup_price(
    model="gpt-5.4-mini",
    provider="openai",
    at=datetime(2026, 8, 17, tzinfo=timezone.utc),
)
print(found.status, found.model_id, found.price.id, found.price.currency)

result = catalog.calculate_cost(
    model="gpt-5.4-mini",
    provider="openai",
    at=datetime(2026, 8, 17, tzinfo=timezone.utc),
    usage=ModelUsage(input_tokens=1000, output_tokens=200, cache_read_tokens=500),
)
print(result.status, result.model_id, result.cost, result.currency)
print(result.components.input, result.components.cache_read)
```

Both resolve exactly as `resolve_price` and `price` do, including
effective-date windows and the Bedrock fallback, and cost through the same
arithmetic. `model_id` is the resolved catalog ID, not the alias passed in, and
`price` is the applied catalog row. An explicit `channel` is authoritative;
without one, `provider` supplies its direct channel, and with neither the
result is `unpriced` with `missing_dimensions=("channel",)`.

Every `Price` states its `currency`, and `CostResult.currency` is the currency
of the applied price. The bundled catalog is USD. `cost` and
`components.input`, `output`, `cache_read`, `cache_write` and `request` are in
that currency; the older `cost_usd` and `*_usd` names return the same values.

A `ModelUsage` count left `None` is unknown, not zero. A missing input or
output count keeps the subtotal that is known, marks the result `partial`, and
lists `input_tokens` or `output_tokens` in `missing_dimensions`.

`region` selects a regional price and overrides the `region` passed to
`load_catalog`. When neither names a region:

- if every region bills the model the same, the result is `priced`;
- if a global price applies but some region bills differently, the global
  price is returned as `partial` with `missing_dimensions=("region",)`;
- if only specific regions price the model, the result is `unpriced` with
  `missing_dimensions=("region",)`.

A region the model is not sold in is `unpriced` with nothing missing.

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

A model entry may record `released`, the publisher's general-availability
date as an ISO date. `models.release_date(canonical_id, load_catalog())`
returns that date with basis `released`, or, when none is recorded, the
earliest `effective_from` of the model's catalog prices with basis
`first_price`, so a consumer can tell a launch date from a pricing date
without computing the fallback itself.

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
    ModelUsage,
    Price,
    PriceResult,
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
