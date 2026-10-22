"""Versioned call-feed continuation. A cursor never grants authorization."""

import base64
import binascii
import hashlib
import json
from datetime import datetime, timezone

from fastapi import HTTPException


def _scope_hash(scope: dict) -> str:
    return hashlib.sha256(
        json.dumps(scope, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def encode_call_cursor(timestamp: datetime, call_id: int, scope: dict) -> str:
    payload = {
        "v": 1,
        "ts": timestamp.astimezone(timezone.utc).isoformat(),
        "id": call_id,
        "scope": _scope_hash(scope),
    }
    encoded = (
        base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )
    return "calls1_" + encoded


def decode_call_cursor(cursor: str, scope: dict) -> tuple[datetime, int]:
    try:
        if not cursor.startswith("calls1_") or len(cursor) > 2048:
            raise ValueError
        token = cursor[len("calls1_") :]
        payload = json.loads(
            base64.b64decode(
                token + "=" * (-len(token) % 4), altchars=b"-_", validate=True
            )
        )
        if not isinstance(payload, dict) or set(payload) != {"v", "ts", "id", "scope"}:
            raise ValueError
        if type(payload["v"]) is not int or payload["v"] != 1:
            raise ValueError
        if type(payload["id"]) is not int or not 0 < payload["id"] <= 2**63 - 1:
            raise ValueError
        if payload["scope"] != _scope_hash(scope):
            raise ValueError
        timestamp = datetime.fromisoformat(payload["ts"])
        if timestamp.tzinfo is None:
            raise ValueError
        return timestamp.astimezone(timezone.utc), payload["id"]
    except (ValueError, TypeError, KeyError, binascii.Error, RecursionError):
        raise HTTPException(400, "invalid cursor or changed call filters") from None
