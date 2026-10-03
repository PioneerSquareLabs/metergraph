"""Budgets for usage aggregates, independent of trace/route optimization."""

import json
from contextlib import contextmanager
from datetime import timedelta

from fastapi import HTTPException
from psycopg.errors import QueryCanceled
from psycopg_pool import PoolTimeout

MAX_WINDOW_DAYS = 91
GROUP_LIMIT = 500
MAX_RESPONSE_BYTES = 1024 * 1024
STATEMENT_TIMEOUT = "5s"
POOL_TIMEOUT_SECONDS = 2


def validate_window(start, end):
    if end - start > timedelta(days=MAX_WINDOW_DAYS):
        raise HTTPException(400, {
            "code": "usage_window_too_wide",
            "message": "Usage windows may span at most 91 days. Split the window or use paged calls for exhaustive retrieval.",
        })
    return start, end


@contextmanager
def connection(pool):
    try:
        with pool.connection(timeout=POOL_TIMEOUT_SECONDS) as con:
            con.execute(f"set local statement_timeout = '{STATEMENT_TIMEOUT}'")
            yield con
    except (QueryCanceled, PoolTimeout) as exc:
        raise HTTPException(503, {
            "code": "usage_query_busy",
            "message": "The usage query is busy. Narrow the window or filters and try again.",
        }, headers={"Retry-After": "5"}) from exc


def completeness(start, end, *, mode="top_n", complete=True, limit=GROUP_LIMIT, returned=0):
    return {"mode": mode, "complete": complete, "limit": limit, "returned": returned,
            "from": start.isoformat(), "to": end.isoformat()}


def response(payload):
    if len(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise HTTPException(503, {
            "code": "usage_response_too_large",
            "message": "The usage response is too large. Narrow the window or filters, or use paged calls.",
        }, headers={"Retry-After": "5"})
    return payload
