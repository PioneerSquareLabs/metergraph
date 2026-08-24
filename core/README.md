# metergraph-core

Reusable catalog and deterministic token-cost pricing engine for MeterGraph.

`metergraph-core` owns the public effective-dated model catalog and the pricing
logic shared across MeterGraph systems: catalog parsing and validation, provider
and model alias resolution, channel and region selection, input/output/cache/
batch/long-context pricing rules, deterministic cost calculation with reason
codes, stable logical price identifiers, and catalog version and content-hash
reporting.

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

## Public API

```python
from metergraph_core import (
    Alias,
    CatalogError,
    CatalogSnapshot,
    CostResult,
    LoadedCatalog,
    Price,
    ResolvedPrice,
    load_catalog,
    parse_catalog,
)
```

## Catalog maintenance

The only manually maintained public catalog lives at
`src/metergraph_core/data/prices.yaml`. Every record requires its provider
source URL and effective date. Corrections close or add effective windows; they
never rewrite historical prices in place. A catalog change updates the declared
catalog version and produces a patch release of `metergraph-core`. Software
version and catalog version are separate values because code and price data have
different lifecycles.
