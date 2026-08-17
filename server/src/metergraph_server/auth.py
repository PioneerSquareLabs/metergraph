import hashlib
import hmac
import os

from fastapi import HTTPException, Request

from .sessions import is_valid_session_token


def _tokens() -> list[str]:
    raw = os.environ.get("MG_TOKENS", "")
    return [token.strip() for token in raw.split(",") if token.strip()]


def _presented_token(request: Request) -> str:
    header = request.headers.get("authorization") or ""
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer" or not presented.strip():
        raise HTTPException(401, "missing bearer token")
    return presented.strip()


def _is_app_token(presented: str, app_tokens: list[str]) -> bool:
    presented_digest = hashlib.sha256(presented.encode()).digest()
    for token in app_tokens:
        expected = hashlib.sha256(token.encode()).digest()
        if hmac.compare_digest(presented_digest, expected):
            return True
    return False


def authenticated_app_token(request: Request) -> str:
    presented = _presented_token(request)
    if _is_app_token(presented, _tokens()):
        return presented
    raise HTTPException(401, "invalid token")


def require_token(request: Request) -> None:
    authenticated_app_token(request)


def require_ingest_token(request: Request) -> None:
    presented = _presented_token(request)
    app_tokens = _tokens()
    if _is_app_token(presented, app_tokens) or is_valid_session_token(
        presented, app_tokens
    ):
        return
    raise HTTPException(401, "invalid token")
