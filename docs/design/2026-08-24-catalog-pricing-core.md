# Catalog and Pricing Core

**Status:** Proposed

## Purpose

MeterGraph currently maintains model prices and pricing behavior in multiple
repositories. The OSS server has the most complete public, effective-dated
catalog, while hosted MeterGraph and the reporting pipeline maintain separate
representations. That duplication allows model coverage, aliases, price history,
and cost calculations to drift.

The first alignment milestone will publish the OSS catalog and pricing engine as
the `metergraph-core` Python package from the existing `metergraph` repository.
The repository remains the single public home for the server, dashboard, public
catalog, and reusable pricing core. No additional public repository is created.

## Scope

The first `metergraph-core` release owns only:

- the public effective-dated catalog;
- catalog parsing and validation;
- provider and model alias resolution;
- channel and region selection;
- input, output, cache, batch, and long-context pricing rules;
- deterministic cost calculation and reason codes;
- stable logical price identifiers; and
- catalog version and content hash reporting.

It does not own HTTP routes, database access, migrations, authentication,
tenancy, ingest, dashboard code, evaluations, recommendations, or hosted-only
operations.

The initial release loads exactly one catalog. It does not implement public-base
plus overlay composition, tenant pricing, negotiated prices, or hosted-only
records. Those capabilities require a separate design when an observed product
requirement exists.

## Repository layout

```text
metergraph/
├── core/
│   ├── pyproject.toml
│   ├── src/metergraph_core/
│   │   ├── __init__.py
│   │   ├── catalog.py
│   │   ├── loader.py
│   │   └── data/prices.yaml
│   └── tests/
├── server/
│   ├── pyproject.toml
│   ├── src/metergraph_server/
│   └── tests/
├── dashboard/
└── docs/
```

The PyPI distribution is named `metergraph-core`; its import package is
`metergraph_core`. The OSS server remains a separate distribution and depends on
`metergraph-core`.

## Public API

The initial package exposes a deliberately small API:

```python
from metergraph_core import (
    CatalogError,
    CatalogSnapshot,
    CostResult,
    LoadedCatalog,
    load_catalog,
    parse_catalog,
)
```

`load_catalog()` loads the catalog bundled in the installed package. Callers may
pass an explicit path for tests or self-hosted catalog replacement. Environment
variables remain server configuration and are not read by core.

```python
catalog = load_catalog(region="global")
result = catalog.snapshot.cost(
    provider="openai",
    model="gpt-5.4-mini",
    at=event_timestamp,
    input_tokens=1000,
    output_tokens=200,
)
```

`LoadedCatalog` contains:

- the declared catalog version;
- the SHA-256 hash of the loaded catalog bytes;
- the parsed document for diagnostics and existing API consumers; and
- the immutable `CatalogSnapshot` used for pricing.

The pricing inputs, `CostResult`, reason codes, effective-window behavior, region
fallback, rounding, and logical price identifiers preserve current OSS behavior.
The extraction is not an opportunity to redesign pricing semantics.

## Catalog ownership and updates

`core/src/metergraph_core/data/prices.yaml` becomes the only manually maintained
public catalog. The old server-local copy is removed rather than synchronized.
Every catalog record continues to require its provider source URL and effective
date. Price corrections close or add effective windows; they do not rewrite
historical prices retroactively.

A catalog change updates the declared catalog version and produces a patch
release of `metergraph-core`. Software version and catalog version remain
separate values because code and price data have different lifecycles.

Downstream consumers pin a released core version. They do not fetch `main` or a
raw GitHub URL at runtime.

## OSS server integration

The server replaces its private catalog implementation with imports from
`metergraph_core`. Its existing `MG_PRICES_PATH` and `MG_REGION` configuration
remain supported by resolving those values in the server and passing them to
`load_catalog()`.

The health response continues to expose `catalog_version` and adds
`catalog_hash`. Ingest and usage behavior remain unchanged.

For the first core release, existing imports from `metergraph_server.catalog`
and `metergraph_server.prices` remain as thin compatibility re-exports. They do
not retain a second implementation or catalog copy.

## Packaging and local development

The repository builds two Python distributions. CI and local development install
the local core before the server:

```bash
python -m pip install -e ./core -e './server[dev]'
```

The server's published metadata declares its compatible `metergraph-core`
version range. Docker and release builds build or install the core artifact before
the server. A clean checkout must not depend on an already published unreleased
core version.

The core wheel and source distribution include exactly one bundled
`prices.yaml`. Package tests install the built wheel in an isolated environment,
load the bundled catalog, and price a representative call.

## Downstream migration

Downstream adoption happens after the OSS extraction is released:

1. `metergraph-pipeline` replaces its independently maintained
   `model_prices.json` and pricing code with the pinned package.
2. `metergraph-internal` pins the package and replaces its duplicate pricing
   calculation with core behavior.
3. Hosted database persistence remains a separate adapter concern. The first
   core release does not redesign hosted migrations, foreign keys, or catalog
   administration.

Each downstream migration is an independent PR and can roll back by restoring
its previous dependency pin and adapter.

## Validation and release gates

The OSS extraction must satisfy all of the following:

- existing server catalog tests pass without changing expected results;
- a golden set of calls returns identical canonical models, logical price IDs,
  statuses, reasons, and costs before and after extraction;
- invalid catalogs retain current validation failures;
- the core wheel and source distribution contain the catalog and exclude tests;
- the installed wheel loads and prices successfully without the source tree;
- the server wheel declares the core dependency and works with the built core
  wheel; and
- the server, dashboard, and latest-SDK end-to-end jobs remain green.

Later downstream PRs add the same golden fixture to pipeline and internal so all
three consumers must produce equivalent pricing results.

## Error handling

Malformed catalog data fails during load with `CatalogError`; the service must
not start with a partially parsed catalog. Unknown models and missing effective
prices remain ordinary unpriced `CostResult` values rather than exceptions.
Invalid usage values continue to produce the established partial-pricing reason
codes.

## Delivery sequence

1. Extract and package core in the OSS repository with no behavioral change.
2. Publish the first `metergraph-core` release.
3. Migrate pipeline to the released package.
4. Migrate hosted pricing calculation to the released package.
5. Revisit the next shared boundary only after these consumers demonstrate
   equivalent behavior.

Each item is delivered as a separate reviewed PR.
