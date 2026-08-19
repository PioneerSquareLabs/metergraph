import json

from metergraph_server import prices
from metergraph_server.ingest import COLUMNS, project_row

_, _, SNAPSHOT = prices.load()

SENTINEL = "TOP-SECRET-PROMPT-CONTENT"


def _row(**overrides) -> dict:
    row = {
        "ts": "2026-07-15T12:00:00+00:00",
        "route": "ticket-classifier",
        "func": "app.billing:summarize",
        "module": "app.billing",
        "provider": "openai",
        "model": "gpt-5.6-luna",
        "endpoint": "chat.completions",
        "input_tokens": 1000,
        "output_tokens": 100,
        "cache_read_tokens": 200,
        "reasoning_tokens": 10,
        "latency_ms": 812,
        "status": "stop",
        "session_id": "ticket-123",
        "trace_id": "a" * 32,
        "template_hash": "abc123",
        "tags": {"team": "billing"},
        "environment": "production",
        "sdk": "python",
        "sdk_version": "0.2.0",
        "request_id": "req_1",
        "stream": False,
        "error": False,
    }
    row.update(overrides)
    return row


def test_projection_maps_and_prices():
    values = dict(zip(COLUMNS, project_row(_row(), SNAPSHOT)))
    assert values["func"] == "app.billing:summarize"
    assert values["canonical_model"] == "openai/gpt-5.6-luna"
    assert values["cost_status"] == "priced"
    assert values["cost_usd"] is not None and values["cost_usd"] > 0
    assert values["reasoning_tokens"] == 10
    assert values["trace_id"] == "a" * 32


def test_content_fields_never_survive():
    row = _row(
        request_json=json.dumps({"messages": [{"content": SENTINEL}]}),
        response_text=SENTINEL,
        content_opted_in=True,
        frames_json=[{"m": "app", "f": "fn", "l": 1}],
        tool_calls=[
            {
                "name": "lookup_account",
                "arguments": SENTINEL,
                "result": SENTINEL,
                "status": "completed",
                "idempotency": "idempotent",
            }
        ],
    )
    values = project_row(row, SNAPSHOT)
    assert SENTINEL not in repr(values)
    named = dict(zip(COLUMNS, values))
    assert json.loads(named["tool_names"]) == ["lookup_account"]


def test_route_falls_back_to_template_hash():
    values = dict(zip(COLUMNS, project_row(_row(route=None), SNAPSHOT)))
    assert values["route"] == "template:abc123"


def test_unknown_model_lands_unpriced():
    values = dict(zip(COLUMNS, project_row(_row(model="mystery-1"), SNAPSHOT)))
    assert values["cost_status"] == "unpriced"
    assert values["cost_usd"] is None


def test_malformed_values_are_tolerated():
    values = dict(
        zip(
            COLUMNS,
            project_row(
                _row(
                    ts="not-a-date",
                    input_tokens="many",
                    stream="yes",
                    tags="not-a-dict",
                ),
                SNAPSHOT,
            ),
        )
    )
    assert values["ts"] is not None
    assert values["input_tokens"] is None
    assert values["stream"] is None
    assert values["tags"] is None
