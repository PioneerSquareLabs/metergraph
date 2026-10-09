from datetime import date, datetime, timezone
from decimal import Decimal
from types import MappingProxyType

import pytest

from metergraph_core import load_catalog
from metergraph_core.models import (
    ModelRegistryError,
    ReleaseDate,
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
    assert registry.version == "2026-10-09"
    assert [route.id for route in registry.candidates("gateway")] == [
        "anthropic/claude-sonnet-5",
        "anthropic/claude-opus-5",
        "anthropic/claude-opus-4.8",
        "anthropic/claude-haiku-4.5",
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
        "zai/glm-5.3-flash",
        "deepseek/deepseek-v4.1-flash",
        "alibaba/qwen3.8-flash",
        "inception/mercury-2.5",
        "anthropic/claude-opus-5.5",
        "anthropic/claude-sonnet-5.5",
        "openai/gpt-6.1-sol",
        "xai/grok-4.7",
        "moonshotai/kimi-k2.7-code",
        "zai/glm-5.3",
        "anthropic/claude-sonnet-4.6",
        "xai/grok-4.3",
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
    assert len(registry.routes_for_execution_profile("default")) == 57
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


def test_released_is_optional_and_must_be_a_calendar_date():
    value = document()
    assert parse_model_registry(value).model("openai/gpt-5.6-sol").released is None
    value["models"][0]["released"] = "2026-08-26"
    assert parse_model_registry(value).model("openai/gpt-5.6-sol").released == date(2026, 8, 26)
    value["models"][0]["released"] = "August 2026"
    with pytest.raises(ModelRegistryError, match="released must be an ISO date"):
        parse_model_registry(value)


def test_release_date_prefers_the_recorded_date_and_names_its_basis():
    value = document()
    value["models"][0]["released"] = "2026-08-26"
    registry = parse_model_registry(value)
    assert registry.release_date("openai/gpt-5.6-sol", load_catalog()) == ReleaseDate(
        date(2026, 8, 26), "released"
    )


def test_release_date_falls_back_to_the_earliest_price_window():
    """Without a recorded date the catalog's first price stands in, and says
    so, so a reader can tell a launch date from a pricing date."""
    registry = parse_model_registry(document())
    catalog = load_catalog()
    entry = next(
        entry for entry in catalog.document["models"]
        if entry["canonical_id"] == "openai/gpt-5.6-sol"
    )
    earliest = min(date.fromisoformat(str(price["effective_from"])) for price in entry["prices"])

    assert registry.release_date("openai/gpt-5.6-sol", catalog) == ReleaseDate(earliest, "first_price")
    assert registry.release_date("example/not-a-model", catalog) is None


def test_release_date_fallback_reads_timestamp_price_windows():
    """A price window may start at a timestamp, which the catalog accepts;
    the earliest window still wins, whichever form it was written in."""
    registry = parse_model_registry(document())
    catalog = load_catalog()
    entry = next(
        entry for entry in catalog.document["models"]
        if entry["canonical_id"] == "openai/gpt-5.6-sol"
    )
    entry["prices"] = [
        {"effective_from": "2026-09-10T04:00:00+00:00"},
        {"effective_from": "2026-04-24"},
        {"effective_from": "2026-07-01T00:00:00"},
    ]
    assert registry.release_date("openai/gpt-5.6-sol", catalog) == ReleaseDate(
        date(2026, 4, 24), "first_price"
    )


def test_every_bundled_model_records_a_release_date():
    """The bundled registry answers from a recorded date, never the price
    fallback, and no model is dated after its registry version."""
    registry = load_model_registry()
    catalog = load_catalog()
    version_day = date.fromisoformat(registry.version.split(".", 1)[0])
    for canonical_id, model in registry.models.items():
        assert model.released is not None, canonical_id
        assert model.released <= version_day, canonical_id
        assert registry.release_date(canonical_id, catalog).basis == "released"


def test_bundled_registry_routes_resolve_in_pricing_catalog():
    validate_model_registry(load_model_registry(), load_catalog())


def test_glm_5_3_flash_is_a_priced_gateway_candidate():
    registry = load_model_registry()
    route = registry.route("default:zai/glm-5.3-flash")
    assert route in registry.candidates("gateway")
    assert route in registry.reachable_candidates({"AI_GATEWAY_API_KEY"})
    assert (route.canonical_id, route.display_name) == (
        "zai/glm-5.3-flash",
        "GLM 5.3 Flash",
    )
    assert (route.provider, route.model_id, route.pricing_channel) == (
        "vercel",
        "zai/glm-5.3-flash",
        "vercel-ai-gateway",
    )

    resolved = load_catalog().snapshot.resolve_price(
        model=route.model_id,
        channel=route.pricing_channel,
        at=datetime(2026, 9, 26, tzinfo=timezone.utc),
    )
    assert resolved is not None
    assert resolved.canonical_model == "zai/glm-5.3-flash"
    assert resolved.price.input_per_mtok == Decimal("0.15")
    assert resolved.price.output_per_mtok == Decimal("0.50")


@pytest.mark.parametrize(
    ("model", "display_name", "input_rate", "output_rate"),
    [
        ("deepseek/deepseek-v4.1-flash", "DeepSeek V4.1 Flash", "0.30", "1.20"),
        ("alibaba/qwen3.8-flash", "Qwen 3.8 Flash", "0.15", "0.47"),
        ("inception/mercury-2.5", "Mercury 2.5", "0.04", "0.15"),
    ],
)
def test_low_cost_gateway_models_are_priced_candidates(
    model, display_name, input_rate, output_rate,
):
    registry = load_model_registry()
    route = registry.route(f"default:{model}")
    assert route in registry.candidates("gateway")
    assert route in registry.reachable_candidates({"AI_GATEWAY_API_KEY"})
    assert (route.canonical_id, route.display_name) == (model, display_name)
    assert (route.provider, route.model_id, route.pricing_channel) == (
        "vercel",
        model,
        "vercel-ai-gateway",
    )

    resolved = load_catalog().snapshot.resolve_price(
        model=route.model_id,
        channel=route.pricing_channel,
        at=datetime(2026, 9, 29, tzinfo=timezone.utc),
    )
    assert resolved is not None
    assert resolved.canonical_model == model
    assert resolved.price.input_per_mtok == Decimal(input_rate)
    assert resolved.price.output_per_mtok == Decimal(output_rate)


def test_a_retired_model_is_priced_but_never_offered_or_routed():
    # Anthropic retired Claude 3 Haiku on 2026-04-20: a request to it fails, so
    # it cannot be a candidate. Its price stays, for the traffic captured
    # while it was served.
    registry = load_model_registry()
    offered = {
        route.id
        for group in registry.offer_groups
        for route in registry.candidates(group)
    }
    assert "anthropic/claude-3-haiku" not in offered
    for profile in ("default", "bedrock"):
        assert "anthropic/claude-3-haiku" not in {
            route.canonical_id for route in registry.routes_for_execution_profile(profile)
        }
    assert "default:anthropic/claude-3-haiku" not in registry.routes

    resolved = load_catalog().snapshot.resolve_price(
        model="claude-3-haiku",
        channel="anthropic-api",
        at=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )
    assert resolved is not None
    assert resolved.canonical_model == "anthropic/claude-3-haiku"


@pytest.mark.parametrize(
    ("model", "display_name", "input_rate", "output_rate"),
    [
        ("anthropic/claude-opus-5.5", "Claude Opus 5.5", "4.00", "20.00"),
        ("anthropic/claude-sonnet-5.5", "Claude Sonnet 5.5", "2.00", "10.00"),
        ("openai/gpt-6.1-sol", "GPT-6.1 Sol", "2.00", "10.00"),
        ("xai/grok-4.7", "Grok 4.7", "2.00", "6.00"),
        ("moonshotai/kimi-k2.7-code", "Kimi K2.7 Code", "0.95", "4.00"),
        ("zai/glm-5.3", "GLM 5.3", "1.40", "4.40"),
    ],
)
def test_new_benchmark_candidates_are_priced_gateway_candidates(model, display_name, input_rate, output_rate):
    registry = load_model_registry()
    route = registry.route(f"default:{model}")
    assert route in registry.candidates("gateway")
    assert (route.canonical_id, route.display_name) == (model, display_name)
    resolved = load_catalog().snapshot.resolve_price(
        model=route.model_id, channel=route.pricing_channel,
        at=datetime(2026, 10, 7, tzinfo=timezone.utc),
    )
    assert resolved is not None and resolved.canonical_model == model
    assert resolved.price.input_per_mtok == Decimal(input_rate)
    assert resolved.price.output_per_mtok == Decimal(output_rate)


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
        ReleaseDate,
        load_model_registry,
        parse_model_registry,
        validate_model_registry,
    )

    registry = load_model_registry()
    assert registry.release_date("openai/gpt-6-luna", load_catalog()) == ReleaseDate(date(2026, 9, 22), "released")
    assert registry.route("default:openai/gpt-6-luna").display_name == "GPT-6 Luna"
