"""Install trusted features and expose their services to the tools and HTTP host."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Generic, TypeVar

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
Context = TypeVar("Context")


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
class KernelFeatureContext:
    lifetime: Lifetime
    database: DatabasePort
    clock: Clock
    channels: ChannelProviders
    notify_run: Callable[[], None]
    cancel_run: Callable[[str, str], Awaitable[None]]
    skill_platform: str
    redact: Callable[[str], str]
    default_tools: bool


@dataclass(frozen=True)
class FeatureBinding:
    service: object
    tools: tuple[Capability, ...] = ()
    owner_tools: tuple[Capability, ...] = ()
    start: Callable[[], None] | None = None


@dataclass(frozen=True)
class FeaturePlugin(FeatureRoutes, Generic[Context]):
    install: Callable[[Context], Awaitable[FeatureBinding]]


@dataclass(frozen=True)
class InstalledFeatures:
    services: Mapping[str, object]
    capabilities: tuple[CapabilityPlugin, ...]
    starters: tuple[Callable[[], None], ...]

    def require(self, feature_id: str, service_type: type[Service]) -> Service:
        service = self.services.get(feature_id)
        if not isinstance(service, service_type):
            raise ValueError(f"Required feature service is unavailable: {feature_id}")
        return service

    def start(self) -> None:
        for start in self.starters:
            start()


async def install_features(plugins: tuple[FeaturePlugin[Context], ...], context: Context) -> InstalledFeatures:
    services: dict[str, object] = {}
    capabilities: list[CapabilityPlugin] = []
    starters: list[Callable[[], None]] = []
    for plugin in plugins:
        binding = await plugin.install(context)
        services[plugin.id] = binding.service
        if binding.tools or binding.owner_tools:
            capabilities.append(CapabilityPlugin(plugin.id, binding.tools, binding.owner_tools, own_adapters=False))
        if binding.start is not None:
            starters.append(binding.start)
    return InstalledFeatures(MappingProxyType(services), tuple(capabilities), tuple(starters))
