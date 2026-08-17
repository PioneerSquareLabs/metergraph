import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from metergraph_server.ingest import router


TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("MG_TOKENS", TOKEN)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_sdk_0_4_session_exchange_returns_a_short_lived_token(client):
    response = client.post(
        "/v1/ingest/sessions",
        json={
            "protocol_version": 2,
            "repository": "github.com/acme/widgets",
            "sdk_version": "0.4.0",
        },
        headers=AUTH,
    )

    assert response.status_code == 201
    assert response.json()["session_token"].startswith("mgs1.")
    assert response.json()["expires_at"].endswith("Z")


@pytest.mark.parametrize(
    "body",
    [
        {"protocol_version": 2, "sdk_version": "0.4.0"},
        {"protocol_version": 2, "repository": " ", "sdk_version": "0.4.0"},
        {"protocol_version": 2, "repository": "github.com/acme/widgets"},
        {
            "protocol_version": 2,
            "repository": "github.com/acme/widgets",
            "sdk_version": " ",
        },
        {
            "protocol_version": 1,
            "repository": "github.com/acme/widgets",
            "sdk_version": "0.4.0",
        },
    ],
)
def test_session_exchange_rejects_malformed_sdk_requests(client, body):
    response = client.post("/v1/ingest/sessions", json=body, headers=AUTH)

    assert response.status_code == 400
