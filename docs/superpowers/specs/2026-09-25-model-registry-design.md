# Canonical Model Registry Design

## Purpose

MeterGraph currently repeats managed model metadata in three places:

- `metergraph-core` pricing data identifies canonical models, aliases, and
  pricing channels.
- `metergraph-pipeline` embeds candidate identity, routing, display names, and
  pricing channels in its evaluation configuration. `customer_report.py`
  carries a second display-name table.
- `metergraph-internal` repeats the candidate sets, display names, credential
  routing, and managed-profile membership in `analysis_runs.py`.

Adding or retiring a managed model therefore requires coordinated edits and can
leave the application and runner with different model sets. The goal is one
versioned registry that both packages consume while preserving a strict
separation between model metadata and effective-dated prices.

## Ownership

`metergraph-core` will own a packaged `data/models.yaml` resource and the typed
Python API that reads it. Core is already the common dependency of the app and
pipeline, and already owns canonical model identity. The registry broadens that
ownership to stable presentation and execution metadata.

`models.yaml` will not contain token rates, effective dates, billing rules, or
price sources. Those remain exclusively in `prices.yaml`. A model being priced
does not automatically make it a managed-analysis candidate.

## Registry schema

The document has an independently versioned top-level `version` and an ordered
`models` list. Each model contains:

- `canonical_id`: stable MeterGraph identity.
- `display_name`: customer-facing model name.
- `publisher`: canonical publisher key.
- `routes`: one or more executable routes. Each route has a stable candidate
  `id`, execution `provider`, provider-facing `model_id`, `pricing_channel`,
  optional route-specific `display_name`, and ordered `execution_profiles` in
  which the runner can use it. Initial execution profiles are `default` and
  `bedrock`. The route-specific name distinguishes direct routes when the UI
  needs labels such as `GPT-5.6 Sol (direct)`.
- `execution_profiles`: top-level records that explicitly preserve each
  pipeline pool's ordered route keys.
- `offer_groups`: top-level ordered records containing an ID, optional required
  credential, and route IDs defining the product fields exposed for `gateway`,
  `fireworks`, `openai-direct`, `anthropic-direct`, and `bedrock`.

Execution availability and product offering are deliberately separate. The
pipeline's default execution profile contains a larger routable pool than the
hosted app offers in its default gateway field. Loaders preserve explicit
execution-profile and offer-group order so each existing consumer keeps its
exact current field.

The registry is intentionally explicit. It does not infer provider availability
from `prices.yaml`, because a historical price or alias is not evidence that a
provider currently serves a model.

## Public core API

Core will expose immutable typed values and loaders:

- `load_model_registry(path=None) -> ModelRegistry`
- `ModelRegistry.version`
- `ModelRegistry.model(canonical_id)`
- `ModelRegistry.routes_for_execution_profile(profile)`
- `ModelRegistry.candidates(offer_group)`
- `ModelRegistry.reachable_candidates(credentials)`

The default loader reads the packaged `models.yaml`. An optional path supports
validation and downstream tests without mutating package data. Unknown profiles,
duplicate identifiers or physical routes, malformed routes, and offer groups
whose credentials or providers do not match their declared routing path fail
closed with `ModelRegistryError`.

The public API returns route-specific candidate records. This keeps direct and
gateway routes distinct even when they resolve to the same canonical model.

## Cross-catalog validation

Core tests will validate both resources together:

- Every route's `canonical_id` exists in `prices.yaml`.
- Every `(model_id, pricing_channel)` route resolves through the pricing
  catalog, unless the route carries an explicit documented pricing exemption.
- Candidate IDs and `(provider, model_id)` routes are unique.
- Display names, publishers, credentials, profiles, and route fields are
  non-empty and drawn from declared values.

The initial registry must reproduce the current pipeline execution pools and
the app's separately ordered offer groups exactly. This PR changes ownership
only; it does not add, remove, or reorder candidates.

## Version and rollout contract

The model-registry version is distinct from the price-catalog version and the
Python package version. Any semantic registry change increments the registry
version and requires a core package release.

The subsequent internal-app change will include the registry version in every
analysis profile snapshot. The subsequent pipeline change will record the
version it loaded and reject a run that names an unsupported registry version.
This makes staggered deployments fail before evaluation rather than silently
running a different candidate set.

Rollout order:

1. Core adds `models.yaml`, loaders, validation, tests, documentation, and a
   package release.
2. Pipeline adopts the released core API, removes model entries from managed
   evaluation JSON, and removes the display-name table from
   `customer_report.py`.
3. Internal adopts the same core API, replaces the constants in
   `analysis_runs.py`, and begins snapshotting the registry version.

Until steps 2 and 3 ship, their existing lists remain authoritative for their
respective runtimes. The core PR is additive and does not change current
behavior.

## Verification

The core PR will include parser tests for valid and invalid documents, exact
profile and credential projections for the initial data, cross-catalog pricing
validation, package-artifact verification, and the full existing core suite.
The later consumer PRs will each include parity tests proving their generated
candidate fields match the previous constants before those constants are
removed.
