from datetime import datetime, timezone
from decimal import Decimal

from metergraph_core import load_catalog


def test_bundled_catalog_has_identity_and_prices_a_call():
    loaded = load_catalog()
    assert loaded.version == "2026-08-19"
    assert len(loaded.content_hash) == 64
    result = loaded.snapshot.cost(
        provider="openai",
        model="gpt-5.4-mini",
        at=datetime(2026, 8, 17, tzinfo=timezone.utc),
        input_tokens=100_000,
        output_tokens=100_000,
    )
    assert result.canonical_model == "openai/gpt-5.4-mini"
    assert result.price_id == "openai/gpt-5.4-mini:openai-api:global:2026-03-17"
    assert result.cost_usd == Decimal("0.52500000")
    assert result.status == "priced"
    assert result.reasons == ()
