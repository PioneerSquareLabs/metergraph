# Privacy model

Metergraph's open-source path is **content-blind by construction**. This page states exactly what that means.

## What the SDK captures by default

For every wrapped LLM call, one metadata row:

- `ts`, `latency_ms`, `ttft_ms` (streaming)
- `func`, `module` — which of *your* functions made the call (name only)
- `route`, `session_id`, `tags`, `environment`, `unit_name`/`unit_count`
- `provider`, `model`, `endpoint`, `request_id`
- `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `reasoning_tokens`
- `status`, `stop_reason`, `error`/`error_type`, `stream`/`batch` flags
- `template_hash` — a structural fingerprint of the request *shape* (roles, ordering, placeholders). Literal text is scrubbed before hashing and is not recoverable from it.
- `tool_calls` — tool **names** and idempotency policy only, unless content capture is explicitly enabled
- `sdk`, `sdk_version`, `runtime`

No prompt text. No completion text. No tool arguments or results.

## What the OSS server refuses to store

Even if an SDK is configured with `capture_text=True`, the self-hosted server projects every row through a strict column allowlist at ingest (`server/src/metergraph_server/ingest.py`). `request_json`, `response_text`, tool-call arguments/results, and any unknown field are dropped before the database is touched — there is no column for them to land in. This is covered by a sentinel test (`server/tests/test_api.py::test_content_never_reaches_database`).

## When content is ever captured

Only when **both** are true:

1. You explicitly opt in (`capture_text=True` on a route, or `METERGRAPH_CAPTURE_TEXT=1`), and
2. The SDK is pointed at Metergraph's hosted service, whose evaluation features (replay campaigns, output-equivalence checks, eval-set generation) need it.

Against a self-hosted server the opt-in is inert.

## Fail-safe defaults

- No `METERGRAPH_APP_TOKEN` → capture is disabled entirely; the SDK never sends anything silently.
- `METERGRAPH_DISABLED=1` → hard off.
- The SDK is fail-open: transport errors never break or slow your LLM calls; rows are dropped, not retried into your request path.
