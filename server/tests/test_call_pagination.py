import base64
import json
from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from metergraph_server import usage


@pytest.fixture
def feed(monkeypatch):
    timestamp = datetime(2026, 8, 1, tzinfo=timezone.utc)
    rows = []
    for call_id in range(7, 0, -1):
        values = [None] * 34
        values[0:2] = [timestamp, "f"]
        values[3] = "checkout"
        values[27] = "same-trace"
        values[30] = "production"
        values[33] = f"request-{call_id}"
        rows.append((*values, call_id))
    connection = Mock()

    def execute(sql, params):
        assert "order by ts desc, id desc" in sql
        selected = rows
        if "(ts, id) <" in sql:
            cutoff = params[-3:-1]
            selected = [row for row in rows if (row[0], row[-1]) < cutoff]
        elif "ts < %s" in sql:
            selected = [row for row in rows if row[0] < params[-2]]
        return SimpleNamespace(fetchall=lambda: selected[: params[-1]])

    connection.execute.side_effect = execute
    pool = Mock(
        return_value=SimpleNamespace(connection=lambda: nullcontext(connection))
    )
    monkeypatch.setattr(usage.db, "pool", pool)
    monkeypatch.setenv("MG_TOKENS", "synthetic-contract-token")
    app = FastAPI()
    app.include_router(usage.router)
    return (
        TestClient(app, headers={"Authorization": "Bearer synthetic-contract-token"}),
        connection,
        pool,
    )


def test_traverse_ties_preserves_items_and_filters(feed):
    client, connection, _ = feed
    query = {
        "limit": 2,
        "func": "f",
        "route": "checkout",
        "environment": ["production", "demo"],
    }
    found = []
    for _ in range(4):
        response = client.get("/v1/calls", params=query)
        assert response.status_code == 200
        body = response.json()
        found.extend(item["request_id"] for item in body["items"])
        assert all("id" not in item for item in body["items"])
        assert body["page"]["limit"] == 2
        if not body["page"]["has_more"]:
            assert body["page"]["next_cursor"] is None
            break
        query["cursor"] = body["page"]["next_cursor"]
        query["environment"].reverse()
    assert found == [f"request-{i}" for i in range(7, 0, -1)]
    for call in connection.execute.call_args_list:
        sql, params = call.args
        assert (
            "environment = any(%s)" in sql
            and "route = %s" in sql
            and "func = %s" in sql
        )
        assert params[-1] == 3


@pytest.mark.parametrize(
    "changed",
    [
        {"route": "other"},
        {"func": "other"},
        {"environment": "other"},
        {"before": "2026-08-01T00:00:00Z"},
    ],
)
def test_changed_filters_and_before_rejected_before_db(feed, changed):
    client, _, pool = feed
    cursor = client.get("/v1/calls?limit=2").json()["page"]["next_cursor"]
    pool.reset_mock()
    response = client.get("/v1/calls", params={"cursor": cursor, **changed})
    assert response.status_code == 400
    pool.assert_not_called()


def test_cursor_still_requires_valid_application_bearer(feed):
    client, _, pool = feed
    cursor = client.get("/v1/calls?limit=2").json()["page"]["next_cursor"]
    pool.reset_mock()
    assert (
        client.get(
            "/v1/calls",
            params={"cursor": cursor},
            headers={"Authorization": "Bearer invalid"},
        ).status_code
        == 401
    )
    pool.assert_not_called()


@pytest.mark.parametrize(
    "cursor",
    [
        "",
        "garbage",
        "calls1_%%%%",
        "calls1_" + base64.urlsafe_b64encode(json.dumps({"v": 99}).encode()).decode(),
    ],
)
def test_invalid_cursor_is_client_error_without_db(feed, cursor):
    client, _, pool = feed
    assert client.get("/v1/calls", params={"cursor": cursor}).status_code == 400
    pool.assert_not_called()


def test_legacy_timestamp_before_and_empty_page_stay_compatible(feed):
    client, _, _ = feed
    response = client.get("/v1/calls?before=2026-08-01T00:00:00Z&limit=2")
    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "page": {"next_cursor": None, "has_more": False, "limit": 2},
    }
