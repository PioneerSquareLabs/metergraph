# metergraph-server

`metergraph-server` is the FastAPI and PostgreSQL backend for the
[open-source Metergraph stack](https://github.com/metergraph/metergraph). It
ingests LLM usage metadata, prices calls with `metergraph-core`, and powers the
self-hosted cost dashboard.

The server is content-blind by construction. Prompts, completions, tool
arguments, and tool results are discarded at ingest and cannot reach the
database. See the repository's [privacy model](https://github.com/metergraph/metergraph/blob/main/docs/privacy.md)
for the exact fields it stores.

For the supported deployment, run the complete stack with Docker Compose as
described in the [self-hosting guide](https://github.com/metergraph/metergraph/blob/main/docs/self-hosting.md).
