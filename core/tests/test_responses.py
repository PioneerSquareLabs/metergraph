"""Costing a provider response object as the caller received it.

The synthetic catalog pins each provider shape against deterministic numbers,
so the assertions never depend on the shipped price list.
"""

import textwrap
from datetime import datetime, timezone
from decimal import Decimal

import pytest
import yaml

from metergraph_core import (
    RESPONSE_FORMATS,
    cost_from_response,
    load_catalog,
    load_catalog_document,
    response_format,
)


_CATALOG = load_catalog_document(yaml.safe_load(textwrap.dedent(
    """
    version: "test-responses-1"
    currency: USD
    pricing_verified_at: "2026-08-01"
    models:
      - canonical_id: openai/gpt-x
        aliases:
          - {provider: openai, alias: gpt-x, channel: openai-api}
        prices:
          - channel: openai-api
            region: global
            effective_from: "2026-01-01"
            effective_to: "2026-06-01"
            input_per_mtok: 2.0
            output_per_mtok: 4.0
            cache_read_per_mtok: 0.2
            source_url: https://example.test/openai-old
          - channel: openai-api
            region: global
            effective_from: "2026-06-01"
            input_per_mtok: 1.0
            output_per_mtok: 2.0
            cache_read_per_mtok: 0.1
            source_url: https://example.test/openai
      - canonical_id: anthropic/claude-x
        aliases:
          - {provider: anthropic, alias: claude-x, channel: anthropic-api}
          - {provider: openrouter, alias: anthropic/claude-x, channel: openrouter-api}
          - {provider: bedrock, alias: anthropic.claude-x-v1:0, channel: aws-bedrock}
        prices:
          - channel: anthropic-api
            region: global
            effective_from: "2026-01-01"
            input_per_mtok: 3.0
            output_per_mtok: 15.0
            cache_read_per_mtok: 0.3
            cache_write_5m_per_mtok: 3.75
            cache_write_1h_per_mtok: 6.0
            batch_input_per_mtok: 1.5
            batch_output_per_mtok: 7.5
            source_url: https://example.test/anthropic
          - channel: openrouter-api
            region: global
            effective_from: "2026-01-01"
            input_per_mtok: 3.0
            output_per_mtok: 15.0
            cache_read_per_mtok: 0.3
            cache_write_5m_per_mtok: 3.75
            source_url: https://example.test/openrouter
          - channel: aws-bedrock
            region: global
            effective_from: "2026-01-01"
            input_per_mtok: 3.0
            output_per_mtok: 15.0
            cache_read_per_mtok: 0.3
            cache_write_5m_per_mtok: 3.75
            source_url: https://example.test/bedrock
      - canonical_id: google/gem-x
        aliases:
          - {provider: google, alias: gem-x, channel: google-api}
        prices:
          - channel: google-api
            region: global
            effective_from: "2026-01-01"
            input_per_mtok: 0.5
            output_per_mtok: 1.5
            cache_read_per_mtok: 0.05
            source_url: https://example.test/google
    """
)))

_JULY = datetime(2026, 7, 1, tzinfo=timezone.utc)
_MARCH = int(datetime(2026, 3, 1, tzinfo=timezone.utc).timestamp())


def _cost(provider, response, **kwargs):
    kwargs.setdefault("at", _JULY)
    return cost_from_response(provider, response, catalog=_CATALOG, **kwargs)


def _chat(**overrides):
    response = {
        "object": "chat.completion",
        "model": "gpt-x",
        "created": int(_JULY.timestamp()),
        "choices": [{"message": {"role": "assistant", "content": "synthetic"}}],
        "usage": {
            "prompt_tokens": 1000,
            "completion_tokens": 500,
            "prompt_tokens_details": {"cached_tokens": 400},
        },
    }
    response.update(overrides)
    return response


def _anthropic(**usage):
    return {
        "type": "message",
        "model": "claude-x",
        "content": [{"type": "text", "text": "synthetic"}],
        "usage": {
            "input_tokens": 100,
            "output_tokens": 200,
            "cache_read_input_tokens": 1000,
            "cache_creation_input_tokens": 500,
            "cache_creation": {"ephemeral_5m_input_tokens": 500, "ephemeral_1h_input_tokens": 0},
            **usage,
        },
    }


