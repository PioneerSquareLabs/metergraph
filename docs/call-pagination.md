# Complete traversal of call metadata

GET `/v1/calls` retains its `items` array and adds `page` with `next_cursor`,
`has_more` and `limit`. Start with no cursor, then pass the server-issued opaque
`cursor` while `has_more` is true. Repeat the same `environment`,
`include_untagged`, `route` and `func` filters. The limit may change between pages.

Calls order by timestamp and database call identity, both descending. Repeated
trace IDs do not affect traversal. The database identity stays out of item fields.
Invalid cursors, changed filters and `cursor` with `before` return 400. Cursor
length is bounded and FastAPI returns 422 for overlong input. Bearer authentication
still applies: a cursor never authorizes access. Cursors are versioned and scoped
to this OSS contract. Do not parse them or transfer them to the hosted API.

Legacy `before` still excludes all rows at or after its timestamp. It cannot
traverse a page-ending timestamp tie completely. Servers can ship this additive
change before clients migrate. Existing function-detail and SDK testbench readers
consume a single bounded `items` page and tolerate the extra field. Complete
export clients must require cursor support, and explicitly decline complete
export when rolled back to an older server without `page`.

This changes call metadata reads only. Core pricing, content-blind ingestion,
SDK Python/TypeScript transport, relay and the pipeline's separate trace-export
API remain unchanged. No package release or deployed compatibility claim follows
from a source test.
