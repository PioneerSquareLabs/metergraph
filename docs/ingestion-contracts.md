# OSS ingestion and SDK session contract

OpenAPI publishes the native/session decoded JSON shapes without adding runtime request models or response filtering. Native event fields remain open, including arbitrary metadata and future SDK fields. Existing OSS normalization drops captured content and ignores outcome rows.

POST /v1/ingest/sessions uses an app bearer and protocol_version=2, repository and sdk_version. Success is 201 with session_token and ISO expiry, without repository_id. Invalid protocol/fields use 400. POST /v1/ingest accepts app or exchanged session bearer, schema_version=1 (or omitted), and a nonempty rows array. Success is 202 with accepted and ignored counts. This profile does not promise native idempotency, a raw batch identifier or durable worker outbox behavior.

Both routes accept uncompressed JSON or Content-Encoding: gzip and apply encoded/decompressed size limits. The application/json schema is the decoded representation. Future fields are not discarded by a documentation schema. Supported producer fixtures use Python/TypeScript SDK 0.6.5 and 0.6.10 and relay 0.7.3; packaged compatibility evidence must identify exact versions and digests. Matching hosted path names do not mean identical status, fields or capability gates.

These declarations preserve the existing wire contract and require no producer migration. Rollback can restore the preceding server without changing producer payloads. An installed client/server test is separate evidence from an OpenAPI declaration or source test.
