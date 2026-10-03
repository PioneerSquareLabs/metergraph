# Usage read budgets and completeness

The internal and OSS usage endpoints share these budgets. Trace list/count retain
MET-510's existing five-second timeout; route coverage and trace query plans
remain owned by MET-532/MET-533. Performance summary keeps its existing admission,
checkout, statement-timeout and cache controls. This change does not establish
a response or memory budget for that separate summary.

| Read | Window | Returned data |
| --- | --- | --- |
| Grouped usage | At most 91 days | Top 500 groups by cost, calls, key, provider; one extra row probes completeness |
| Internal days preset | Clamped 1 to 90 days | First 500 route/day groups in existing day/route order; exact start/end bounds |
| Usage timeseries | At most 91 days | Requested top 1 to 25 keys plus an exact other series, at most 2,185 hourly buckets |
| Environments | At most 91 days | At most 500 complete choices; excess fails rather than initializing a partial filter |

Ninety days is the existing internal preset ceiling. The 91-day explicit-window
budget gives the browser's fixed 90-day anchor one day of polling headroom.
Refresh the window before that headroom expires. OSS custom ranges over 91 days
must be split before upgrading. Naive timestamps retain UTC interpretation;
timeseries boundaries are normalized to UTC before generating bucket labels.

Every covered connection uses a two-second pool checkout and five-second
statement timeout, including internal legacy pending-batch reads. These are
per-checkout and per-statement bounds, not a five-second total request SLA.
Cancellation and pool exhaustion return 503, code usage_query_busy, and
Retry-After: 5. Narrow time or filters before retrying.

JSON responses are capped at 1 MiB of compact UTF-8. Larger output returns 503
usage_response_too_large with recovery guidance. This is an output budget,
not a process-memory guarantee: selected values are read before serialization.
Five hundred groups were already the grouped contract. The synthetic baseline
with 2,525 calls, 505 routes and five days returned 500 groups in 128,636 bytes
without indicating omission. Its 25-key timeseries was 2,205 bytes. The byte
ceiling gives about eight times the measured grouped response headroom, while
the existing 90-day preset and 25-key maximum bound timeseries shape. These are
engineering resource limits, not invented latency targets or production-scale
capacity evidence.

Successful envelopes retain items, usage, buckets and series as appropriate,
and add completeness:

```json
{"mode":"top_n","complete":false,"limit":500,"returned":500,
 "from":"2026-08-01T00:00:00+00:00","to":"2026-08-08T00:00:00+00:00"}
```

Exactly 500 groups can be complete. The 501st probe makes complete false.
No unknown omitted count is fabricated. A top-N result is not an exhaustive
aggregate export, and totals summed from incomplete items are partial. Current
internal and OSS dashboards display that qualification. Legacy route/day reads
use mode route_days because their retained order is not cost-ranked.

Timeseries mode top_n_with_other has complete true: all matched costs contribute
to named series or the exact other series. SQL compresses non-top groups before
returning rows; it still aggregates the matching window under the timeout.
other_series_index identifies the appended synthetic series even when a real
key is itself named other. A compressed cost total does not give exhaustive
per-key attribution.

To recover exhaustive attribution, narrow by exact route/model/environment or
time until each response is complete, or export all metadata through the
existing paged /v1/calls contract and aggregate it. Do not sum percentile or
error-rate fields across partitions as though they were additive. Internal call
pagination has no time filter, so callers must filter the exported timestamps.
There is no continuation token for a changing top-N aggregate.

Roll out matching API/dashboard bundles and refresh cached browsers before
claiming readers recognize completeness. Old clients ignore additive metadata
and can still display partial totals without a warning. New dashboards tolerate
old servers without metadata but cannot infer completeness from its absence.
Rollback to older servers restores the unbounded legacy/timeseries paths and
silent cutoff. No database migration is required.

MET-534's exact pipeline, relay, Python/TypeScript SDK, agent and deployment
inventory controls the blast radius. Capture and pipeline export protocols are
unchanged. MET-543 supplies typed telemetry schemas; MET-545 owns packaged
old/new compatibility gates. Local HTTP, metrics and performance reports do not
prove installed fleet rollout or production performance.
