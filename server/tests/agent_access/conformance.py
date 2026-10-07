"""Pure conformance checks for Agent Access clients.

The checks intentionally know only the versioned contract and a callable that
looks like ``call(name, arguments) -> (document, is_error)``. An OSS server can
vendor this package without importing the hosted application.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from metergraph_server import agent_contract


Call = Callable[[str, dict[str, Any]], tuple[dict[str, Any], bool]]
HTTPCall = Callable[..., Any]


def _document(result: Any, is_error: bool) -> dict[str, Any] | None:
    if is_error or not isinstance(result, dict):
        return None
    return result


def _error_code(result: Any) -> Any:
    if not isinstance(result, Mapping):
        return None
    error = result.get("error")
    if isinstance(error, Mapping):
        return error.get("code")
    return result.get("code")


def _path(document: Any, path: str) -> tuple[bool, Any]:
    value = document
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return False, None
        value = value[part]
    return True, value


def _metadata_tools(profile: Mapping[str, Any]) -> list[str]:
    return [
        name
        for name, spec in profile["tools"].items()
        if spec["privacy_class"] == "metadata" and spec["available"]
    ]


def _walk_keys(value: Any):
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield str(key)
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def check_capability_discovery(call: Call, profile: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
    result, is_error = call("metergraph_get_capabilities", {})
    document = _document(result, is_error)
    if document is None:
        return ["metergraph_get_capabilities did not return a successful document"]

    if document.get("deployment_profile") != profile["deployment_profile"]:
        failures.append(
            "deployment_profile does not match the profile fixture"
        )
    agent = document.get("agent")
    if not isinstance(agent, Mapping):
        return failures + ["capability document has no agent map"]
    actual_names = {
        spec.get("capability")
        for spec in agent_contract.TOOL_CONTRACT.values()
        if isinstance(spec, Mapping)
    }
    if set(agent) != actual_names:
        failures.append("capability document does not cover the contract capabilities")

    for tool_name, expected in profile["tools"].items():
        capability = agent_contract.TOOL_CONTRACT[tool_name]["capability"]
        actual = agent.get(capability)
        if not isinstance(actual, Mapping):
            failures.append(f"missing capability for {tool_name}")
            continue
        for field in ("privacy_class", "required_scope", "available"):
            if actual.get(field) != expected[field]:
                failures.append(
                    f"{tool_name}.{field} expected {expected[field]!r}, got {actual.get(field)!r}"
                )
    bounds = document.get("bounds")
    if not isinstance(bounds, Mapping):
        failures.append("capability document has no bounds")
    else:
        for field, expected in profile["bounds"].items():
            if bounds.get(field) != expected:
                failures.append(
                    f"bounds.{field} expected {expected!r}, got {bounds.get(field)!r}"
                )
        if bounds.get("content_included_by_default") != profile["content_included_by_default"]:
            failures.append("content_included_by_default does not match the profile fixture")
    return failures


def check_content_free(call: Call, profile: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
    for name in _metadata_tools(profile):
        arguments: dict[str, Any] = {}
        if name == "metergraph_list_reports":
            arguments = {"limit": 1}
        elif name == "metergraph_get_analysis_run":
            listed, listed_error = call("metergraph_list_analysis_runs", {"limit": 1})
            runs = listed.get("runs", []) if not listed_error and isinstance(listed, Mapping) else []
            if not runs:
                continue
            arguments = {"run_id": runs[0].get("run_id")}
        elif name == "metergraph_get_report":
            listed, listed_error = call("metergraph_list_reports", {"limit": 1})
            reports = listed.get("reports", []) if not listed_error and isinstance(listed, Mapping) else []
            if not reports:
                continue
            arguments = {"analysis_id": reports[0].get("analysis_id")}
        result, is_error = call(name, arguments)
        document = _document(result, is_error)
        if document is None:
            failures.append(f"{name} did not return a successful document")
            continue
        for key in _walk_keys(document):
            if key in agent_contract.CONTENT_FIELD_NAMES:
                failures.append(f"{name} returned content field {key!r}")
        included, value = _path(document, "content_included")
        if included and value is not False:
            failures.append(f"{name}.content_included is not false")
    return failures


def check_bounds(call: Call) -> list[str]:
    failures: list[str] = []
    for name, arguments in (
        ("metergraph_get_usage", {"days": 91}),
        ("metergraph_get_usage", {"limit": 201}),
        ("metergraph_list_reports", {"limit": 201}),
        ("metergraph_list_analysis_runs", {"limit": 201}),
    ):
        result, is_error = call(name, arguments)
        if not is_error or _error_code(result) != -32602:
            failures.append(f"{name} {arguments} did not return JSON-RPC -32602")

    first, is_error = call("metergraph_query_traces", {"days": 90, "limit": 1})
    first_document = _document(first, is_error)
    if first_document is None:
        return failures + ["bounded trace query did not return a document"]
    page = first_document.get("page")
    cursor = first_document.get("next_cursor")
    if not isinstance(page, Mapping) or page.get("truncated") is not True or not cursor:
        return failures + ["limit=1 trace query did not return a truncated page cursor"]
    second, second_error = call(
        "metergraph_query_traces", {"days": 90, "limit": 1, "cursor": cursor}
    )
    second_document = _document(second, second_error)
    if second_document is None:
        return failures + ["trace continuation did not return a document"]
    first_ids = {item.get("trace_id") for item in first_document.get("traces", [])}
    second_ids = {item.get("trace_id") for item in second_document.get("traces", [])}
    if first_ids & second_ids:
        failures.append("trace continuation repeated a trace from the first page")
    return failures


def check_stable_errors(call: Call) -> list[str]:
    failures: list[str] = []
    unknown, unknown_error = call("metergraph_unknown", {})
    if not unknown_error or _error_code(unknown) != -32602:
        failures.append("unknown tool did not return JSON-RPC -32602")

    replay, replay_error = call(
        "metergraph_replay_trace", {"trace_id": "missing-trace"}
    )
    if not replay_error or _error_code(replay) != "forbidden":
        failures.append("replay without scope did not return forbidden")

    missing, missing_error = call(
        "metergraph_get_trace", {"trace_id": "missing-trace"}
    )
    if not missing_error or _error_code(missing) != "not_found":
        failures.append("unknown trace id did not return not_found")
    return failures


def check_schemas(call: Call) -> list[str]:
    failures: list[str] = []
    listed, listed_error = call("metergraph_list_reports", {"limit": 1})
    reports = listed.get("reports", []) if not listed_error and isinstance(listed, Mapping) else []
    report_id = reports[0].get("analysis_id") if reports else None
    run_list, run_error = call("metergraph_list_analysis_runs", {"limit": 1})
    runs = run_list.get("runs", []) if not run_error and isinstance(run_list, Mapping) else []
    run_id = runs[0].get("run_id") if runs else None
    report_workload_id = None
    if report_id is not None:
        detail, detail_error = call("metergraph_get_report", {"analysis_id": report_id})
        if not detail_error and isinstance(detail, Mapping):
            workloads = detail.get("workloads", [])
            if isinstance(workloads, list) and workloads:
                report_workload_id = workloads[0].get("workload_id")
    for name, contract in agent_contract.TOOL_CONTRACT.items():
        arguments = {"days": 90, "limit": 200} if name == "metergraph_get_usage" else {}
        if name == "metergraph_get_ingestion_health":
            arguments = {"days": 90}
        elif name == "metergraph_list_incidents":
            arguments = {"limit": 200}
        elif name == "metergraph_query_traces":
            arguments = {"days": 90, "limit": 200}
        elif name == "metergraph_get_analysis_run":
            if run_id is None:
                continue
            arguments = {"run_id": run_id}
        elif name in {"metergraph_list_reports", "metergraph_list_analysis_runs"}:
            arguments = {"limit": 200}
        elif name == "metergraph_get_report":
            if report_id is None:
                continue
            arguments = {"analysis_id": report_id}
        elif name == "metergraph_get_report_evidence":
            if report_id is None or report_workload_id is None:
                continue
            arguments = {
                "analysis_id": report_id,
                "workload_id": report_workload_id,
                "limit": 1,
            }
        elif name in {"metergraph_get_trace", "metergraph_replay_trace"}:
            arguments = {"trace_id": "missing-trace"}
        result, is_error = call(name, arguments)
        document = _document(result, is_error)
        if document is None:
            continue
        try:
            agent_contract.validate(contract["schema"], document)
        except Exception as exc:  # jsonschema exposes several validator errors
            failures.append(f"{name} failed {contract['schema']} validation: {exc}")
    return failures


def check_abuse_controls(call_http: HTTPCall) -> list[str]:
    """Exercise the HTTP-only origin, size, and redirect boundaries."""
    failures: list[str] = []
    origin = call_http(
        "POST",
        "/v1/agent/mcp",
        headers={"Origin": "https://not-the-deployment.example"},
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
    )
    if origin.status_code != 403 or origin.json().get("error", {}).get("code") != "forbidden":
        failures.append("foreign Origin was not rejected with forbidden")

    oversized = call_http(
        "POST",
        "/v1/agent/mcp",
        content=b"{" + b"a" * (1_048_576 + 1) + b"}",
        headers={"Content-Type": "application/json"},
    )
    if oversized.status_code != 413 or oversized.json().get("error", {}).get("code") != -32700:
        failures.append("oversized MCP body was not rejected with JSON-RPC parse error")

    for path in ("/v1/agent/mcp/", "/v1/agent/routes/"):
        redirected = call_http("GET" if path.endswith("routes/") else "POST", path)
        if redirected.status_code != 404:
            failures.append(f"{path} returned {redirected.status_code}, expected 404")
    return failures
