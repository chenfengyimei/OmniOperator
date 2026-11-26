"""OmniWeave rollout modes and the canonical owner Web trigger contract."""

from __future__ import annotations

from typing import Literal

from .contracts import BusinessError

OmniWeaveMode = Literal["off", "observe"]
OMNIWEAVE_MODES: tuple[OmniWeaveMode, ...] = ("off", "observe")
OWNER_WEB_SOURCE = "owner_web"
TRIGGER_VERSION = 1
OBSERVE_DECISION = "observe"


def checked_mode(value: str) -> OmniWeaveMode:
    if value == "off" or value == "observe":
        return value
    raise BusinessError("omniweave_mode_invalid", "OmniWeave mode must be off or observe", status=422)