def test_openai_chat_completion_bills_cached_tokens_once():
    result = _cost("openai", _chat())

    assert result.status == "priced"
    assert result.model_id == "openai/gpt-x"
    # 600 uncached at $1, 400 cached at $0.10, 500 out at $2 per 1M.
    assert result.cost == Decimal("0.00164")


def test_openai_responses_api():
    result = _cost("openai", {
        "object": "response",
        "model": "gpt-x",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "synthetic"}]}],
        "usage": {
            "input_tokens": 1000,
            "output_tokens": 500,
            "input_tokens_details": {"cached_tokens": 400},
            "output_tokens_details": {"reasoning_tokens": 300},
        },
    })

    assert result.status == "priced"
    assert result.cost == Decimal("0.00164")


def test_anthropic_message_bills_cache_reads_and_writes():
    result = _cost("anthropic", _anthropic())

    assert result.status == "priced"
    # 100 in at $3, 200 out at $15, 1000 read at $0.30, 500 5m writes at $3.75.
    assert result.cost == Decimal("0.005475")


def test_anthropic_batch_tier_uses_batch_rates():
    result = _cost("anthropic", _anthropic(
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
        cache_creation=None,
        service_tier="batch",
    ))

    assert result.status == "priced"
    # 100 in at $1.50, 200 out at $7.50.
    assert result.cost == Decimal("0.00165")


def test_unpriced_service_tier_turns_the_result_partial():
    result = _cost("openai", _chat(service_tier="priority"))

    assert result.status == "partial"
    assert "service_tier" in result.missing_dimensions
    assert result.cost == Decimal("0.00164")


def test_service_tier_argument_overrides_the_response():
    batch = _cost("anthropic", _anthropic(
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
        cache_creation=None,
        service_tier="standard",
    ), service_tier="batch")
    partial = _cost("openai", _chat(service_tier="default"), service_tier="priority")

    assert batch.status == "priced"
    assert batch.cost == Decimal("0.00165")
    assert partial.status == "partial"
    assert "service_tier" in partial.missing_dimensions


def test_standard_service_tier_stays_priced():
    assert _cost("openai", _chat(service_tier="default")).status == "priced"


@pytest.mark.parametrize("response", [
    {
        "modelVersion": "gem-x",
        "usageMetadata": {
            "promptTokenCount": 1000,
            "cachedContentTokenCount": 200,
            "candidatesTokenCount": 100,
            "thoughtsTokenCount": 300,
        },
    },
    {
        "model_version": "gem-x",
        "usage_metadata": {
            "prompt_token_count": 1000,
            "cached_content_token_count": 200,
            "candidates_token_count": 100,
            "thoughts_token_count": 300,
        },
    },
], ids=["rest", "sdk"])
def test_gemini_bills_thinking_as_output(response):
    result = _cost("google", response)

    assert result.status == "priced"
    # 800 uncached at $0.50, 200 cached at $0.05, 400 out at $1.50 per 1M.
    assert result.cost == Decimal("0.00101")


def test_gateway_chat_shape_restates_input_for_a_row_that_does_not_deduct_cache():
    result = _cost("openrouter", _chat(
        model="anthropic/claude-x",
        usage={"prompt_tokens": 1000, "completion_tokens": 0, "prompt_tokens_details": {"cached_tokens": 400}},
    ))

    assert result.status == "priced"
    # 600 uncached at $3 and 400 cached at $0.30, not 1000 at $3 plus the reads.
    assert result.cost == Decimal("0.00192")


@pytest.mark.parametrize("shape", ["chat", "responses"])
def test_gateway_cache_writes_are_billed_at_the_write_rate(shape):
    if shape == "chat":
        response = _chat(model="anthropic/claude-x", usage={
            "prompt_tokens": 1000,
            "completion_tokens": 0,
            "prompt_tokens_details": {"cached_tokens": 400, "cache_write_tokens": 200},
        })
    else:
        response = {"object": "response", "model": "anthropic/claude-x", "usage": {
            "input_tokens": 1000,
            "output_tokens": 0,
            "input_tokens_details": {"cached_tokens": 400, "cache_write_tokens": 200},
        }}

    result = _cost("openrouter", response)

    assert result.status == "priced"
    # 400 uncached at $3, 400 reads at $0.30, 200 writes at $3.75 per 1M.
    assert result.cost == Decimal("0.00207")


