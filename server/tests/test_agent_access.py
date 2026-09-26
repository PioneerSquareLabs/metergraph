"""Agent Access conformance tests. Require Postgres: set MG_TEST_DATABASE_URL."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from server.tests.agent_access import conformance


TEST_DSN = os.environ.get("MG_TEST_DATABASE_URL")

APP_TOKEN = "app-token"
AGENT_TOKEN = "agent-read-token"
AUTH = {"Authorization": f"Bearer {AGENT_TOKEN}"}
SENTINEL = "PROMPT-CONTENT-MUST-NOT-APPEAR"
FIXTURE = Path(__file__).parent / "agent_access/fixtures/oss-content-blind.json"


@pytest.fixture()
def client(monkeypatch):
    if not TEST_DSN:
        pytest.skip("MG_TEST_DATABASE_URL not set")
    monkeypatch.setenv("DATABASE_URL", TEST_DSN)
    monkeypatch.setenv("MG_TOKENS", APP_TOKEN)
    monkeypatch.setenv("MG_AGENT_TOKENS", AGENT_TOKEN)
    monkeypatch.setenv("MG_WORKSPACE_SLUG", "oss-test")
    monkeypatch.setenv("MG_WORKSPACE_NAME", "OSS Test Workspace")
    monkeypatch.delenv("MG_RETENTION_DAYS", raising=False)

    import psycopg

    with psycopg.connect(TEST_DSN) as con:
        con.execute("drop table if exists calls, schema_migrations")

    from fastapi.testclient import TestClient

    from metergraph_server import db
    from metergraph_server.main import create_app

    db.close()
    with TestClient(create_app()) as test_client:
        _seed(test_client)
        yield test_client
    db.close()


def _seed(client) -> None:
    now = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for index in range(240):
        trace_id = f"trace-{index:03d}" if index % 5 else None
        rows.append(
            {
                "ts": (now - timedelta(minutes=index)).isoformat(),
                "func": f"app.worker:run_{index % 3}",
                "route": ("summarize", "classify", "extract")[index % 3],
                "provider": ("openai", "anthropic")[index % 2],
                "model": ("gpt-5.6-luna", "claude-haiku-4-5")[index % 2],
                "input_tokens": 100 + index,
                "output_tokens": 20 + index % 40,
                "cache_read_tokens": index % 7,
                "cache_write_tokens": index % 5,
                "cost_usd": 0.01,
                "latency_ms": 100 + index % 80,
                "status": "error" if index % 11 == 0 else "success",
                "error": index % 11 == 0,
                "error_type": "TimeoutError" if index % 11 == 0 else None,
                "trace_id": trace_id,
                "request_json": SENTINEL,
                "response_text": SENTINEL,
            }
        )
    response = client.post(
        "/v1/ingest",
        json={"schema_version": 1, "rows": rows},
        headers={"Authorization": f"Bearer {APP_TOKEN}"},
    )
    assert response.status_code == 202, response.text
    assert response.json() == {"accepted": 240, "ignored": 0}


def _call(client, name: str, arguments: dict[str, Any]):
    response = client.post(
        "/v1/agent/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    message = response.json()
    if "error" in message:
        return message["error"], True
    result = message["result"]
    return result.get("structuredContent"), bool(result.get("isError"))


def test_shared_agent_access_conformance(client):
    profile = json.loads(FIXTURE.read_text())
    assert conformance.check_capability_discovery(_call_adapter(client), profile) == []
    assert conformance.check_content_free(_call_adapter(client), profile) == []
    assert conformance.check_bounds(_call_adapter(client)) == []
    assert conformance.check_schemas(_call_adapter(client)) == []


def _call_adapter(client):
    return lambda name, arguments: _call(client, name, arguments)


def test_unsupported_capabilities_and_auth_boundaries(client):
    for name, arguments in (
        ("metergraph_get_ingestion_health", {}),
        ("metergraph_list_incidents", {}),
        ("metergraph_list_reports", {}),
        ("metergraph_get_report", {"analysis_id": "missing"}),
        ("metergraph_get_report_evidence", {"analysis_id": "missing", "workload_id": "missing"}),
        ("metergraph_get_trace", {"trace_id": "missing"}),
        ("metergraph_replay_trace", {"trace_id": "missing"}),
    ):
        document, is_error = _call(client, name, arguments)
        assert is_error is True
        assert document["error"]["code"] == "unsupported_capability"

    unknown, is_error = _call(client, "metergraph_unknown", {})
    assert is_error is True
    assert unknown["code"] == -32602

    response = client.post(
        "/v1/ingest",
        json={"rows": [{}]},
        headers=AUTH,
    )
    assert response.status_code == 401

    app_read = client.get(
        "/v1/agent/workspace",
        headers={"Authorization": f"Bearer {APP_TOKEN}"},
    )
    assert app_read.status_code == 200


def test_agent_responses_do_not_expose_content_fields(client):
    responses = [
        client.get("/v1/agent/workspace", headers=AUTH).json(),
        client.get("/v1/agent/capabilities", headers=AUTH).json(),
        client.get("/v1/agent/routes", headers=AUTH).json(),
        client.get("/v1/agent/usage", headers=AUTH).json(),
        client.get("/v1/agent/traces", headers=AUTH).json(),
    ]

    def keys(value):
        if isinstance(value, dict):
            for key, child in value.items():
                yield key
                yield from keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from keys(child)

    forbidden = {
        "text",
        "tool_definitions",
        "tool_calls",
        "prompt",
        "system",
        "messages",
        "input",
        "output",
    }
    assert not (set(keys(responses)) & forbidden)
    assert SENTINEL not in json.dumps(responses)


def test_mcp_notifications_and_stdio_tools_list(client):
    notification = client.post(
        "/v1/agent/mcp",
        json={"jsonrpc": "2.0", "method": "ping"},
        headers=AUTH,
    )
    assert notification.status_code == 202
    assert notification.content == b""

    from metergraph_server import agent_mcp

    class FakeAPI:
        pass

    response = agent_mcp.handle_message(
        FakeAPI(), {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    )
    assert response["result"]["tools"] == agent_mcp.TOOLS


def test_agent_contract_covers_tools_and_fixture_exactly():
    from metergraph_server import agent_contract, agent_mcp

    names = {tool["name"] for tool in agent_mcp.TOOLS}
    assert set(agent_contract.TOOL_CONTRACT) == names
    profile = json.loads(FIXTURE.read_text())
    assert set(profile["tools"]) == names
