"""Shared producer schemas describe actual HTTP bodies, not filtered models."""
from copy import deepcopy
from decimal import Decimal

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from pydantic import AwareDatetime, TypeAdapter

from metergraph_server import telemetry_contracts as contracts
from .test_analytical_read_budgets import client_with_rows

CHECKER = FormatChecker()


@CHECKER.checks("date-time")
def aware_timestamp(value):
    if not isinstance(value, str):
        return True
    try:
        TypeAdapter(AwareDatetime).validate_python(value)
        return True
    except ValueError:
        return False


def validate(response, path, client):
    schema = client.app.openapi()["paths"][path]["get"]["responses"][str(response.status_code)]["content"]["application/json"]["schema"]
    Draft202012Validator(schema, format_checker=CHECKER).validate(response.json())


@pytest.mark.parametrize("path", ["/v1/usage", "/v1/usage/timeseries", "/v1/environments", "/v1/calls"])
def test_every_scoped_operation_has_explicit_success_and_error_schemas(monkeypatch, path):
    client, _, _ = client_with_rows(monkeypatch, [])
    op = client.app.openapi()["paths"][path]["get"]
    assert op["responses"]["200"]["content"]["application/json"]["schema"]["required"]
    Draft202012Validator.check_schema(op["responses"]["200"]["content"]["application/json"]["schema"])
    assert {"200", "400", "401", "422"} <= set(op["responses"])
    client.close()


def test_actual_usage_and_error_bodies_validate(monkeypatch):
    row = ("synthetic", 1, Decimal("0.5"), 1, 2, 0, 0, None, None, 0, 0, 0)
    client, _, _ = client_with_rows(monkeypatch, [row])
    with client:
        response = client.get("/v1/usage?from=2026-08-01&to=2026-08-02")
        assert response.status_code == 200
        validate(response, "/v1/usage", client)
        assert "cache_write_tokens" not in response.json()["items"][0]
        for query in ["from=bad", "from=2026-01-01&to=2026-08-01", "group_by=bad"]:
            response = client.get("/v1/usage?" + query)
            assert response.status_code == 400
            validate(response, "/v1/usage", client)
        response = client.get("/v1/calls?limit=501")
        assert response.status_code == 422
        validate(response, "/v1/calls", client)


def test_actual_timeseries_and_catalog_bodies_validate(monkeypatch):
    client, _, _ = client_with_rows(monkeypatch, [("2026-08-01", "other", Decimal("2"), False), ("2026-08-01", "other", Decimal("3"), True)])
    with client:
        response = client.get("/v1/usage/timeseries?top=1&from=2026-08-01&to=2026-08-02")
        assert response.status_code == 200
        validate(response, "/v1/usage/timeseries", client)
    client, _, _ = client_with_rows(monkeypatch, [("synthetic", 1), (None, 5)])
    with client:
        response = client.get("/v1/environments?from=2026-08-01&to=2026-08-02")
        assert response.status_code == 200
        validate(response, "/v1/environments", client)
        assert response.json()["items"][-1] == {"value": None, "calls": 5}


def test_actual_calls_preserve_nullable_extensions_and_future_fields(monkeypatch):
    from datetime import datetime, timezone
    row = [None] * 34
    row[0] = datetime(2026, 8, 1, tzinfo=timezone.utc)
    row[16] = []
    row[29] = ["synthetic-tool"]
    client, _, _ = client_with_rows(monkeypatch, [(*row, 1)])
    with client:
        response = client.get("/v1/calls")
        assert response.status_code == 200
        validate(response, "/v1/calls", client)
        item = response.json()["items"][0]
        assert item["tool_names"] == ["synthetic-tool"]
    item["future_metadata"] = {"custom": [True, None, 3]}
    validator = Draft202012Validator(contracts.CALL, format_checker=CHECKER)
    validator.validate(item)
    for field, value in [("ts", "bad"), ("input_tokens", "12"), ("error", "false")]:
        bad = deepcopy(item)
        bad[field] = value
        with pytest.raises(ValidationError):
            validator.validate(bad)
