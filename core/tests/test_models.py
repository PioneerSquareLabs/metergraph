from types import MappingProxyType

import pytest

from metergraph_core import load_catalog
from metergraph_core.models import (
    ModelRegistryError,
    load_model_registry,
    parse_model_registry,
    validate_model_registry,
)


def document():
    return {
        "version": "2026-09-25",
        "models": [
            {
                "canonical_id": "openai/gpt-5.6-sol",
                "display_name": "GPT-5.6 Sol",
                "publisher": "openai",
                "routes": [
                    {
                        "key": "gateway:openai/gpt-5.6-sol",
                        "id": "openai/gpt-5.6-sol",
                        "provider": "vercel",
                        "model_id": "openai/gpt-5.6-sol",
                        "pricing_channel": "vercel-ai-gateway",
                        "execution_profiles": ["default"],
                    },
                    {
                        "key": "openai-direct:openai:gpt-5.6",
                        "id": "openai:gpt-5.6",
                        "provider": "openai",
                        "model_id": "gpt-5.6",
                        "display_name": "GPT-5.6 Sol (direct)",
                        "pricing_channel": "openai-api",
                        "execution_profiles": ["default"],
                    },
                ],
            }
        ],
        "execution_profiles": [
            {
                "id": "default",
                "routes": [
                    "gateway:openai/gpt-5.6-sol",
                    "openai-direct:openai:gpt-5.6",
                ],
            }
        ],
        "offer_groups": [
            {
                "id": "gateway",
                "credential": "AI_GATEWAY_API_KEY",
                "routes": ["gateway:openai/gpt-5.6-sol"],
            },
            {
                "id": "openai-direct",
                "credential": "OPENAI_API_KEY",
                "routes": ["openai-direct:openai:gpt-5.6"],
            },
        ],
    }


def test_registry_version_accepts_a_same_day_revision():
    value = document()
    value["version"] = "2026-09-25.2"
    assert parse_model_registry(value).version == "2026-09-25.2"


@pytest.mark.parametrize(
    "version",
    ["2026-09-25.0", "2026-09-25.01", "2026-09-25.next", "2026-09-25.1.1"],
)
def test_registry_version_rejects_invalid_same_day_revisions(version):
    value = document()
    value["version"] = version
    with pytest.raises(ModelRegistryError, match="ISO date"):
        parse_model_registry(value)


def test_parse_model_registry_keeps_routes_immutable_and_distinct():
    registry = parse_model_registry(document())
    gateway, direct = registry.routes_for_execution_profile("default")
    assert gateway.canonical_id == direct.canonical_id == "openai/gpt-5.6-sol"
    assert gateway.display_name == "GPT-5.6 Sol"
    assert direct.display_name == "GPT-5.6 Sol (direct)"
    assert registry.model("openai/gpt-5.6-sol").publisher == "openai"
    assert isinstance(registry.models, MappingProxyType)
    with pytest.raises(TypeError):
        registry.routes["another:model"] = gateway


def test_offer_groups_and_credentials_preserve_declared_order():
    registry = parse_model_registry(document())
    assert [route.id for route in registry.candidates("gateway")] == [
        "openai/gpt-5.6-sol"
    ]
    assert [
        route.id
        for route in registry.reachable_candidates(
            {"OPENAI_API_KEY", "AI_GATEWAY_API_KEY"}
        )
    ] == ["openai/gpt-5.6-sol", "openai:gpt-5.6"]


