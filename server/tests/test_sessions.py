from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from metergraph_server.auth import require_ingest_token, require_token
from metergraph_server.sessions import issue_session_token, is_valid_session_token


def _request(token: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/ingest",
            "headers": [(b"authorization", f"Bearer {token}".encode())],
        }
    )


def test_issued_session_token_verifies_without_exposing_app_token():
    token, expires_at = issue_session_token(
        "app-secret", "github.com/acme/widgets", now=1_700_000_000
    )

    assert token.startswith("mgs1.")
    assert "app-secret" not in token
    assert expires_at == datetime.fromtimestamp(1_700_000_300, timezone.utc)
    assert is_valid_session_token(token, ["app-secret"], now=1_700_000_299)


def test_session_token_rejects_expiry_tampering_and_other_app_tokens():
    token, _ = issue_session_token(
        "app-secret", "github.com/acme/widgets", now=1_700_000_000
    )

    assert not is_valid_session_token(token, ["app-secret"], now=1_700_000_300)
    assert not is_valid_session_token(token + "x", ["app-secret"], now=1_700_000_001)
    assert not is_valid_session_token(
        token, ["different-secret"], now=1_700_000_001
    )


def test_session_token_rejects_a_non_object_payload():
    assert not is_valid_session_token("mgs1.W10.eA", ["app-secret"])


def test_session_token_authentication_is_limited_to_ingest(monkeypatch):
    monkeypatch.setenv("MG_TOKENS", "app-secret")
    token, _ = issue_session_token("app-secret", "github.com/acme/widgets")

    require_ingest_token(_request(token))
    with pytest.raises(HTTPException) as error:
        require_token(_request(token))

    assert error.value.status_code == 401
