"""JSON Schema read contracts. Documentation only; no response filtering."""


def scalar(kind, *, nullable=False, description=None, **constraints):
    schema = {"type": [kind, "null"] if nullable else kind, **constraints}
    if description:
        schema["description"] = description
    return schema


def array(items, *, nullable=False, **constraints):
    return {"type": ["array", "null"] if nullable else "array", "items": items, **constraints}


def obj(properties, *, optional=(), **constraints):
    return {"type": "object", "properties": properties,
            "required": [name for name in properties if name not in optional],
            "additionalProperties": True, **constraints}


def fields(names, schema):
    return {name: schema for name in names.split()}


TEXT = scalar("string")
NULL_TEXT = scalar("string", nullable=True)
COUNT = scalar("integer", description="Count of calls, tokens or records as named.")
NULL_COUNT = scalar("integer", nullable=True)
USD = scalar("number", description="US dollars; includes only costs available for the matching records.")
NULL_USD = scalar("number", nullable=True, description="US dollars; null means unavailable, not zero.")
MS = scalar("integer", nullable=True, description="Milliseconds; null means no measurement.")
FRACTION = scalar("number", description="Dimensionless fraction, not a percentage.")
BOOL = scalar("boolean")
NULL_BOOL = scalar("boolean", nullable=True)
TIME = scalar("string", format="date-time", description="ISO 8601 timestamp with timezone.")
NULL_TIME = scalar("string", nullable=True, format="date-time")
JSON_VALUE = {"description": "Intentional arbitrary JSON metadata/value, including nested arrays, objects and null."}
COMPLETENESS = obj({"mode": scalar("string", enum=["top_n", "route_days", "distinct", "top_n_with_other"]),
    "complete": BOOL, "limit": COUNT, "returned": COUNT, "from": TIME, "to": TIME,
    "other_series_index": scalar("integer", nullable=True, description="Index of the synthetic Other series; null if absent. Distinguishes a real key named other.")}, optional=("other_series_index",))
GROUP_ITEM = obj({"key": TEXT, "provider": TEXT,
    **fields("calls input_tokens output_tokens cache_read_tokens reasoning_tokens reported_calls unpriced_calls", COUNT),
    "cost_usd": USD, "avg_latency_ms": MS, "p95_latency_ms": MS, "error_rate": FRACTION}, optional=("provider",))
GROUPED_USAGE = obj({"items": array(GROUP_ITEM, maxItems=500), "completeness": COMPLETENESS})
USAGE_SERIES = obj({"buckets": array(TEXT), "series": array(obj({"key": TEXT, "values": array(USD)}), maxItems=26), "completeness": COMPLETENESS})
ENVIRONMENTS = obj({"items": array(obj({"value": NULL_TEXT, "calls": COUNT}), maxItems=500), "completeness": COMPLETENESS})
CALL_COMMON = {"ts": TIME,
    **fields("func module route provider model canonical_model catalog_price_id cost_status status error_type session_id environment sdk sdk_version request_id trace_id", NULL_TEXT),
    **fields("input_tokens output_tokens cache_read_tokens cache_write_tokens reasoning_tokens", NULL_COUNT),
    **fields("cost_usd reported_cost_usd catalog_cost_usd", NULL_USD),
    "catalog_reasons": array(TEXT), "latency_ms": MS, "stream": NULL_BOOL}
PAGE = obj({"next_cursor": scalar("string", nullable=True, description="Opaque filter/profile-bound continuation, not authorization. Null on the final page."),
    "has_more": BOOL, "limit": scalar("integer", minimum=1, maximum=500)})
ERROR = obj({"detail": {"anyOf": [TEXT, obj({"code": TEXT, "message": TEXT}, optional=("code", "message")),
    array(obj({"loc": array({"type": ["string", "integer"]}), "msg": TEXT, "type": TEXT,
        "ctx": JSON_VALUE, "input": JSON_VALUE}, optional=("ctx", "input")))]}})

WINDOW = "from/to accept ISO timestamps (naive timestamps are UTC); bounds are inclusive from, exclusive to. Default is the last seven days, or one day for hourly timeseries. Repeated environment parameters select matching records."
USAGE_DESCRIPTION = WINDOW + " group_by is func/module/route/model/provider/day/hour. Results retain at most 500 groups ranked by cost then calls then key (model also provider); day/hour items are displayed by key. Model groups add provider. completeness.complete=false means omitted groups and partial displayed totals; use narrower filters or paged calls. Maximum window 91 days."
SERIES_DESCRIPTION = WINDOW + " group_by is func/route/model; bucket is day/hour; top is 1..25. UTC bucket labels align with each values array. Top keys are selected by whole-window cost; all remaining cost is folded into a final Other series identified by completeness.other_series_index. Complete cost totals do not imply exhaustive named keys. Maximum window 91 days."
ENV_DESCRIPTION = WINDOW + " Returns environment value/calls objects, including value=null for untagged records; more than 500 choices fails explicitly rather than returning an incomplete selection. Maximum window 91 days."
CALL_DESCRIPTION = "Newest metadata calls first, ordered by (ts DESC, internal id DESC). limit is 1..500 (default 50). Repeat requests using page.next_cursor and unchanged func/route/environment filters; a cursor is profile/filter-bound, not authorization. Legacy before is an exclusive timestamp boundary and cannot be combined with cursor; timestamps alone cannot traverse ties. No from/to query is supported. Customer metadata remains arbitrary JSON; no content fields are added."


def contract(schema, description, *, busy=False, not_found=False):
    """Decorate successful/error JSON documentation without runtime models."""
    responses = {200: {"description": "Successful read", "content": {"application/json": {"schema": schema}}}}
    descriptions = {400: "Invalid query or continuation", 401: "Missing or invalid credentials",
                    422: "Query/path validation failed"}
    if not_found:
        descriptions[404] = "No matching tenant-visible resource"
    if busy:
        descriptions[503] = "Query busy or response budget exceeded; retry or narrow the request as described by detail"
    for status, message in descriptions.items():
        responses[status] = {"description": message, "content": {"application/json": {"schema": ERROR}}}
        if status == 503:
            responses[status]["headers"] = {"Retry-After": {"description": "Suggested delay in seconds when supplied.", "schema": {"type": "string"}}}
    return {"responses": responses, "description": description}

CALL = obj({**CALL_COMMON, **fields("status_code", NULL_COUNT), **fields("finish_reason finish_reason_raw template_hash", NULL_TEXT), "error": NULL_BOOL, "tool_names": array(TEXT, nullable=True)})
CALLS = obj({"items": array(CALL, maxItems=500), "page": PAGE})
