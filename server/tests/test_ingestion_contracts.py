import pytest
from jsonschema import Draft202012Validator, ValidationError
from metergraph_server import ingestion_contracts
from metergraph_server.main import app


@pytest.mark.parametrize(
    "path,status", [("/v1/ingest/sessions", "201"), ("/v1/ingest", "202")]
)
def test_oss_ingestion_profile_publishes_explicit_decoded_schemas(path, status):
    operation = app.openapi()["paths"][path]["post"]
    assert operation["requestBody"]["required"]
    schema = operation["responses"][status]["content"]["application/json"]["schema"]
    Draft202012Validator.check_schema(schema)
    assert set(schema["properties"]) >= (
        {"session_token", "expires_at"} if status == "201" else {"accepted", "ignored"}
    )
    assert "batch" not in schema["properties"]
    assert "repository_id" not in schema["properties"]


def test_oss_session_native_schemas_preserve_profile_and_open_fields():
    Draft202012Validator(ingestion_contracts.SESSION).validate(
        {
            "protocol_version": 2,
            "repository": "example.com/demo",
            "sdk_version": "0.6.10",
        }
    )
    Draft202012Validator(ingestion_contracts.NATIVE).validate(
        {"rows": [{"future_field": {"x": [1, None]}, "request_json": {"messages": []}}]}
    )
    Draft202012Validator(ingestion_contracts.NATIVE_REPLY).validate(
        {"accepted": 1, "ignored": 0}
    )
    with pytest.raises(ValidationError):
        Draft202012Validator(ingestion_contracts.NATIVE).validate({"rows": []})


def test_session_protocol_constraint_and_actual_response(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from metergraph_server.ingest import router

    monkeypatch.setenv("MG_TOKENS", "synthetic-contract-token")
    body = {
        "protocol_version": 2,
        "repository": "synthetic/contracts",
        "sdk_version": "0.6.10",
    }
    with pytest.raises(ValidationError):
        Draft202012Validator(ingestion_contracts.SESSION).validate(
            {**body, "protocol_version": 1}
        )
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).post(
        "/v1/ingest/sessions",
        json=body,
        headers={"Authorization": "Bearer synthetic-contract-token"},
    )
    assert response.status_code == 201
    schema = app.openapi()["paths"]["/v1/ingest/sessions"]["post"]["responses"]["201"][
        "content"
    ]["application/json"]["schema"]
    Draft202012Validator(schema).validate(response.json())
