"""Register capability providers and capture a deterministic per-run tool surface."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ..domain.contracts import BusinessError, ToolSpec
from .ports import CapabilityPort, Record, RunCapabilityPort


@dataclass(frozen=True)
class Capability:
    spec: ToolSpec
    adapter: CapabilityPort
    quiescent: Callable[[], bool] | None = None


@dataclass(frozen=True)
class CapabilityPlugin:
    """A trusted contributor assembled with the instance."""

    id: str
    tools: tuple[Capability, ...] = ()
    owner_tools: tuple[Capability, ...] = ()
    own_adapters: bool = True


@dataclass(frozen=True)
class RunCapabilityProvider:
    id: str
    adapter: RunCapabilityPort
    review_quiescent: Callable[[Record], bool] | None = None


class CapabilityRegistry:
    def __init__(
        self,
        reserved: frozenset[str],
        injected: tuple[Capability, ...],
        owner: tuple[Capability, ...],
    ) -> None:
        self._reserved = reserved
        self._tools: dict[str, Capability] = {}
        self._owner_tools: dict[str, Capability] = {}
        self.tools: Mapping[str, Capability] = MappingProxyType(self._tools)
        self.owner_tools: Mapping[str, Capability] = MappingProxyType(self._owner_tools)
        self._ids: set[str] = set()
        self._owned: dict[int, CapabilityPort] = {}
        self._borrowed: set[int] = set()
        self._providers: dict[str, RunCapabilityProvider] = {}
        self._sealed = False
        self._register(CapabilityPlugin("injected", injected, owner), reserved=frozenset())

    def _check_id(self, plugin_id: str) -> None:
        if self._sealed:
            raise ValueError("Capabilities must be attached before start")
        if not plugin_id or plugin_id in self._ids:
            raise ValueError("Capability plugin IDs must be nonempty and unique")

    def register(self, plugin: CapabilityPlugin) -> None:
        self._register(plugin, reserved=self._reserved)

    def _register(self, plugin: CapabilityPlugin, *, reserved: frozenset[str]) -> None:
        self._check_id(plugin.id)
        tools = {cap.spec.name: cap for cap in plugin.tools}
        owner_tools = {cap.spec.name: cap for cap in plugin.owner_tools}
        if len(tools) != len(plugin.tools) or len(owner_tools) != len(plugin.owner_tools):
            raise ValueError("Duplicate capability names")
        names = tools.keys() | owner_tools.keys()
        if tools.keys() & owner_tools.keys() or names & (self._tools.keys() | self._owner_tools.keys()):
            raise ValueError("Duplicate capability names")
        if names & reserved:
            raise ValueError("Capability name is reserved by the engine")
        adapters = {id(cap.adapter): cap.adapter for cap in (*plugin.tools, *plugin.owner_tools)}
        if adapters.keys() & (self._borrowed if plugin.own_adapters else self._owned.keys()):
            raise ValueError("A capability adapter must have one lifecycle owner")

        self._tools.update(tools)
        self._owner_tools.update(owner_tools)
        if plugin.own_adapters:
            self._owned.update(adapters)
        else:
            self._borrowed.update(adapters)
        self._ids.add(plugin.id)

    def register_provider(
        self, plugin_id: str, adapter: RunCapabilityPort, *, review_quiescent: Callable[[Record], bool] | None = None
    ) -> None:
        """Register a provider whose service lifecycle is owned by instance composition."""
        self._check_id(plugin_id)
        if id(adapter) in self._owned:
            raise ValueError("A capability adapter must have one lifecycle owner")
        self._providers[plugin_id] = RunCapabilityProvider(plugin_id, adapter, review_quiescent)
        self._borrowed.add(id(adapter))
        self._ids.add(plugin_id)

    def provider_quiescent(self, plugin_id: str, record: Record) -> bool:
        provider = self._providers.get(plugin_id)
        return provider is not None and provider.review_quiescent is not None and provider.review_quiescent(record)

    def seal(self) -> None:
        self._sealed = True

    async def capture(self, agent_id: str, run_id: str, controls: tuple[Capability, ...]) -> dict[str, Capability]:
        tools = dict(self.tools)
        tools.update((cap.spec.name, cap) for cap in controls)
        for provider in self._providers.values():
            for spec in await provider.adapter.tools_for_run(agent_id, run_id):
                if spec.name in tools or spec.name in self._reserved or spec.name in self.owner_tools:
                    raise BusinessError(
                        f"{provider.id}_tool_collision", "Provider tool conflicts with a registered capability"
                    )
                tools[spec.name] = Capability(spec, provider.adapter)
        return tools

    def owned_adapters(self) -> tuple[CapabilityPort, ...]:
        return tuple(self._owned.values())
