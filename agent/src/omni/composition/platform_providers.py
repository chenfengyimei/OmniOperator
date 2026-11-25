"""Select the host platform's desktop and terminal bindings at startup."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from ..adapters.platform.desktop.backend import DesktopBackend
from ..adapters.platform.desktop.desktop_control import DesktopControlBackend
from ..adapters.platform.desktop.portal import PortalBackend
from ..adapters.platform.runtime.paths import InstancePaths
from ..adapters.platform.runtime.terminal import TerminalBackend
from ..application.ports import DesktopBackendPort, DesktopControlPort, DesktopPortalPort, TerminalBackendPort
from .lifecycle import Lifetime


@dataclass(frozen=True)
class PlatformPorts:
    terminal: TerminalBackendPort
    desktop: DesktopBackendPort
    portal: DesktopPortalPort
    control: DesktopControlPort


@dataclass(frozen=True)
class PlatformProvider:
    skill_platform: str
    macos_transport: Literal["posix", "launchservices"] = "posix"

    def register(
        self,
        lifetime: Lifetime,
        paths: InstancePaths,
        validate_workspace: Callable[[str], None],
        *,
        terminal_backend: TerminalBackendPort | None = None,
        desktop_backend: DesktopBackendPort | None = None,
        portal_backend: DesktopPortalPort | None = None,
        control_backend: DesktopControlPort | None = None,
    ) -> PlatformPorts:
        terminal = terminal_backend if terminal_backend is not None else TerminalBackend(validate_workspace)
        lifetime.own("terminal_io", 1, terminal.close)
        capacity = asyncio.Semaphore(2)
        desktop = (
            desktop_backend
            if desktop_backend is not None
            else DesktopBackend(
                capture_root=paths.control / "desktop-captures",
                capacity=capacity,
                macos_transport=self.macos_transport,
            )
        )
        lifetime.own("desktop_io", 1, desktop.close, desktop.stop_accepting)
        portal = (
            portal_backend
            if portal_backend is not None
            else PortalBackend(capacity=capacity, capture_root=paths.control / "portal-captures")
        )
        lifetime.own("desktop_portal_io", 1, portal.close)
        control = control_backend if control_backend is not None else DesktopControlBackend(capacity=capacity)
        # The service takes this step over because it settles consent after native cleanup.
        lifetime.own("desktop_control", 0, control.close, control.stop_accepting)
        return PlatformPorts(terminal, desktop, portal, control)


_PROVIDERS = {
    "win32": PlatformProvider("windows"),
    "darwin": PlatformProvider("macos", macos_transport="launchservices"),
    "linux": PlatformProvider("linux"),
}
_DEFAULT = PlatformProvider("linux")


def platform_provider(host: str) -> PlatformProvider:
    return _PROVIDERS.get(host, _DEFAULT)
