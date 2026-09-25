from types import MappingProxyType

import pytest

from metergraph_core.models import ModelRegistryError, parse_model_registry


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
                        "id": "openai/gpt-5.6-sol",
                        "provider": "vercel",
                        "model_id": "openai/gpt-5.6-sol",
                        "pricing_channel": "vercel-ai-gateway",
                        "execution_profiles": ["default"],
                    },
                    {
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
        "offer_groups": [
            {
                "id": "gateway",
                "credential": "AI_GATEWAY_API_KEY",
                "routes": ["openai/gpt-5.6-sol"],
            },
            {
                "id": "openai-direct",
                "credential": "OPENAI_API_KEY",
                "routes": ["openai:gpt-5.6"],
            },
        ],
    }


def test_parse_model_registry_keeps_routes_immutable_and_distinct():
    registry = parse_model_registry(document())
    gateway, direct = registry.routes_for_execution_profile("default")
    assert gateway.canonical_id == direct.canonical_id == "openai/gpt-5.6-sol"
    assert gateway.display_name == "GPT-5.6 Sol"
    assert direct.display_name == "GPT-5.6 Sol (direct)"
    assert registry.model("openai/gpt-5.6-sol").publisher == "openai"
    assert isinstance(registry.models, MappingProxyType)
    with pytest.raises(TypeError):
        registry.routes["another/model"] = gateway


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


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("duplicate_model", "duplicate canonical model"),
        ("duplicate_route", "duplicate route id"),
        ("duplicate_offer_group", "duplicate offer group"),
        ("unknown_offer_group", "unknown offer group"),
        ("unknown_offer_route", "unknown route"),
        ("incompatible_offer_route", "not available to execution profile"),
        ("unknown_execution_profile", "unknown execution profile"),
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
                "routes": [{**value["models"][0]["routes"][0]}],
            }
        )
    elif mutation == "duplicate_offer_group":
        value["offer_groups"].append({**value["offer_groups"][0]})
    elif mutation == "unknown_offer_group":
        value["offer_groups"][0]["id"] = "another"
    elif mutation == "unknown_offer_route":
        value["offer_groups"][0]["routes"] = ["missing/model"]
    elif mutation == "incompatible_offer_route":
        value["models"][0]["routes"][0]["execution_profiles"] = ["bedrock"]
    elif mutation == "unknown_execution_profile":
        value["models"][0]["routes"][0]["execution_profiles"] = ["another"]
    else:
        value["models"][0]["routes"][0]["provider"] = "   "
    with pytest.raises(ModelRegistryError, match=message):
        parse_model_registry(value)


@pytest.mark.parametrize(
    "mutation",
    ["missing_version", "models_not_list", "routes_not_list", "offer_groups_not_list"],
)
def test_registry_document_shape_is_required(mutation):
    value = document()
    if mutation == "missing_version":
        value.pop("version")
    elif mutation == "models_not_list":
        value["models"] = {}
    elif mutation == "routes_not_list":
        value["models"][0]["routes"] = {}
    else:
        value["offer_groups"] = {}
    with pytest.raises(ModelRegistryError):
        parse_model_registry(value)