@pytest.mark.parametrize(("tier", "status"), [("priority", "partial"), ("default", "priced")])
def test_bedrock_converse_reads_the_service_tier(tier, status):
    result = _cost("bedrock", {
        "usage": {"inputTokens": 100, "outputTokens": 10},
        "serviceTier": {"type": tier},
    }, model="anthropic.claude-x-v1:0")

    assert result.status == status
    assert ("service_tier" in result.missing_dimensions) == (status == "partial")


def test_bedrock_converse_takes_the_model_from_the_caller():
    response = {
        "output": {"message": {"role": "assistant", "content": [{"text": "synthetic"}]}},
        "usage": {"inputTokens": 100, "outputTokens": 10, "totalTokens": 110},
    }

    missing = _cost("bedrock", response)
    priced = _cost("bedrock", response, model="anthropic.claude-x-v1:0")

    assert missing.status == "unpriced"
    assert missing.missing_dimensions == ("model",)
    assert priced.status == "priced"
    assert priced.cost == Decimal("0.00045")


def test_creation_time_selects_the_price_in_effect():
    result = cost_from_response("openai", _chat(created=_MARCH), catalog=_CATALOG)

    # The March price: 600 at $2, 400 at $0.20, 500 at $4 per 1M.
    assert result.cost == Decimal("0.00328")
    assert result.effective_at == datetime(2026, 3, 1, tzinfo=timezone.utc)


def test_explicit_time_overrides_the_response():
    assert _cost("openai", _chat(created=_MARCH)).cost == Decimal("0.00164")


def test_sdk_objects_are_read_through_model_dump():
    class SdkResponse:
        def model_dump(self):
            return _chat()

    assert _cost("openai", SdkResponse()).cost == Decimal("0.00164")


def test_only_pricing_fields_are_read():
    read = set()

    class Watched(dict):
        def get(self, key, default=None):
            read.add(key)
            return super().get(key, default)

        def __getitem__(self, key):
            read.add(key)
            return super().__getitem__(key)

    _cost("openai", Watched(_chat()))

    assert "choices" not in read


def test_a_stated_format_reads_a_response_detection_cannot_place():
    sparse = {"model": "claude-x", "usage": {"input_tokens": 100, "output_tokens": 200}}

    with pytest.raises(ValueError):
        _cost("anthropic", sparse)
    result = _cost("anthropic", sparse, format="anthropic.messages")

    assert result.status == "priced"
    # 100 in at $3, 200 out at $15 per 1M.
    assert result.cost == Decimal("0.0033")


def test_an_unknown_format_is_rejected():
    with pytest.raises(ValueError):
        _cost("openai", _chat(), format="openai.completions")


def test_response_format_names_each_shape():
    assert response_format(_chat()) == "openai.chat_completions"
    assert response_format(_anthropic()) == "anthropic.messages"
    assert response_format({"object": "response", "usage": {"input_tokens": 1}}) == "openai.responses"
    assert response_format({"usageMetadata": {"promptTokenCount": 1}}) == "gemini.generate_content"
    assert response_format({"usage": {"inputTokens": 1}}) == "bedrock.converse"
    assert response_format({"model": "gpt-x"}) is None
    assert set(RESPONSE_FORMATS) == {
        "openai.chat_completions",
        "openai.responses",
        "anthropic.messages",
        "gemini.generate_content",
        "bedrock.converse",
    }


def test_a_missing_model_keeps_the_effective_time():
    result = _cost("bedrock", {"usage": {"inputTokens": 1, "outputTokens": 1}})

    assert result.effective_at == _JULY


def test_unknown_model_is_unpriced():
    assert _cost("openai", _chat(model="gpt-unknown")).status == "unpriced"


def test_a_response_without_usage_is_rejected():
    with pytest.raises(ValueError):
        _cost("openai", {"model": "gpt-x"})


def test_a_non_mapping_response_is_rejected():
    with pytest.raises(TypeError):
        _cost("openai", "gpt-x")


def test_defaults_to_the_bundled_catalog():
    document = load_catalog().document
    model, alias = next(
        (model, alias)
        for model in document["models"]
        for alias in model.get("aliases") or []
        if alias.get("channel") == "openai-api"
        and any(
            price.get("channel") == "openai-api" and price.get("effective_to") is None
            and price.get("input_per_mtok") is not None
            for price in model.get("prices") or []
        )
    )

    result = cost_from_response("openai", _chat(model=alias["alias"], created=None))

    assert result.model_id == model["canonical_id"]
    assert result.cost is not None
