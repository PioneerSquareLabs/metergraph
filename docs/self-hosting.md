# Self-hosting

## One-liner

```bash
MG_TOKENS=$(openssl rand -hex 16) docker compose up -d
```

Brings up Postgres 16 and the server (API + dashboard) on port 8787. Note the token you generated — the SDK and the dashboard both use it.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `postgresql://metergraph:metergraph@localhost:5432/metergraph` | Postgres DSN |
| `MG_TOKENS` | *(empty — all requests rejected)* | Comma-separated static bearer tokens |
| `MG_PRICES_PATH` | bundled `prices.yaml` | Mount an updated price catalog without rebuilding |
| `MG_REGION` | `global` | Preferred price region (falls back to `*`, then `global`) |
| `MG_MAX_BODY_BYTES` | `8388608` | Ingest body cap |
| `MG_MAX_ROWS` | `5000` | Rows per ingest request cap |
| `MG_DASHBOARD_DIST` | `dashboard/dist` / `/app/static` in Docker | Static dashboard directory |

## Pointing the SDK at your server

```bash
export METERGRAPH_INGEST_URL=http://your-host:8787
export METERGRAPH_APP_TOKEN=<one of MG_TOKENS>
```

## API

- `POST /v1/ingest` — SDK wire format (`{"schema_version":1,"rows":[...]}`), gzip-aware, returns 202. Content fields are stripped; `event_type: "outcome"` rows are accepted and ignored (hosted feature).
- `GET /v1/usage?group_by=func|module|route|model|provider|day|hour&from=&to=&environment=` — aggregates
- `GET /v1/usage/timeseries?group_by=func|route|model&bucket=hour|day&top=8` — chart series
- `GET /v1/calls?func=&route=&limit=&before=` — recent metadata rows
- `GET /v1/catalog` — loaded price catalog
- `GET /v1/config` — empty canary config (hosted feature), stable ETag
- `GET /healthz`

All `/v1/*` endpoints except `/v1/config` and `/healthz` require `Authorization: Bearer <token>`.

## Security notes

- v0 auth is static bearer tokens; run the server inside your network boundary and use distinct tokens per producer so they can be rotated independently.
- The dashboard stores its token in browser localStorage; treat dashboard access as trusted-network access.
- Durability is a synchronous Postgres commit before the 202; the SDK is fire-and-forget, so a down server drops rows rather than blocking your app. At self-host scale (SDK batches ≤512 KiB) a single instance comfortably handles typical traffic.
