"""OSS native/session schemas, documenting intentional profile differences."""

from .telemetry_contracts import COUNT, ERROR, TEXT, array, obj, scalar

SESSION = obj(
    {
        "protocol_version": scalar("integer", const=2),
        "repository": scalar("string", minLength=1, maxLength=512),
        "sdk_version": scalar("string", minLength=1, maxLength=64),
    }
)
SESSION_REPLY = obj(
    {"session_token": TEXT, "expires_at": scalar("string", format="date-time")}
)
ROW = {
    "properties": {
        key: {}
        for key in "event_type ts func module route provider model trace_id request_id session_id environment sdk sdk_version input_tokens output_tokens cache_read_tokens cache_write_tokens reasoning_tokens latency_ms cost_usd status error_type tags request_json response_json request_text response_text tool_definitions".split()
    },
    "type": "object",
    "additionalProperties": True,
    "description": "Native SDK/relay event row. Existing permissive normalization accepts aliases; arbitrary metadata and future fields stay open. OSS intentionally drops captured content.",
}
NATIVE = obj(
    {
        "schema_version": scalar("integer", const=1),
        "rows": array(ROW, minItems=1),
        "meta": {},
    },
    optional=("schema_version", "meta"),
)
NATIVE_REPLY = obj({"accepted": COUNT, "ignored": COUNT})


def contract(*, session=False):
    status = 201 if session else 202
    body = SESSION if session else NATIVE
    response = SESSION_REPLY if session else NATIVE_REPLY
    description = (
        "OSS session exchange uses an app bearer and protocol_version=2; success is 201 with session_token/expires_at (no repository_id). Invalid body is 400, unlike internal 422. "
        if session
        else "OSS native schema_version=1 JSON; missing version means 1. App or exchanged SDK session bearer; 202 returns accepted/ignored counts, without internal's raw-batch field or outbox promise. Outcome rows are ignored; content is dropped by the OSS projection. No ingestion idempotency support is promised by this profile. "
    )
    description += "Body can be plain JSON or Content-Encoding: gzip with decoded byte/row limits. No new runtime models or filtering."
    responses = {
        status: {
            "description": "Successful session exchange"
            if session
            else "Accepted native rows",
            "content": {"application/json": {"schema": response}},
        }
    }
    for code in (400, 401, 413):
        responses[code] = {
            "description": "Existing body/auth/size validation failure",
            "content": {"application/json": {"schema": ERROR}},
        }
    return {
        "description": description,
        "responses": responses,
        "openapi_extra": {
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": body}},
            },
            "parameters": [
                {
                    "name": "Content-Encoding",
                    "in": "header",
                    "required": False,
                    "schema": TEXT,
                    "description": "gzip or omitted",
                }
            ],
        },
    }
