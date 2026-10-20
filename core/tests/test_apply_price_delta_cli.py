"""The reviewed additive-delta writer preserves hand-maintained catalog text."""

import importlib.util
import sys
from pathlib import Path

import yaml

from metergraph_core.catalog_delta import apply_catalog_delta

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "apply_price_delta.py"
_spec = importlib.util.spec_from_file_location("apply_price_delta", _SCRIPT)
apply_price_delta = importlib.util.module_from_spec(_spec)
sys.modules["apply_price_delta"] = apply_price_delta
_spec.loader.exec_module(apply_price_delta)


CATALOG = """\
# Keep this catalog comment.
version: "test"
currency: USD
pricing_verified_at: "2026-10-20"
models:
  - canonical_id: example/model-a
    publisher: example
    aliases:
      - {provider: openai, alias: model-a, channel: openai-api}
    prices:
      - channel: openai-api
        region: global
        effective_from: "2026-01-01"
        input_per_mtok: 1
        output_per_mtok: 4
        source_url: https://example.com/pricing
retrieval: []
"""


def _delta():
    alias = {
        "provider": "openrouter",
        "alias": "example/model-a",
        "channel": "openrouter-api",
    }
    price = {
        "channel": "openrouter-api",
        "region": "global",
        "effective_from": "2026-10-20",
        "input_per_mtok": "1",
        "output_per_mtok": "4",
        "source_url": "https://openrouter.ai/api/v1/models",
    }
    model = {
        "canonical_id": "example/model-b",
        "publisher": "example",
        "aliases": [dict(alias, alias="example/model-b")],
        "prices": [dict(price)],
    }
    return {
        "schema_version": 2,
        "kind": "pricing_delta",
        "provenance": {"observations": ["gateway@2026-10-20"]},
        "operations": [
            {
                "op": "add_alias",
                "key": dict(alias, canonical_id="example/model-a"),
                "expect": None,
                "alias": alias,
            },
            {
                "op": "add_price",
                "key": {
                    "canonical_id": "example/model-a",
                    "channel": "openrouter-api",
                    "region": "global",
                    "effective_from": "2026-10-20",
                },
                "expect": None,
                "price": price,
            },
            {
                "op": "add_model",
                "key": {"canonical_id": "example/model-b"},
                "expect": None,
                "model": model,
            },
        ],
    }


def test_render_additive_delta_preserves_comments_and_matches_pure_application():
    rendered = apply_price_delta.render_additive_delta(CATALOG, _delta())

    assert "# Keep this catalog comment." in rendered
    assert rendered.index("example/model-b") < rendered.index("retrieval:")
    expected = apply_catalog_delta(yaml.safe_load(CATALOG), _delta())
    assert yaml.safe_load(rendered) == expected


def test_render_additive_delta_rejects_history_changes():
    delta = _delta()
    delta["operations"] = [
        {
            "op": "close_price",
            "key": {
                "canonical_id": "example/model-a",
                "channel": "openai-api",
                "region": "global",
                "effective_from": "2026-01-01",
            },
            "expect": yaml.safe_load(CATALOG)["models"][0]["prices"][0],
            "effective_to": "2026-10-20",
        }
    ]
    delta["schema_version"] = 1

    try:
        apply_price_delta.render_additive_delta(CATALOG, delta)
    except apply_price_delta.ApplyDeltaError as exc:
        assert "only additive operations" in str(exc)
    else:
        raise AssertionError("history-changing delta was accepted")