def test_execution_profiles_preserve_declared_route_order():
    value = document()
    value["execution_profiles"][0]["routes"].reverse()
    registry = parse_model_registry(value)
    assert [route.id for route in registry.routes_for_execution_profile("default")] == [
        "openai:gpt-5.6",
        "openai/gpt-5.6-sol",
    ]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("duplicate_model", "duplicate canonical model"),
        ("duplicate_route", "duplicate route id"),
        ("duplicate_candidate", "duplicate candidate id"),
        ("duplicate_physical_route", "duplicate physical route"),
        ("duplicate_offer_group", "duplicate offer group"),
        ("unknown_offer_group", "unknown offer group"),
        ("unknown_offer_route", "unknown route"),
        ("incompatible_offer_route", "not available to execution profile"),
        ("incompatible_offer_provider", "requires provider"),
        ("unexpected_credential", "requires credential"),
        ("unknown_execution_profile", "unknown execution profile"),
        ("unknown_provider", "unknown route provider"),
        ("incompatible_provider_channel", "cannot use pricing channel"),
        ("publisher_mismatch", "publisher"),
        ("blank_route_field", "needs provider"),
    ],
)
def test_invalid_registry_references_fail_closed(mutation, message):
    value = document()
    if mutation == "duplicate_model":
        value["models"].append({**value["models"][0]})
    elif mutation == "duplicate_route":
        value["models"].append(
                {
                    **value["models"][0],
                    "canonical_id": "other/model",
                    "publisher": "other",
                    "routes": [{**value["models"][0]["routes"][0]}],
            }
        )
    elif mutation == "duplicate_candidate":
        value["models"][0]["routes"].append(
            {
                **value["models"][0]["routes"][0],
                "key": "gateway:duplicate-key",
            }
        )
    elif mutation == "duplicate_physical_route":
        value["models"][0]["routes"].append(
            {
                **value["models"][0]["routes"][0],
                "key": "gateway:duplicate-key",
                "id": "openai/duplicate-id",
            }
        )
    elif mutation == "duplicate_offer_group":
        value["offer_groups"].append({**value["offer_groups"][0]})
    elif mutation == "unknown_offer_group":
        value["offer_groups"][0]["id"] = "another"
    elif mutation == "unknown_offer_route":
        value["offer_groups"][0]["routes"] = ["missing:route"]
    elif mutation == "incompatible_offer_route":
        value["models"][0]["routes"][0]["execution_profiles"] = ["bedrock"]
    elif mutation == "incompatible_offer_provider":
        value["offer_groups"][0]["routes"] = [
            "openai-direct:openai:gpt-5.6"
        ]
    elif mutation == "unexpected_credential":
        value["offer_groups"][0]["credential"] = "AI_GATEWAY_APY_KEY"
    elif mutation == "unknown_execution_profile":
        value["models"][0]["routes"][0]["execution_profiles"] = ["another"]
    elif mutation == "unknown_provider":
        value["models"][0]["routes"][0]["provider"] = "vercle"
    elif mutation == "incompatible_provider_channel":
        value["offer_groups"].pop()
        value["models"][0]["routes"][1]["provider"] = "anthropic"
    elif mutation == "publisher_mismatch":
        value["models"][0]["publisher"] = "opena1"
    else:
        value["models"][0]["routes"][0]["provider"] = "   "
    with pytest.raises(ModelRegistryError, match=message):
        parse_model_registry(value)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_version",
        "models_not_list",
        "routes_not_list",
        "execution_profiles_not_list",
        "offer_groups_not_list",
    ],
)
def test_registry_document_shape_is_required(mutation):
    value = document()
    if mutation == "missing_version":
        value.pop("version")
    elif mutation == "models_not_list":
        value["models"] = {}
    elif mutation == "routes_not_list":
        value["models"][0]["routes"] = {}
    elif mutation == "execution_profiles_not_list":
        value["execution_profiles"] = {}
    else:
        value["offer_groups"] = {}
    with pytest.raises(ModelRegistryError):
        parse_model_registry(value)


