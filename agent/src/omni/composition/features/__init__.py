"""Install trusted features and expose their services to the tools and HTTP host."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TypeVar

from ...adapters.http_routes import FeatureRoutes
from ...application.android import AndroidPort
from ...application.capabilities import Capability, CapabilityPlugin
from ...application.images import ImageService
from ...application.media import MediaProcessingPort
from ...application.objects import ObjectService
from ...application.persona import PersonaGrowthService
from ...application.ports import AsyncSecretPort, Clock, DatabasePort, Ids
from ..channel_providers import ChannelProviders
from ..lifecycle import Lifetime

Service = TypeVar("Service")


@dataclass(frozen=True)
class FeatureContext:
    lifetime: Lifetime
    database: DatabasePort
    control: Path
    objects: ObjectService
    images: ImageService
    persona: PersonaGrowthService
    clock: Clock
    ids: Ids
    secrets: AsyncSecretPort
    channels: ChannelProviders
    accepting: Callable[[], bool]
    default_tools: bool
    android_backend: AndroidPort | None = None
    media_processor: MediaProcessingPort | None = None


@dataclass(frozen=True)
class FeatureBinding:
    service: object
    tools: tuple[Capability, ...] = ()
    owner_tools: tuple[Capability, ...] = ()


@dataclass(frozen=True)
class FeaturePlugin(FeatureRoutes):
    install: Callable[[FeatureContext], Awaitable[FeatureBinding]]


@dataclass(frozen=True)
class InstalledFeatures:
    services: Mapping[str, object]
    capabilities: tuple[CapabilityPlugin, ...]

    def require(self, feature_id: str, service_type: type[Service]) -> Service:
        service = self.services.get(feature_id)
        if not isinstance(service, service_type):
            raise ValueError(f"Required feature service is unavailable: {feature_id}")
        return service


async def install_features(plugins: tuple[FeaturePlugin, ...], context: FeatureContext) -> InstalledFeatures:
    if len({plugin.id for plugin in plugins}) != len(plugins):
        raise ValueError("Duplicate feature plugin IDs")
    services: dict[str, object] = {}
    capabilities: list[CapabilityPlugin] = []
    for plugin in plugins:
        binding = await plugin.install(context)
        services[plugin.id] = binding.service
        if binding.tools or binding.owner_tools:
            capabilities.append(CapabilityPlugin(plugin.id, binding.tools, binding.owner_tools, own_adapters=False))
    return InstalledFeatures(MappingProxyType(services), tuple(capabilities))
