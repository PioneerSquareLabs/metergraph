"""Read-only stdio MCP bridge for Metergraph's agent API.

The bearer token is loaded from the process environment as required for MCP
stdio transports. It is sent only to the configured Metergraph origin and is
never written to stdout/stderr.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

SERVER_NAME = "metergraph"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOLS = {"2025-03-26", "2025-06-18", PROTOCOL_VERSION}
MAX_MESSAGE_BYTES = 1_048_576
MAX_RESPONSE_BYTES = 5_242_880


TOOLS = [
    {
        "name": "metergraph_get_workspace_context",
        "title": "Get Metergraph workspace context",
        "description": (
            "Describe the authenticated tenant, retention, access scopes, and "
            "content policy without returning member or customer content."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "metergraph_get_capabilities",
        "title": "Discover Metergraph capabilities",
        "description": (
            "Discover which Agent Access capabilities are available in this "
            "deployment and the bounds that apply to every read."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "metergraph_list_routes",
        "title": "List Metergraph routes",
        "description": (
            "List this tenant's LLM routes, constraints, evaluation contracts, "
            "call counts, and replay-eligible counts. Never returns prompts or outputs."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "metergraph_get_usage",
        "title": "Get Metergraph usage summary",
        "description": (
            "Read bounded, content-free usage by route and UTC day, including "
            "cost, tokens, latency, and error counts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "minimum": 1, "maximum": 90},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "metergraph_get_ingestion_health",
        "title": "Get Metergraph ingestion health",
        "description": (
            "Read bounded ingestion batch counts, failure classes, and recent "
            "receive/process timestamps for the authenticated tenant."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "minimum": 1, "maximum": 90},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "metergraph_list_incidents",
        "title": "List Metergraph incidents",
        "description": (
            "List bounded tenant-scoped detector incidents without delivery "
            "secrets or trace content."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "metergraph_query_traces",
        "title": "Query Metergraph trace metadata",
        "description": (
            "Query recent content-free LLM trace metadata: route, model, tokens, "
            "cost, latency, outcome, session identifiers, and unit economics."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "route": {"type": "string", "minLength": 1, "maxLength": 512},
                "status": {"type": "string", "minLength": 1, "maxLength": 128},
                "workload": {"type": "string", "minLength": 1, "maxLength": 200},
                "cursor": {"type": "string", "minLength": 1, "maxLength": 512},
                "days": {"type": "integer", "minimum": 1, "maximum": 90},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "metergraph_list_analysis_runs",
        "title": "List Metergraph analysis runs",
        "description": "List a bounded page of workspace analysis and classification runs. Lifecycle is separate from imported report availability and outcome. Read-only.",
        "inputSchema": {"type": "object", "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            "cursor": {"type": "string", "minLength": 1, "maxLength": 512},
        }, "additionalProperties": False},
    },
    {
        "name": "metergraph_get_analysis_run",
        "title": "Get a Metergraph analysis run",
        "description": "Read one workspace-owned analysis run by UUID, with lifecycle timestamps and report availability. Does not start, cancel or rerun analysis.",
        "inputSchema": {"type": "object", "properties": {
            "run_id": {"type": "string", "minLength": 1, "maxLength": 36},
        }, "required": ["run_id"], "additionalProperties": False},
    },
    {
        "name": "metergraph_list_reports",
        "title": "List Metergraph pipeline reports",
        "description": (
            "List bounded tenant-scoped pipeline report metadata, including "
            "schema version, provenance, workload coverage, outcomes, and artifact identity."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 200}},
            "additionalProperties": False,
        },
    },
    {
        "name": "metergraph_get_report",
        "title": "Get a Metergraph pipeline report",
        "description": (
            "Retrieve one tenant-scoped report's metadata and safe workload projections. "
            "Download the stored artifact through the bounded HTTP artifact path."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"analysis_id": {"type": "string", "minLength": 1, "maxLength": 200}},
            "required": ["analysis_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "metergraph_get_report_evidence",
        "title": "Get pipeline report evidence",
        "description": (
            "Read one bounded page of replay evidence for a report workload. "
            "Evidence is content-class data and is never included in report metadata."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "analysis_id": {"type": "string", "minLength": 1, "maxLength": 200},
                "workload_id": {"type": "string", "minLength": 1, "maxLength": 200},
                "cursor": {"type": "string", "minLength": 1, "maxLength": 512},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["analysis_id", "workload_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "metergraph_get_trace",
        "title": "Get a Metergraph trace",
        "description": (
            "Retrieve one tenant-scoped trace with captured input and output "
            "content, tool definitions, tool calls, errors, analysis context, "
            "and bounded replay eligibility."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "trace_id": {"type": "string", "minLength": 1, "maxLength": 200},
            },
            "required": ["trace_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "metergraph_replay_trace",
        "title": "Replay a Metergraph trace",
        "description": (
            "Run one bounded, non-persistent replay of a captured trace. Only "
            "configured model routes, prompt/system text, temperature, top_p, "
            "and output-token overrides are accepted. The operation cannot write "
            "to production."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "trace_id": {"type": "string", "minLength": 1, "maxLength": 200},
                "overrides": {
                    "type": "object",
                    "properties": {
                        "model": {"type": "string", "minLength": 1, "maxLength": 200},
                        "prompt": {"type": "string", "minLength": 1, "maxLength": 16384},
                        "system": {"type": "string", "minLength": 1, "maxLength": 16384},
                        "temperature": {"type": "number", "minimum": 0, "maximum": 1},
                        "top_p": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
                        "max_output_tokens": {"type": "integer", "minimum": 1, "maximum": 1024},
                    },
                    "additionalProperties": False,
                },
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 30},
            },
            "required": ["trace_id"],
            "additionalProperties": False,
        },
    },
]


class AgentAPIError(RuntimeError):
    pass


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AgentAPI:
    def __init__(self, base_url: str, token: str, timeout: float = 20.0):
        parsed = urllib.parse.urlsplit(base_url.rstrip("/"))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("METERGRAPH_URL must be an absolute HTTP(S) URL")
        if parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError("METERGRAPH_URL must contain only scheme, host, and path")
        if not token:
            raise ValueError("METERGRAPH_AGENT_TOKEN is required")
        self.base_url = urllib.parse.urlunsplit(parsed).rstrip("/")
        self.token = token
        self.timeout = timeout
        self.opener = urllib.request.build_opener(_RejectRedirects())

    def get(self, path: str, query: dict[str, Any] | None = None) -> dict:
        return self._request("GET", path, query=query)

    def post(self, path: str, payload: dict[str, Any]) -> dict:
        return self._request("POST", path, payload=payload)

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict:
        url = f"{self.base_url}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        body = None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "User-Agent": f"metergraph-mcp/{SERVER_VERSION}",
        }
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            if len(body) > MAX_MESSAGE_BYTES:
                raise AgentAPIError("replay request exceeded 1 MiB")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers=headers,
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read(4096).decode("utf-8", "replace")
            except OSError:
                pass
            raise AgentAPIError(
                f"Metergraph API returned HTTP {exc.code}"
                + (f": {detail}" if detail else "")
            ) from exc
        except urllib.error.URLError as exc:
            raise AgentAPIError(f"Metergraph API request failed: {exc.reason}") from exc
        if len(body) > MAX_RESPONSE_BYTES:
            raise AgentAPIError("Metergraph API response exceeded 5 MiB")
        try:
            document = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AgentAPIError("Metergraph API returned invalid JSON") from exc
        if not isinstance(document, dict):
            raise AgentAPIError("Metergraph API returned a non-object response")
        return document


def _validate_arguments(name: str, arguments: Any) -> dict[str, Any]:
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be an object")
    limits = {
        "metergraph_get_workspace_context": set(),
        "metergraph_get_capabilities": set(),
        "metergraph_list_routes": set(),
        "metergraph_get_usage": {"days", "limit"},
        "metergraph_get_ingestion_health": {"days"},
        "metergraph_list_incidents": {"limit"},
        "metergraph_query_traces": {"route", "status", "workload", "cursor", "days", "limit"},
        "metergraph_list_analysis_runs": {"limit", "cursor"},
        "metergraph_get_analysis_run": {"run_id"},
        "metergraph_list_reports": {"limit"},
        "metergraph_get_report": {"analysis_id"},
        "metergraph_get_report_evidence": {"analysis_id", "workload_id", "cursor", "limit"},
        "metergraph_get_trace": {"trace_id"},
        "metergraph_replay_trace": {"trace_id", "overrides", "timeout_seconds"},
    }
    if name not in limits:
        raise KeyError(name)
    unknown = set(arguments) - limits[name]
    if unknown:
        raise ValueError(f"unknown tool arguments: {sorted(unknown)!r}")
    for field, maximum in (
        ("route", 512),
        ("status", 128),
        ("workload", 200),
        ("cursor", 512),
        ("run_id", 36),
        ("trace_id", 200),
        ("analysis_id", 200),
        ("workload_id", 200),
    ):
        value = arguments.get(field)
        if value is not None and (
            not isinstance(value, str) or not value or len(value) > maximum
        ):
            raise ValueError(
                f"{field} must be a non-empty string of at most {maximum} characters"
            )
    integer_limits = {"days": (1, 90), "limit": (1, 200)}
    if name == "metergraph_get_report_evidence":
        integer_limits["limit"] = (1, 50)
    for field, (minimum, maximum) in integer_limits.items():
        value = arguments.get(field)
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not minimum <= value <= maximum
        ):
            raise ValueError(f"{field} must be an integer from {minimum} to {maximum}")
    if name == "metergraph_get_analysis_run" and "run_id" not in arguments:
        raise ValueError("run_id is required")
    if name == "metergraph_get_trace" and "trace_id" not in arguments:
        raise ValueError("trace_id is required")
    if name in {"metergraph_get_report", "metergraph_get_report_evidence"} and "analysis_id" not in arguments:
        raise ValueError("analysis_id is required")
    if name == "metergraph_get_report_evidence" and "workload_id" not in arguments:
        raise ValueError("workload_id is required")
    if name == "metergraph_replay_trace":
        if "trace_id" not in arguments:
            raise ValueError("trace_id is required")
        timeout = arguments.get("timeout_seconds")
        if timeout is not None and (
            isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 30
        ):
            raise ValueError("timeout_seconds must be an integer from 1 to 30")
        overrides = arguments.get("overrides")
        if overrides is not None and not isinstance(overrides, dict):
            raise ValueError("overrides must be an object")
    return arguments


def _call_tool(api: AgentAPI, name: str, arguments: Any) -> dict:
    arguments = _validate_arguments(name, arguments)
    if name == "metergraph_get_workspace_context":
        document = api.get("/v1/agent/workspace")
    elif name == "metergraph_get_capabilities":
        document = api.get("/v1/agent/capabilities")
    elif name == "metergraph_list_routes":
        document = api.get("/v1/agent/routes")
    elif name == "metergraph_get_usage":
        document = api.get("/v1/agent/usage", arguments)
    elif name == "metergraph_get_ingestion_health":
        document = api.get("/v1/agent/ingestion-health", arguments)
    elif name == "metergraph_list_incidents":
        document = api.get("/v1/agent/incidents", arguments)
    elif name == "metergraph_query_traces":
        document = api.get("/v1/agent/traces", arguments)
    elif name == "metergraph_list_analysis_runs":
        document = api.get("/v1/agent/analysis-runs", arguments)
    elif name == "metergraph_get_analysis_run":
        document = api.get("/v1/agent/analysis-runs/" + urllib.parse.quote(arguments["run_id"], safe=""))
    elif name == "metergraph_list_reports":
        document = api.get("/v1/agent/reports", arguments)
    elif name == "metergraph_get_report":
        document = api.get(
            "/v1/agent/reports/" + urllib.parse.quote(arguments["analysis_id"], safe="")
        )
    elif name == "metergraph_get_report_evidence":
        document = api.get(
            "/v1/agent/reports/"
            + urllib.parse.quote(arguments["analysis_id"], safe="")
            + "/workloads/"
            + urllib.parse.quote(arguments["workload_id"], safe="")
            + "/evidence",
            {key: value for key, value in arguments.items() if key not in {"analysis_id", "workload_id"}},
        )
    elif name == "metergraph_get_trace":
        document = api.get(
            "/v1/agent/debug/traces/" + urllib.parse.quote(arguments["trace_id"], safe="")
        )
    elif name == "metergraph_replay_trace":
        document = api.post("/v1/agent/debug/replay", arguments)
    else:
        raise KeyError(name)
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(document, sort_keys=True, separators=(",", ":")),
            }
        ],
        "structuredContent": document,
        "isError": False,
    }


def _response(
    request_id: Any, *, result: Any = None, error: dict | None = None
) -> dict:
    message = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        message["error"] = error
    else:
        message["result"] = result
    return message


def handle_message(api: AgentAPI, message: Any) -> dict | None:
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _response(None, error={"code": -32600, "message": "Invalid Request"})
    request_id = message.get("id")
    method = message.get("method")
    if request_id is None:
        return None
    if method == "initialize":
        params = message.get("params") or {}
        requested = params.get("protocolVersion")
        selected = requested if requested in SUPPORTED_PROTOCOLS else PROTOCOL_VERSION
        return _response(
            request_id,
            result={
                "protocolVersion": selected,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": (
                    "Metergraph retrieval is tenant-scoped. Trace replay is bounded, "
                    "non-persistent, and cannot write to production."
                ),
            },
        )
    if method == "ping":
        return _response(request_id, result={})
    if method == "tools/list":
        return _response(request_id, result={"tools": TOOLS})
    if method == "tools/call":
        params = message.get("params")
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            return _response(
                request_id,
                error={"code": -32602, "message": "Invalid tool call parameters"},
            )
        try:
            result = _call_tool(api, params["name"], params.get("arguments"))
        except KeyError:
            return _response(
                request_id,
                error={"code": -32602, "message": "Unknown tool"},
            )
        except ValueError as exc:
            return _response(
                request_id,
                error={"code": -32602, "message": str(exc)},
            )
        except AgentAPIError as exc:
            return _response(
                request_id,
                result={
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            )
        return _response(request_id, result=result)
    return _response(request_id, error={"code": -32601, "message": "Method not found"})


def serve(api: AgentAPI) -> None:
    for raw_line in sys.stdin.buffer:
        if len(raw_line) > MAX_MESSAGE_BYTES:
            response = _response(
                None, error={"code": -32700, "message": "Message exceeds 1 MiB"}
            )
        else:
            try:
                message = json.loads(raw_line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                response = _response(
                    None, error={"code": -32700, "message": "Parse error"}
                )
            else:
                response = handle_message(api, message)
        if response is not None:
            wire = json.dumps(response, separators=(",", ":"), ensure_ascii=False)
            sys.stdout.write(wire + "\n")
            sys.stdout.flush()


def main() -> None:
    try:
        api = AgentAPI(
            os.environ.get("METERGRAPH_URL", ""),
            os.environ.get("METERGRAPH_AGENT_TOKEN", ""),
        )
    except ValueError as exc:
        print(f"metergraph-mcp: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    serve(api)


if __name__ == "__main__":
    main()
