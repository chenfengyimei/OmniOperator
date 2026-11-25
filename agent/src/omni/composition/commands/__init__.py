"""Typed registration for trusted V2 command-line capabilities."""

from __future__ import annotations

from argparse import ArgumentParser, Namespace
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from ...adapters.platform.runtime.paths import InstancePaths


@dataclass(frozen=True)
class CommandPlugin:
    name: str
    run: Callable[[Namespace, InstancePaths], int]
    help: str | None = field(default=None, kw_only=True)
    configure: Callable[[ArgumentParser], None] | None = field(default=None, kw_only=True)


def register_commands(*plugins: CommandPlugin) -> Mapping[str, CommandPlugin]:
    commands: dict[str, CommandPlugin] = {}
    for plugin in plugins:
        if plugin.name in commands:
            raise ValueError(f"Duplicate command: {plugin.name}")
        commands[plugin.name] = plugin
    return MappingProxyType(commands)
