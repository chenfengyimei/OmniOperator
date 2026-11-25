"""Group the desktop services that share the same owner view."""

from __future__ import annotations

from dataclasses import dataclass

from .desktop import DesktopService
from .desktop_actions import DesktopActions
from .desktop_control import DesktopControlService
from .desktop_portal import DesktopPortalService


@dataclass(frozen=True)
class DesktopServices:
    observations: DesktopService
    portal: DesktopPortalService
    control: DesktopControlService
    actions: DesktopActions
