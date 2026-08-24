# Metergraph

**LLM cost tracking by function.** Wrap your OpenAI, Anthropic, or Gemini client and see exactly which functions in your codebase spend what — tokens in/out, cached tokens, reasoning tokens, latency, and server-priced dollar cost — in a dashboard you can self-host with one `docker compose up`.

Metergraph is **content-blind by construction**: the SDK captures usage metadata only, and this server strips any content fields at ingest before anything touches the database. Your prompts and completions never leave your process.

```python
import metergraph
from openai import OpenAI

metergraph.init()
client = metergraph.wrap(OpenAI())

@metergraph.track
def summarize_invoice(invoice):
    return client.chat.completions.create(model="gpt-5.6-luna", messages=[...])
```

Every call is attributed to `yourapp.billing:summarize_invoice` and shows up in the dashboard priced from an effective-dated, community-maintained [price catalog](core/src/metergraph_core/data/prices.yaml).

## Quickstart (self-hosted)

```bash
git clone https://github.com/PioneerSquareLabs/metergraph && cd metergraph
MG_TOKENS=dev-token docker compose up
```

Then point the SDK at it:

```bash
export METERGRAPH_INGEST_URL=http://localhost:8787
export METERGRAPH_APP_TOKEN=dev-token
```

Dashboard: http://localhost:8787 (enter the same token). No provider API key is
needed to try it — the demo wraps local OpenAI-, Anthropic-, and Google-shaped
clients with the published SDK:

```bash
python -m pip install 'metergraph>=0.4,<1'
MG_TOKEN=dev-token python scripts/seed_demo.py
```

The demo performs the same SDK 0.4+ session exchange and batched ingestion as
an instrumented application; it does not construct ingest payloads directly.

## Packages

| Package | Where | What |
|---|---|---|
| `metergraph` (PyPI + npm) | [metergraphsdk](https://github.com/PioneerSquareLabs/metergraphsdk) | Zero-dependency capture SDKs for Python and TypeScript — OpenAI, Anthropic, and Gemini clients |
| `metergraph-core` (PyPI) | [`core`](core) | Reusable price catalog + deterministic pricing engine; the only copy of `prices.yaml`. Reused by other MeterGraph systems |
| `metergraph-server` | [`server`](server) | FastAPI + Postgres ingest and usage API; prices traffic through `metergraph-core` |
| dashboard | [`dashboard`](dashboard) | React SPA served by the server |

## How attribution works

- **Python**: automatic — the SDK walks the stack at call time and attributes each LLM call to the nearest function under your app root. Add `@metergraph.track` (or `@metergraph.track("billing.summarize")`) for explicit, stable names.
- **TypeScript**: use `track(fn)` / `track("billing.summarize", fn)` — reliable across bundlers and minifiers, where stack parsing is not. Best-effort stack attribution is the fallback.
- `metergraph.route("ticket-classifier")` groups calls by product surface, orthogonal to functions.

## What a row contains — and what it never contains

Captured: timestamp, function/module, route, provider, model, input/output/cache-read/cache-write/reasoning token counts, latency, TTFT, status, stream/batch flags, session id, a content-free structural template hash, tool-call **names**, tags, environment, SDK version.

Never stored by this server: prompts, completions, tool-call arguments or results. Rows are projected through a column allowlist at ingest; content fields are structurally incapable of reaching the database. See [docs/privacy.md](docs/privacy.md).

## Hosted service and default URL

Without `METERGRAPH_INGEST_URL`, the SDK points at Metergraph's hosted service, and **without a `METERGRAPH_APP_TOKEN` capture is entirely disabled** — nothing is ever sent silently. The hosted tier adds the evaluation layer: model-swap recommendations, replay campaigns, judge-qualified evals, canary rollouts. Content capture (`capture_text`) is an explicit opt-in that only has effect against the hosted service; this server discards content regardless of SDK configuration.

## Development

The SDKs live in their own repo: [metergraphsdk](https://github.com/PioneerSquareLabs/metergraphsdk). Everything else — the server, dashboard, public catalog, and the reusable pricing core — lives in this one repository; you still clone a single server repo. The `metergraph-core` package is carved out so other MeterGraph systems can reuse the exact catalog and pricing behavior.

Install the local core before the server so the server resolves its `metergraph-core` dependency from source, not a published release:

```bash
# Core + server (needs Postgres). Core must install first.
python -m pip install -e './core[dev]' -e './server[dev]'
MG_TEST_DATABASE_URL=postgresql://localhost:5432/metergraph_test pytest core/tests server/tests

# Dashboard
cd dashboard && npm install && npm run dev
```

## Updating model prices

Prices live in [`core/src/metergraph_core/data/prices.yaml`](core/src/metergraph_core/data/prices.yaml) — the single public catalog, effective-dated so history reprices correctly. To update: close the old window with `effective_to`, add a new entry with `effective_from` and a `source_url`, and open a PR. A catalog change updates the declared catalog version and produces a patch release of `metergraph-core`. Self-hosters can still mount a newer file with `MG_PRICES_PATH` without rebuilding. See [docs/prices.md](docs/prices.md).

## License

[Apache-2.0](LICENSE)
