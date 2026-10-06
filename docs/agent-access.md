# Coding agent access

Metergraph OSS provides the versioned `metergraph.agent-access/v1` contract
through REST endpoints and the stateless `POST /v1/agent/mcp` JSON-RPC
transport. The contract is shared with the hosted Metergraph product.

All successful responses are tenant-scoped metadata. The OSS server stores no
prompts, completions, tool arguments, tool definitions, or replay inputs, so
content and replay cannot be retrieved through Agent Access.

## Tools

| Tool | Privacy class | OSS availability |
|---|---|---|
| `metergraph_list_classified_workloads` | metadata | unavailable: OSS has no analysis checkpoints |
| `metergraph_get_workload_readiness` | metadata | unavailable: OSS has no classified trace cohorts |
| `metergraph_get_model_readiness` | metadata | unavailable: OSS has no analysis provider configuration |
| `metergraph_get_workspace_context` | metadata | available |
| `metergraph_get_capabilities` | metadata | available |
| `metergraph_list_routes` | metadata | available |
| `metergraph_get_usage` | metadata | available |
| `metergraph_get_ingestion_health` | metadata | unavailable: OSS has no ingest-batch history |
| `metergraph_list_incidents` | metadata | unavailable: OSS has no detector incidents |
| `metergraph_query_traces` | metadata | available |
| `metergraph_list_reports` | metadata | unavailable: OSS has no imported pipeline reports |
| `metergraph_get_report` | metadata | unavailable: OSS has no imported pipeline reports |
| `metergraph_get_report_evidence` | content | unavailable: OSS has no report evidence |
| `metergraph_get_trace` | content | unavailable: OSS stores metadata only |
| `metergraph_replay_trace` | replay | unavailable: OSS has no captured content or replay provider |

Unavailable tools return an `unsupported_capability` error document. They do
not fail with an ambiguous server error or return an empty document.

## Bounds

- `days` is limited to 1 through 90.
- List and query limits are limited to 1 through 200. Report evidence keeps the
  shared 1 through 50 limit even though reports are unavailable in OSS.
  Workload readiness also keeps the shared 1 through 50 limit.
- Responses are limited to 5 MiB.
- Content is never included by default and cannot be enabled on the OSS server.

Trace metadata excludes calls with no `trace_id`. Trace pagination uses an
opaque keyset cursor over the latest timestamp and trace ID. Usage is grouped
by UTC day and route.

## Authentication and rotation

Use `MG_AGENT_TOKENS` for read-only agent tokens, as a comma-separated
environment variable. A token in `MG_TOKENS` is also accepted for Agent
Access, while an agent-only token is rejected by the ingest endpoints.

For rotation, add the new token to the comma-separated list and restart,
update clients, then remove the old token and restart again. The stdio bridge
reads `METERGRAPH_URL` and `METERGRAPH_AGENT_TOKEN` and never prints the token.
