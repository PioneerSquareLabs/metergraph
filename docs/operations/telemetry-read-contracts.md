# Telemetry read contracts

The generated `/openapi.json` publishes JSON Schema contracts for GET usage,
usage timeseries, environments and calls in both producers. The internal
producer also covers GET traces, trace count, summary, timeseries and detail.
Schemas describe existing serialized bodies. They do not filter, coerce or
discard customer metadata at runtime. Extra response fields are permitted for
additive releases. Embedded score metadata and internal tags/tool JSON remain
open values. Other performance, workload-routing, catalog, session/experiment
score and agent operations are separate surfaces.

## Values and nullability

Required means the member is present. Nullable means its value can be JSON
null. Call measurements, identities and capture metadata can be null; do not
treat unavailable price or latency as zero. Costs are US dollars, token fields
are counts, latency/duration fields are milliseconds and error rates/shares
are fractions. Internal legacy economics margin fields are percentage points
on a 0..100 scale; units are customer-defined. Trace scores carry numeric,
boolean or text values selected by data_type, with arbitrary object metadata.
Score values are not all normalized to a common scale.

The shared envelopes are compatible, but extensions differ. Internal grouped
usage includes cache_write_tokens, and its days preset returns a route/day
usage envelope. OSS does not implement the days preset and returns environment
items as value/calls objects; internal environment items are strings. Internal
calls include tags, runtime and cache-write-window fields; OSS calls include
finish reasons, status_code and template_hash. Do not generate a single closed
model by assuming one producer's optional extensions exist on the other.

## Windows, filters and completeness

from/to are ISO timestamps with an inclusive lower and exclusive upper bound.
Naive inputs are interpreted as UTC. Omitted bounds use the current clock and
the default window: seven days, or one day for hourly timeseries. Repeated
environment values select matching records. Internal empty selections mean
all; OSS explicitly distinguishes include_untagged and selections, as each
operation description explains. Route matching on trace lists is trace
membership, so other spans on a matching trace still contribute to its totals.
status=error is the only nonempty supported trace status filter. q matches an
ID prefix or case-insensitive name substring. content is any/analyzable/
not_analyzable. These are documentation declarations of existing validation.

Usage's budgets and recovery are in [usage-read-contract.md](usage-read-contract.md).
A retained top-N list can have complete=false; its displayed sums are partial.
Top-N plus Other timeseries preserves cost totals but not exhaustive named
groups. The synthetic Other position disambiguates a real key called other.
Parallel series arrays align with bucket labels. Usage normalizes UTC buckets.
Existing trace timeseries uses the supplied start offset to construct labels;
supply UTC from/to for UTC-aligned trace buckets. This schema publication does
not change that existing behavior.

## Pagination

Calls use (timestamp descending, internal row ID descending), with opaque
page.next_cursor, page.has_more and page.limit. Keep func/route/environment
filters fixed. The cursor binds profile and filters; it grants no permission.
The legacy before timestamp cannot traverse ties and cannot combine with a
cursor. Calls have no from/to query filter; use timestamps to filter an
exhaustive export locally when needed.

Trace lists sort by a supported aggregate and trace ID in the chosen
direction. Started-at continuation needs both before and before_trace_id from
the last item, unchanged filters/direction and offset zero. before means
started_at, never last_span_at. A first auto_window page can widen its range;
reuse effective_from/effective_to for later pages. total is null when
include_total=false. has_more is the continuation signal. The pipeline
reader's corrected continuation is specified in MET-540.

## Errors and release boundaries

Error detail can be a string, an object with code/message and additional
fields, or FastAPI validation entries with loc/msg/type and optional ctx/input.
Operations declare query 400, authentication 401, internal scope 403,
validation 422, detail 404 and scoped query/budget 503 where applicable.
Retry-After is documented for 503 when provided. Existing auth and workspace
resolution still apply; publishing a schema grants no access.

This change needs no database migration or consumer payload migration. The
API specification and existing response values must be deployed together;
older producers lack these schemas. Rollback to the parent restores the
previous specification with the same read values. Current supported versions
and artifact selectors are tracked by MET-534. Pipeline v0.2.67/v0.2.68 reads
trace values, the SDK testbench reads calls, dashboards read these envelopes,
and relay/SDK ingestion and agent protocols are unchanged. MET-545 owns
packaged producer/consumer and rolling-release gates. Local response/schema
validation is distinct from deployed or installed fleet acceptance.
