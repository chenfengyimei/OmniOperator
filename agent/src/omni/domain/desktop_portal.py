"""Ephemeral window-share consent and helper protocol; grants never identify a native PID or authorize input."""

from __future__ import annotations

import json
import re
import uuid

from .contracts import BusinessError

VERSION = "omni-portal-ipc-2"
MAX_FRAME, MAX_TOTAL, MAX_EVENTS = 16384, 262144, 512
CHOICE_SECONDS, LIFETIME_SECONDS = 90, 150
PHASES = frozenset(
    {"starting", "prepared", "creating", "selecting", "authorizing", "active", "closing", "closed", "denied", "failed"}
)
TERMINAL = frozenset({"closed", "denied", "failed"})
BINDING_KEYS = frozenset({"agent_id", "view_id", "boot_id", "grant_id"})
TRANSITIONS = {
    "starting": {"prepared", "failed", "closing"},
    "prepared": {"creating", "closing", "failed"},
    "creating": {"selecting", "closing", "failed", "denied"},
    "selecting": {"authorizing", "closing", "failed", "denied"},
    "authorizing": {"active", "closing", "failed", "denied"},
    "active": {"closing", "failed"},
    "closing": {"closed", "denied", "failed"},
    "closed": set(),
    "denied": set(),
    "failed": set(),
}


def invalid() -> BusinessError:
    return BusinessError("portal_protocol_invalid", "Use the exact scoped window-share protocol", status=422)


def uuid_value(value: object) -> str:
    if not isinstance(value, str) or len(value) != 36:
        raise invalid()
    try:
        if str(uuid.UUID(value)) != value:
            raise invalid()
    except ValueError:
        raise invalid() from None
    return value


def binding(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != BINDING_KEYS:
        raise invalid()
    return {key: uuid_value(value[key]) for key in sorted(BINDING_KEYS)}


def command(value: object, expected: dict | None = None) -> dict:
    capture = isinstance(value, dict) and value.get("command") == "capture"
    if (
        not isinstance(value, dict)
        or set(value)
        != (
            {"version", "binding", "command", "capture_id", "capture_path"}
            if capture
            else {"version", "binding", "command"}
        )
        or value["version"] != VERSION
        or not isinstance(value["command"], str)
        or value["command"] not in {"open", "permit", "close", "capture"}
    ):
        raise invalid()
    scope = binding(value["binding"])
    if expected is not None and scope != binding(expected):
        raise invalid()
    result = {"version": VERSION, "binding": scope, "command": value["command"]}
    if capture:
        path = value["capture_path"]
        if not isinstance(path, str) or not 1 <= len(path) <= 4000 or any(ord(c) < 32 for c in path):
            raise invalid()
        result.update(capture_id=uuid_value(value["capture_id"]), capture_path=path)
    return result


def event(value: object, expected: dict) -> dict:
    if (
        not isinstance(value, dict)
        or set(value)
        != {"version", "binding", "kind", "phase", "error_code", "clean", "capture_available", "capture_error_code"}
        or value["version"] != VERSION
        or not isinstance(value["kind"], str)
        or value["kind"] not in {"state", "heartbeat"}
        or not isinstance(value["phase"], str)
        or value["phase"] not in PHASES
    ):
        raise invalid()
    if binding(value["binding"]) != binding(expected) or type(value["clean"]) is not bool:
        raise invalid()
    code = value["error_code"]
    if code is not None and (
        not isinstance(code, str) or re.fullmatch(r"(?:portal|desktop)_[a-z0-9_]{1,100}", code) is None
    ):
        raise invalid()
    if value["clean"] and value["phase"] not in TERMINAL:
        raise invalid()
    if value["phase"] == "closed" and (code is not None or not value["clean"]):
        raise invalid()
    if value["phase"] in {"failed", "denied"} and code is None:
        raise invalid()
    if value["phase"] not in TERMINAL and code is not None:
        raise invalid()
    capture_availability(value)
    return dict(value)


def capture_availability(value: dict) -> None:
    available, code = value["capture_available"], value["capture_error_code"]
    if (
        type(available) is not bool
        or (code is not None and (not isinstance(code, str) or re.fullmatch(r"portal_[a-z0-9_]{1,100}", code) is None))
        or available != (code is None)
    ):
        raise invalid()


def receipt(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"width", "height", "bytes", "sha256", "method"}:
        raise invalid()
    if (
        any(type(value[k]) is not int for k in ("width", "height", "bytes"))
        or not 1 <= value["width"] <= 4096
        or not 1 <= value["height"] <= 4096
        or value["width"] * value["height"] > 16_000_000
        or not 1 <= value["bytes"] <= 16 * 1024 * 1024
        or not isinstance(value["sha256"], str)
        or re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is None
        or value["method"] != "pipewire_window"
    ):
        raise invalid()
    return dict(value)


def frame_event(value: object, expected: dict) -> dict:
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "binding", "kind", "capture_id", "receipt"}
        or value["version"] != VERSION
        or value["kind"] != "frame"
        or binding(value["binding"]) != binding(expected)
    ):
        raise invalid()
    return {"capture_id": uuid_value(value["capture_id"]), "receipt": receipt(value["receipt"])}


def encode(value: dict) -> bytes:
    data = json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("ascii") + b"\n"
    if len(data) > MAX_FRAME:
        raise invalid()
    return data


def decode(data: bytes) -> dict:
    if not data or len(data) > MAX_FRAME or not data.endswith(b"\n") or b"\n" in data[:-1] or b"\r" in data:
        raise invalid()

    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise invalid()
            value[key] = item
        return value

    def constant(_):
        raise invalid()

    try:
        value = json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError):
        raise invalid() from None
    if not isinstance(value, dict):
        raise invalid()
    return value


def owner(value: object) -> str:
    if not isinstance(value, dict) or set(value) != {"consent", "view_id"} or value["consent"] is not True:
        raise invalid()
    return uuid_value(value["view_id"])


def capture_request(value: object) -> dict:
    if (
        not isinstance(value, dict)
        or set(value) != {"consent", "view_id", "grant_id", "session_id"}
        or value["consent"] is not True
    ):
        raise invalid()
    return {key: uuid_value(value[key]) for key in ("view_id", "grant_id", "session_id")}


def backend_status(value: object, expected_grant: str | None = None) -> dict:
    keys = {
        "grant_id",
        "phase",
        "error_code",
        "revision",
        "clean",
        "cleanup_pending",
        "capture_available",
        "capture_error_code",
    }
    if (
        not isinstance(value, dict)
        or set(value) != keys
        or not isinstance(value["phase"], str)
        or value["phase"] not in PHASES
    ):
        raise invalid()
    uuid_value(value["grant_id"])
    if expected_grant is not None and value["grant_id"] != uuid_value(expected_grant):
        raise invalid()
    if (
        type(value["revision"]) is not int
        or value["revision"] < 0
        or type(value["clean"]) is not bool
        or type(value["cleanup_pending"]) is not bool
    ):
        raise invalid()
    capture_availability(value)
    code = value["error_code"]
    if code is not None and (
        not isinstance(code, str) or re.fullmatch(r"(?:portal|desktop)_[a-z0-9_]{1,100}", code) is None
    ):
        raise invalid()
    return dict(value)
