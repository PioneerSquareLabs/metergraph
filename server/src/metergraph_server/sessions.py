"""Short-lived session tokens used by repository-aware SDK ingestion."""

import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone


_PREFIX = "mgs1"
_DEFAULT_TTL_SECONDS = 300
_MIN_TTL_SECONDS = 60
_MAX_TTL_SECONDS = 3600


def _ttl_seconds() -> int:
    try:
        configured = int(
            os.environ.get("MG_SESSION_TTL_SECONDS", _DEFAULT_TTL_SECONDS)
        )
    except ValueError:
        return _DEFAULT_TTL_SECONDS
    return max(_MIN_TTL_SECONDS, min(configured, _MAX_TTL_SECONDS))


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.b64decode(
        value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
    )


def issue_session_token(
    app_token: str,
    repository: str,
    *,
    now: float | None = None,
) -> tuple[str, datetime]:
    issued_at = time.time() if now is None else now
    expires_at = datetime.fromtimestamp(
        int(issued_at) + _ttl_seconds(), tz=timezone.utc
    )
    payload = _encode(
        json.dumps(
            {"exp": int(expires_at.timestamp()), "repository": repository},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    )
    signed = f"{_PREFIX}.{payload}"
    signature = _encode(
        hmac.new(app_token.encode(), signed.encode(), hashlib.sha256).digest()
    )
    return f"{signed}.{signature}", expires_at


def is_valid_session_token(
    token: str,
    app_tokens: list[str],
    *,
    now: float | None = None,
) -> bool:
    try:
        prefix, encoded_payload, encoded_signature = token.split(".")
        if prefix != _PREFIX:
            return False
        payload = json.loads(_decode(encoded_payload))
        signature = _decode(encoded_signature)
        if not isinstance(payload, dict):
            return False
        expires_at = payload.get("exp")
        repository = payload.get("repository")
        if (
            isinstance(expires_at, bool)
            or not isinstance(expires_at, int)
            or not isinstance(repository, str)
            or not repository
            or expires_at <= (time.time() if now is None else now)
        ):
            return False
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return False

    signed = f"{prefix}.{encoded_payload}".encode()
    return any(
        hmac.compare_digest(
            signature,
            hmac.new(app_token.encode(), signed, hashlib.sha256).digest(),
        )
        for app_token in app_tokens
    )
