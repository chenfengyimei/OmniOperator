"""Independent ephemeral remote-control consent protocol; system device selection never authorizes a business input action."""

from __future__ import annotations

import re
from typing import cast

from .contracts import BusinessError
from .desktop_portal import binding

VERSION = "omni-desktop-control-1"
PURPOSE = "interactive-input"
MAX_DEVICES = 3
PHASES = frozenset(
    {
        "starting",
        "prepared",
        "creating",
        "selecting",
        "authorizing",
        "system_authorized",
        "closing",
        "closed",
        "denied",
        "failed",
    }
)
TERMINAL = frozenset({"closed", "denied", "failed"})
TRANSITIONS: dict[str, set[str]] = {
    "starting": {"prepared", "closing", "failed"},
    "prepared": {"creating", "closing", "failed"},
    "creating": {"selecting", "closing", "denied", "failed"},
    "selecting": {"authorizing", "closing", "denied", "failed"},
    "authorizing": {"system_authorized", "closing", "denied", "failed"},
    "system_authorized": {"closing", "failed"},
    "closing": {"closed", "denied", "failed"},
    "closed": set(),
    "denied": set(),
    "failed": set(),
}


def invalid() -> BusinessError:
    return BusinessError(
        "portal_control_invalid", "Use explicit, separately bound ephemeral control consent", status=422
    )


def devices(value: object) -> int:
    if type(value) is not int or not 1 <= value <= MAX_DEVICES:
        raise invalid()
    return value


def command(value: object, expected: dict[str, str] | None = None) -> dict[str, object]:
    if not isinstance(value, dict):
        raise invalid()
    raw = cast(dict[str, object], value)
    opening = raw.get("command") == "open"
    if set(raw) != {"version", "binding", "command"} | ({"purpose", "devices", "consent"} if opening else set()):
        raise invalid()
    if (
        raw["version"] != VERSION
        or not isinstance(raw["command"], str)
        or raw["command"] not in {"open", "permit", "close"}
    ):
        raise invalid()
    scope = binding(raw["binding"])
    if expected is not None and scope != binding(expected):
        raise invalid()
    result: dict[str, object] = {"version": VERSION, "binding": scope, "command": raw["command"]}
    if opening:
        if raw["purpose"] != PURPOSE or raw["consent"] is not True:
            raise invalid()
        result.update(purpose=PURPOSE, devices=devices(raw["devices"]), consent=True)
    return result


def event(value: object, expected: dict[str, str], previous: str | None = None) -> dict[str, object]:
    if not isinstance(value, dict):
        raise invalid()
    raw = cast(dict[str, object], value)
    if set(raw) != {
        "version",
        "binding",
        "kind",
        "phase",
        "devices",
        "granted_devices",
        "input_authorized",
        "clean",
        "error_code",
    }:
        raise invalid()
    phase, kind, code = raw["phase"], raw["kind"], raw["error_code"]
    if (
        raw["version"] != VERSION
        or binding(raw["binding"]) != binding(expected)
        or not isinstance(phase, str)
        or phase not in PHASES
        or not isinstance(kind, str)
        or kind not in {"state", "heartbeat"}
        or raw["input_authorized"] is not False
        or type(raw["clean"]) is not bool
    ):
        raise invalid()
    requested = devices(raw["devices"])
    granted = raw["granted_devices"]
    if type(granted) is not int or granted != (requested if phase == "system_authorized" else 0):
        raise invalid()
    if (
        raw["clean"]
        and phase not in TERMINAL
        or phase == "closed"
        and (not raw["clean"] or code is not None)
        or phase in {"failed", "denied"}
        and code is None
        or phase not in TERMINAL
        and code is not None
        or code is not None
        and (not isinstance(code, str) or re.fullmatch(r"(?:portal|desktop)_[a-z0-9_]{1,100}", code) is None)
    ):
        raise invalid()
    if previous is not None:
        if previous not in TRANSITIONS:
            raise invalid()
        if kind == "heartbeat" and phase != previous or kind == "state" and phase not in TRANSITIONS[previous]:
            raise invalid()
    return dict(raw)
