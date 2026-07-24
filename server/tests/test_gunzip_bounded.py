"""Pure-logic tests for the bounded gzip decompression guard.

No database required — these exercise _gunzip_bounded/_decode_body directly,
the same way test_projection.py tests ingest.py's projection logic without
standing up the FastAPI app.
"""

import gzip
import json

import pytest
from fastapi import HTTPException

from metergraph_server.ingest import _decode_body, _gunzip_bounded


def _gz(data: bytes) -> bytes:
    return gzip.compress(data)


def test_decode_body_plain_json():
    body = json.dumps({"rows": [{"a": 1}]}).encode()
    assert _decode_body(body, None, limit=1_000_000) == {"rows": [{"a": 1}]}


def test_decode_body_gzip_roundtrip():
    payload = json.dumps({"rows": [{"a": 1}]}).encode()
    assert _decode_body(_gz(payload), "gzip", limit=1_000_000) == {"rows": [{"a": 1}]}


def test_gunzip_bounded_accepts_exact_limit():
    payload = b"x" * 2000
    assert _gunzip_bounded(_gz(payload), limit=2000) == payload


def test_gunzip_bounded_rejects_one_byte_over_limit():
    payload = b"x" * 2001
    with pytest.raises(HTTPException) as exc_info:
        _gunzip_bounded(_gz(payload), limit=2000)
    assert exc_info.value.status_code == 413


def test_gunzip_bounded_handles_multi_chunk_payload():
    # Payload well over 64KB so any internal chunking has to span multiple
    # rounds; guards against the offset/unconsumed_tail bug caught in review.
    payload = bytes((i % 256) for i in range(500_000))
    assert _gunzip_bounded(_gz(payload), limit=len(payload)) == payload


def test_gunzip_bounded_rejects_decompression_bomb():
    # ~1000:1 ratio: tens of MB decompressed from a payload under 50KB
    # compressed. A limit far below the true decompressed size proves the
    # cap is enforced during decompression, not only after full expansion.
    bomb = gzip.compress(b"0" * 20_000_000, compresslevel=9)
    assert len(bomb) < 50_000
    with pytest.raises(HTTPException) as exc_info:
        _gunzip_bounded(bomb, limit=1000)
    assert exc_info.value.status_code == 413


def test_gunzip_bounded_rejects_malformed_gzip():
    with pytest.raises(Exception):
        _gunzip_bounded(b"not a gzip stream", limit=1000)


def test_decode_body_wraps_malformed_gzip_as_400():
    with pytest.raises(HTTPException) as exc_info:
        _decode_body(b"not a gzip stream", "gzip", limit=1000)
    assert exc_info.value.status_code == 400


def test_decode_body_rejects_oversized_compressed_input_before_gunzip():
    body = _gz(b"x" * 10)
    with pytest.raises(HTTPException) as exc_info:
        _decode_body(body, "gzip", limit=1)
    assert exc_info.value.status_code == 413
