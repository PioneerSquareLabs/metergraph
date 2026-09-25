"""Canonical model identity and managed execution-route registry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping

import yaml

from .loader import LoadedCatalog


EXECUTION_PROFILES = frozenset({"default", "bedrock"})
OFFER_GROUP_PROFILES = MappingProxyType(
    {
        "gateway": "default",
        "fireworks": "default",
        "openai-direct": "default",
        "anthropic-direct": "default",
        "bedrock": "bedrock",
    }
)
DEFAULT_MODEL_REGISTRY_PATH = Path(__file__).parent / "data" / "models.yaml"


class ModelRegistryError(ValueError):
    """Raised when a model-registry document violates its contract."""


@dataclass(frozen=True, slots=True)
class ModelRoute:
    key: str
    id: str
    canonical_id: str
    display_name: str
    provider: str
    model_id: str
    pricing_channel: str
    execution_profiles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ModelDefinition:
    canonical_id: str
    display_name: str
    publisher: str
    routes: tuple[ModelRoute, ...]


@dataclass(frozen=True, slots=True)
class OfferGroup:
    id: str
    credential: str | None
    route_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ModelRegistry:
    version: str
    models: Mapping[str, ModelDefinition]
    routes: Mapping[str, ModelRoute]
    offer_groups: Mapping[str, OfferGroup]

    def model(self, canonical_id: str) -> ModelDefinition:
        return self.models[canonical_id]

    def route(self, route_key: str) -> ModelRoute:
        return self.routes[route_key]

    def routes_for_execution_profile(self, profile: str) -> tuple[ModelRoute, ...]:
        if profile not in EXECUTION_PROFILES:
            raise KeyError(profile)
        return tuple(
            route
            for route in self.routes.values()
            if profile in route.execution_profiles
        )

    def candidates(self, offer_group: str) -> tuple[ModelRoute, ...]:
        group = self.offer_groups[offer_group]
        return tuple(self.routes[route_id] for route_id in group.route_ids)

    def reachable_candidates(
        self, credentials: Iterable[str]
    ) -> tuple[ModelRoute, ...]:
        present = frozenset(credentials)
        seen: set[str] = set()
        result: list[ModelRoute] = []
        for group in self.offer_groups.values():
            if group.credential is None or group.credential not in present:
                continue
            for route_id in group.route_ids:
                if route_id not in seen:
                    result.append(self.routes[route_id])
                    seen.add(route_id)
        return tuple(result)


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ModelRegistryError(f"{field} must be a mapping")
    return value


def _list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ModelRegistryError(f"{field} must be a list")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ModelRegistryError(f"{field} must be a nonblank string")
    return value.strip()


def _unique_text_list(value: Any, field: str) -> tuple[str, ...]:
    items = tuple(_text(item, field) for item in _list(value, field))
    if not items:
        raise ModelRegistryError(f"{field} must not be empty")
    if len(set(items)) != len(items):
        raise ModelRegistryError(f"{field} contains a duplicate value")
    return items


def parse_model_registry(document: Any) -> ModelRegistry:
    """Parse and freeze one versioned model-registry document."""
    root = _mapping(document, "models document")
    version = _text(root.get("version"), "models document version")
    try:
        date.fromisoformat(version)
    except ValueError as exc:
        raise ModelRegistryError("models document version must be an ISO date") from exc

    models: dict[str, ModelDefinition] = {}
    routes: dict[str, ModelRoute] = {}
    candidate_profiles: set[tuple[str, str]] = set()
    for model_value in _list(root.get("models"), "models document models"):
        model = _mapping(model_value, "model entry")
        canonical_id = _text(model.get("canonical_id"), "model entry canonical_id")
        if canonical_id in models:
            raise ModelRegistryError(f"duplicate canonical model {canonical_id!r}")
        display_name = _text(
            model.get("display_name"), f"{canonical_id}: display_name"
        )
        publisher = _text(model.get("publisher"), f"{canonical_id}: publisher")
        model_routes: list[ModelRoute] = []
        route_values = _list(model.get("routes"), f"{canonical_id}: routes")
        if not route_values:
            raise ModelRegistryError(f"{canonical_id}: routes must not be empty")
        for route_value in route_values:
            route = _mapping(route_value, f"{canonical_id}: route")
            route_key = _text(route.get("key"), f"{canonical_id}: route key")
            route_id = _text(route.get("id"), f"{canonical_id}: route id")
            if route_key in routes:
                raise ModelRegistryError(f"duplicate route id/key {route_key!r}")
            profiles = _unique_text_list(
                route.get("execution_profiles"),
                f"{route_id}: execution_profiles",
            )
            unknown_profiles = set(profiles) - EXECUTION_PROFILES
            if unknown_profiles:
                unknown = sorted(unknown_profiles)[0]
                raise ModelRegistryError(
                    f"{route_id}: unknown execution profile {unknown!r}"
                )
            for profile in profiles:
                candidate_profile = (profile, route_id)
                if candidate_profile in candidate_profiles:
                    raise ModelRegistryError(
                        f"duplicate candidate id {route_id!r} in execution "
                        f"profile {profile!r}"
                    )
                candidate_profiles.add(candidate_profile)
            parsed = ModelRoute(
                key=route_key,
                id=route_id,
                canonical_id=canonical_id,
                display_name=_text(
                    route.get("display_name", display_name),
                    f"{route_id}: display_name",
                ),
                provider=_text(route.get("provider"), f"{route_id}: needs provider"),
                model_id=_text(route.get("model_id"), f"{route_id}: needs model_id"),
                pricing_channel=_text(
                    route.get("pricing_channel"),
                    f"{route_id}: needs pricing_channel",
                ),
                execution_profiles=profiles,
            )
            routes[route_key] = parsed
            model_routes.append(parsed)
        models[canonical_id] = ModelDefinition(
            canonical_id=canonical_id,
            display_name=display_name,
            publisher=publisher,
            routes=tuple(model_routes),
        )

    offer_groups: dict[str, OfferGroup] = {}
    for group_value in _list(
        root.get("offer_groups"), "models document offer_groups"
    ):
        group = _mapping(group_value, "offer group")
        group_id = _text(group.get("id"), "offer group id")
        if group_id not in OFFER_GROUP_PROFILES:
            raise ModelRegistryError(f"unknown offer group {group_id!r}")
        if group_id in offer_groups:
            raise ModelRegistryError(f"duplicate offer group {group_id!r}")
        credential_value = group.get("credential")
        credential = (
            None
            if credential_value is None
            else _text(credential_value, f"{group_id}: credential")
        )
        route_ids = _unique_text_list(
            group.get("routes"), f"{group_id}: routes"
        )
        expected_profile = OFFER_GROUP_PROFILES[group_id]
        for route_id in route_ids:
            if route_id not in routes:
                raise ModelRegistryError(
                    f"{group_id}: offer group references unknown route {route_id!r}"
                )
            if expected_profile not in routes[route_id].execution_profiles:
                raise ModelRegistryError(
                    f"{group_id}: route {route_id!r} is not available to "
                    f"execution profile {expected_profile!r}"
                )
        offer_groups[group_id] = OfferGroup(
            id=group_id,
            credential=credential,
            route_ids=route_ids,
        )

    return ModelRegistry(
        version=version,
        models=MappingProxyType(models),
        routes=MappingProxyType(routes),
        offer_groups=MappingProxyType(offer_groups),
    )


def load_model_registry(path: str | Path | None = None) -> ModelRegistry:
    """Load the bundled model registry or an explicit replacement path."""
    resolved = Path(path) if path is not None else DEFAULT_MODEL_REGISTRY_PATH
    return parse_model_registry(yaml.safe_load(resolved.read_bytes()))


def validate_model_registry(
    registry: ModelRegistry, catalog: LoadedCatalog
) -> None:
    """Require every execution route to resolve in the pricing catalog."""
    validation_time = datetime.combine(
        date.fromisoformat(registry.version), time.min, tzinfo=timezone.utc
    )
    catalog_models = {
        entry.get("canonical_id"): entry
        for entry in catalog.document.get("models", [])
    }
    for route in registry.routes.values():
        canonical = catalog.canonical_model_id(route.provider, route.model_id)
        if canonical == route.model_id:
            resolved = catalog.snapshot.resolve_price(
                model=route.model_id,
                channel=route.pricing_channel,
                at=validation_time,
            )
            if resolved is not None:
                canonical = resolved.canonical_model
        if canonical != route.canonical_id:
            raise ModelRegistryError(
                f"{route.id}: pricing resolves canonical model {canonical!r}, "
                f"expected {route.canonical_id!r}"
            )
        catalog_model = catalog_models.get(route.canonical_id)
        active_channel = any(
            price.get("channel") == route.pricing_channel
            and date.fromisoformat(str(price.get("effective_from")))
            <= validation_time.date()
            and (
                price.get("effective_to") is None
                or validation_time.date()
                < date.fromisoformat(str(price["effective_to"]))
            )
            for price in (catalog_model or {}).get("prices", [])
        )
        if not active_channel:
            raise ModelRegistryError(
                f"{route.id}: no price for pricing channel "
                f"{route.pricing_channel!r}"
            )