def test_bundled_registry_preserves_current_execution_and_product_fields():
    registry = load_model_registry()
    assert registry.version == "2026-09-25.2"
    assert [route.id for route in registry.candidates("gateway")] == [
        "anthropic/claude-sonnet-5",
        "anthropic/claude-opus-5",
        "anthropic/claude-opus-4.8",
        "anthropic/claude-haiku-4.5",
        "anthropic/claude-3-haiku",
        "openai/gpt-5.6-sol",
        "openai/gpt-5.6-luna",
        "openai/gpt-5.6-terra",
        "openai/gpt-6-luna",
        "openai/gpt-6-sol",
        "openai/gpt-6-astra",
        "openai/gpt-5.4-mini",
        "openai/gpt-5.4-nano",
        "openai/gpt-5-mini",
        "google/gemini-3.6-flash",
        "google/gemini-3.1-pro-preview",
        "moonshotai/kimi-k3",
        "deepseek/deepseek-v4-pro",
        "deepseek/deepseek-v4-flash",
        "minimax/minimax-m3",
        "anthropic/claude-fable-5",
        "anthropic/claude-fable-5.1",
        "google/gemini-3.8-flash",
        "google/gemini-3.7-flash",
        "google/gemini-3.5-flash",
        "google/gemini-3.5-flash-lite",
        "google/gemini-3.1-flash-lite",
        "google/gemma-4-31b-it",
        "google/gemma-4-26b-a4b-it",
        "moonshotai/kimi-k2.5",
        "xai/grok-4.1-fast-reasoning",
        "xai/grok-4.1-fast-non-reasoning",
        "mistral/ministral-14b",
        "nvidia/nemotron-3-super-120b-a12b",
    ]
    assert [route.id for route in registry.candidates("fireworks")] == [
        "fireworks:accounts/fireworks/models/glm-5p2",
        "fireworks:accounts/fireworks/models/qwen3p7-plus",
    ]
    assert [route.id for route in registry.candidates("openai-direct")] == [
        "openai:gpt-5.6",
        "openai:gpt-5.6-terra",
        "openai:gpt-5.6-luna",
        "openai:gpt-5.4",
        "openai:gpt-5.4-mini",
        "openai:gpt-5.4-nano",
    ]
    assert [route.id for route in registry.candidates("anthropic-direct")] == [
        "anthropic:claude-opus-5",
        "anthropic:claude-opus-4-8",
        "anthropic:claude-sonnet-5",
        "anthropic:claude-haiku-4-5",
    ]
    assert [route.id for route in registry.candidates("bedrock")] == [
        "anthropic/claude-sonnet-5",
        "openai/gpt-5.6-sol",
        "openai/gpt-5.6-luna",
        "openai/gpt-5.6-terra",
        "google/gemma-3-27b-it",
        "moonshotai/kimi-k2.5",
        "deepseek/deepseek-v3.2",
        "deepseek/deepseek-v3.1",
    ]
    assert len(registry.routes_for_execution_profile("default")) == 46
    assert len(registry.routes_for_execution_profile("bedrock")) == 8


def test_candidate_ids_may_repeat_across_execution_profiles():
    value = document()
    value["models"][0]["routes"].append(
        {
            "key": "bedrock:openai/gpt-5.6-sol",
            "id": "openai/gpt-5.6-sol",
            "provider": "bedrock",
            "model_id": "us.openai.gpt-5.6-sol",
            "pricing_channel": "aws-bedrock",
            "execution_profiles": ["bedrock"],
        }
    )
    value["offer_groups"].append(
        {
            "id": "bedrock",
            "credential": None,
            "routes": ["bedrock:openai/gpt-5.6-sol"],
        }
    )
    value["execution_profiles"].append(
        {
            "id": "bedrock",
            "routes": ["bedrock:openai/gpt-5.6-sol"],
        }
    )
    registry = parse_model_registry(value)
    assert registry.candidates("gateway")[0].id == "openai/gpt-5.6-sol"
    assert registry.candidates("bedrock")[0].id == "openai/gpt-5.6-sol"
    assert registry.candidates("bedrock")[0].provider == "bedrock"


def test_bundled_registry_routes_resolve_in_pricing_catalog():
    validate_model_registry(load_model_registry(), load_catalog())


def test_validation_rejects_canonical_drift():
    value = document()
    value["models"][0]["canonical_id"] = "other/model"
    value["models"][0]["publisher"] = "other"
    with pytest.raises(ModelRegistryError, match="canonical model"):
        validate_model_registry(parse_model_registry(value), load_catalog())


def test_validation_rejects_pricing_channel_drift():
    value = document()
    value["models"][0]["routes"][1]["pricing_channel"] = "aws-bedrock"
    with pytest.raises(ModelRegistryError, match="pricing channel"):
        validate_model_registry(parse_model_registry(value), load_catalog())


def test_registry_api_is_public():
    from metergraph_core import (
        ModelDefinition,
        ModelRegistry,
        ModelRegistryError,
        ModelRoute,
        OfferGroup,
        load_model_registry,
        parse_model_registry,
        validate_model_registry,
    )

    registry = load_model_registry()
    assert registry.route("default:openai/gpt-6-luna").display_name == "GPT-6 Luna"
