"""Install trusted features and expose their services to the tools and HTTP host."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Generic, TypeVar

from ...adapters.diagnostics import JsonLogSink
from ...adapters.http_routes import FeatureRoutes
from ...adapters.platform.runtime.paths import InstancePaths
from ...application.android import AndroidPort
from ...application.background_review import ReadDispatch
from ...application.capabilities import Capability, CapabilityPlugin, RunCapabilityProvider
from ...application.images import ImageService
from ...application.mcp import MCPTransportPort
from ...application.media import MediaProcessingPort
from ...application.model_calls import ModelCalls
from ...application.objects import ObjectService
from ...application.ports import (
    AsyncSecretPort,
    Clock,
    DatabasePort,
    Ids,
    LocalVectorizer,
    MetricsPort,
    Observer,
    Record,
    TokenCounter,
)
from ...domain.contracts import ToolSpec
from ..channel_providers import ChannelProviders
from ..lifecycle import Lifetime
from ..platform_providers import PlatformPorts

Service = TypeVar("Service")
Context = TypeVar("Context")
FeatureStart = Callable[[], Awaitable[None] | None]


@dataclass(frozen=True)
class FeatureContext:
    lifetime: Lifetime
    database: DatabasePort
    paths: InstancePaths
    boot_id: str
    objects: ObjectService
    images: ImageService
    services: Mapping[str, object]
    vectors: LocalVectorizer | None
    clock: Clock
    ids: Ids
    observer: Observer
    token_counter: TokenCounter
    secrets: AsyncSecretPort
    channels: ChannelProviders
    platform: PlatformPorts
    skill_platform: str
    redact_secret: Callable[[str], None]
    accepting: Callable[[], bool]
    default_tools: bool
    android_backend: AndroidPort | None = None
    media_processor: MediaProcessingPort | None = None
    mcp_transport: MCPTransportPort | None = None

    @property
    def home(self) -> Path:
        return self.paths.home

    @property
    def control(self) -> Path:
        return self.paths.control


@dataclass(frozen=True)
class KernelFeatureContext:
    base: FeatureContext
    notify_run: Callable[[], None]
    cancel_run: Callable[[str, str], Awaitable[None]]
    review_blockers: Callable[[Record], list[str]]
    runtime_health: Callable[[], Record]
    metrics: MetricsPort
    sink: JsonLogSink


@dataclass(frozen=True)
class WorkflowFeatureContext:
    base: FeatureContext
    model_calls: ModelCalls
    read_tools: tuple[ToolSpec, ...]
    read_dispatch: ReadDispatch


@dataclass(frozen=True)
class FeatureBinding:
    service: object
    tools: tuple[Capability, ...] = ()
    owner_tools: tuple[Capability, ...] = ()
    start: FeatureStart | None = None
    run_provider: RunCapabilityProvider | None = None


@dataclass(frozen=True)
class FeaturePlugin(FeatureRoutes, Generic[Context]):
    install: Callable[[Context], Awaitable[FeatureBinding]]


@dataclass(frozen=True)
class InstalledFeatures:
    services: Mapping[str, object]
    capabilities: tuple[CapabilityPlugin, ...]
    starters: tuple[FeatureStart, ...]
    providers: tuple[RunCapabilityProvider, ...]

    def require(self, feature_id: str, service_type: type[Service]) -> Service:
        return require_service(self.services, feature_id, service_type)

    async def start(self) -> None:
        for start in self.starters:
            waiting = start()
            if waiting is not None:
                await waiting


def require_service(services: Mapping[str, object], feature_id: str, service_type: type[Service]) -> Service:
    service = services.get(feature_id)
    if not isinstance(service, service_type):
        raise ValueError(f"Required feature service is unavailable: {feature_id}")
    return service


async def install_features(
    plugins: tuple[FeaturePlugin[Context], ...], context: Context, services: dict[str, object]
) -> InstalledFeatures:
    capabilities: list[CapabilityPlugin] = []
    starters: list[FeatureStart] = []
    providers: list[RunCapabilityProvider] = []
    for plugin in plugins:
        binding = await plugin.install(context)
        services[plugin.id] = binding.service
        if binding.tools or binding.owner_tools:
            capabilities.append(CapabilityPlugin(plugin.id, binding.tools, binding.owner_tools, own_adapters=False))
        if binding.start is not None:
            starters.append(binding.start)
        if binding.run_provider is not None:
            providers.append(binding.run_provider)
    return InstalledFeatures(MappingProxyType(services), tuple(capabilities), tuple(starters), tuple(providers))
