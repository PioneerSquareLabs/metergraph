from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from psycopg.errors import QueryCanceled
from psycopg_pool import PoolTimeout

from fastapi import FastAPI
from metergraph_server import analytical_reads as reads, usage
from metergraph_server.auth import require_token

NOW = datetime(2026, 8, 1, tzinfo=timezone.utc)


def test_window_budget_has_headroom_for_90_day_preset_and_bounds_custom_range():
    assert reads.validate_window(NOW, NOW + timedelta(days=90, seconds=60))
    with pytest.raises(HTTPException) as error:
        reads.validate_window(NOW, NOW + timedelta(days=91, seconds=1))
    assert error.value.status_code == 400 and error.value.detail["code"] == "usage_window_too_wide"


@pytest.mark.parametrize("exception", [QueryCanceled("statement timeout"), PoolTimeout("checkout timeout")])
def test_cancellation_is_retryable_without_returning_partial_result(exception):
    pool = SimpleNamespace(connection=lambda **_kw: nullcontext(Mock()))
    with pytest.raises(HTTPException) as error, reads.connection(pool):
        raise exception
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "usage_query_busy"
    assert error.value.headers == {"Retry-After": "5"}


def test_byte_budget_counts_utf8_and_fails_explicitly():
    assert reads.response({"items": []}) == {"items": []}
    with pytest.raises(HTTPException) as error:
        reads.response({"items": ["é" * reads.MAX_RESPONSE_BYTES]})
    assert error.value.detail["code"] == "usage_response_too_large"


def client_with_rows(monkeypatch, rows):
    con = Mock()
    con.execute.return_value = SimpleNamespace(fetchall=lambda: rows)
    pool = Mock()
    pool.connection.return_value = nullcontext(con)
    monkeypatch.setattr(usage.db, "pool", lambda: pool)
    app = FastAPI()
    app.include_router(usage.router)
    app.dependency_overrides[require_token] = lambda: None
    client = TestClient(app)
    return client, con, pool


@pytest.mark.parametrize("count,complete", [(0, True), (500, True), (501, False)])
def test_group_limit_probe_distinguishes_exact_limit_from_truncation(monkeypatch, count, complete):
    row = ("synthetic", 1, Decimal("1"), 10, 20, 0, 0, None, None, 0, 0, 0)
    client, con, pool = client_with_rows(monkeypatch, [row] * count)
    response = client.get("/v1/usage?group_by=route&from=2026-08-01&to=2026-08-08")
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == min(count, 500)
    assert data["completeness"]["complete"] is complete
    assert data["completeness"]["mode"] == "top_n"
    assert data["completeness"]["returned"] == min(count, 500)
    assert "limit 501" in con.execute.call_args.args[0]
    assert "statement_timeout" in con.execute.call_args_list[0].args[0]
    pool.connection.assert_called_once_with(timeout=2)


@pytest.mark.parametrize("path", ["/v1/usage", "/v1/usage/timeseries", "/v1/environments"])
def test_wide_window_rejected_before_checkout(monkeypatch, path):
    pool = Mock(side_effect=AssertionError("DB reached"))
    client, _, _ = client_with_rows(monkeypatch, [])
    monkeypatch.setattr(usage.db, "pool", pool)
    response = client.get(path + "?from=2026-01-01&to=2026-08-01")
    assert response.status_code == 400
    pool.assert_not_called()


def test_timeseries_other_keeps_cost_and_does_not_confuse_real_other_key(monkeypatch):
    rows = [("2026-08-01", "other", Decimal("2"), False),
            ("2026-08-01", "other", Decimal("3"), True)]
    client, con, _ = client_with_rows(monkeypatch, rows)
    response = client.get("/v1/usage/timeseries?top=1&from=2026-08-01T02:00:00%2B02:00&to=2026-08-02T02:00:00%2B02:00")
    data = response.json()
    assert response.status_code == 200 and data["buckets"] == ["2026-08-01"]
    assert data["series"] == [{"key": "other", "values": [2.0]}, {"key": "other", "values": [3.0]}]
    assert data["completeness"]["other_series_index"] == 1
    assert data["completeness"]["complete"] is True
    assert "top_keys" in con.execute.call_args.args[0]
    assert con.execute.call_args.args[1][-1] == 1


def test_environment_catalogue_never_initializes_from_partial_choices(monkeypatch):
    rows = [("environment-" + str(i), 1) for i in range(501)]
    client, _, _ = client_with_rows(monkeypatch, rows)
    response = client.get("/v1/environments?from=2026-08-01&to=2026-08-08")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "usage_response_too_large"


@pytest.mark.parametrize("count", [0, 500])
def test_environment_success_declares_complete_choices(monkeypatch, count):
    rows = [("synthetic-environment-" + str(i), 1) for i in range(count)]
    client, _, _ = client_with_rows(monkeypatch, rows)
    response = client.get("/v1/environments?from=2026-08-01&to=2026-08-08")
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == count
    assert data["completeness"]["mode"] == "distinct"
    assert data["completeness"]["complete"] is True
    assert data["completeness"]["returned"] == count
