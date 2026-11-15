"""Ephemeral window-share consent and helper protocol; grants never identify a native PID or authorize input."""

from __future__ import annotations

import json
import re
import uuid
from typing import NoReturn, cast

from .contracts import BusinessError

VERSION = "omni-portal-ipc-2"
MAX_FRAME, MAX_TOTAL, MAX_EVENTS = 16384, 262144, 512
CHOICE_SECONDS, LIFETIME_SECONDS = 90, 150
PHASES = frozenset(
    {"starting", "prepared", "creating", "selecting", "authorizing", "active", "closing", "closed", "denied", "failed"}
)
TERMINAL = frozenset({"closed", "denied", "failed"})
BINDING_KEYS = frozenset({"agent_id", "view_id", "boot_id", "grant_id"})
TRANSITIONS: dict[str, set[str]] = {
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


def _fields(value: object, keys: set[str] | frozenset[str]) -> dict[str, object]:
    if not isinstance(value, dict):
        raise invalid()
    fields = cast(dict[str, object], value)
    if set(fields) != keys:
        raise invalid()
    return fields


def uuid_value(value: object) -> str:
    if not isinstance(value, str) or len(value) != 36:
        raise invalid()
    try:
        if str(uuid.UUID(value)) != value:
            raise invalid()
    except ValueError:
        raise invalid() from None
    return value


def binding(value: object) -> dict[str, str]:
    fields = _fields(value, BINDING_KEYS)
    return {key: uuid_value(fields[key]) for key in sorted(BINDING_KEYS)}


def command(value: object, expected: dict[str, str] | None = None) -> dict[str, object]:
    capture = isinstance(value, dict) and cast(dict[str, object], value).get("command") == "capture"
    fields = _fields(
        cast(object, value),
        (
            {"version", "binding", "command", "capture_id", "capture_path"}
            if capture
            else {"version", "binding", "command"}
        ),
    )
    if (
        fields["version"] != VERSION
        or not isinstance(fields["command"], str)
        or fields["command"] not in {"open", "permit", "close", "capture"}
    ):
        raise invalid()
    scope = binding(fields["binding"])
    if expected is not None and scope != binding(expected):
        raise invalid()
    result: dict[str, object] = {"version": VERSION, "binding": scope, "command": fields["command"]}
    if capture:
        path = fields["capture_path"]
        if not isinstance(path, str) or not 1 <= len(path) <= 4000 or any(ord(c) < 32 for c in path):
            raise invalid()
        result.update(capture_id=uuid_value(fields["capture_id"]), capture_path=path)
    return result


def event(value: object, expected: dict[str, str]) -> dict[str, object]:
    fields = _fields(
        value,
        {"version", "binding", "kind", "phase", "error_code", "clean", "capture_available", "capture_error_code"},
    )
    if (
        fields["version"] != VERSION
        or not isinstance(fields["kind"], str)
        or fields["kind"] not in {"state", "heartbeat"}
        or not isinstance(fields["phase"], str)
        or fields["phase"] not in PHASES
    ):
        raise invalid()
    if binding(fields["binding"]) != binding(expected) or type(fields["clean"]) is not bool:
        raise invalid()
    code = fields["error_code"]
    if code is not None and (
        not isinstance(code, str) or re.fullmatch(r"(?:portal|desktop)_[a-z0-9_]{1,100}", code) is None
    ):
        raise invalid()
    if fields["clean"] and fields["phase"] not in TERMINAL:
        raise invalid()
    if fields["phase"] == "closed" and (code is not None or not fields["clean"]):
        raise invalid()
    if fields["phase"] in {"failed", "denied"} and code is None:
        raise invalid()
    if fields["phase"] not in TERMINAL and code is not None:
        raise invalid()
    capture_availability(fields)
    return dict(fields)


def capture_availability(value: dict[str, object]) -> None:
    available, code = value["capture_available"], value["capture_error_code"]
    if (
        type(available) is not bool
        or (code is not None and (not isinstance(code, str) or re.fullmatch(r"portal_[a-z0-9_]{1,100}", code) is None))
        or available != (code is None)
    ):
        raise invalid()


def receipt(value: object) -> dict[str, object]:
    fields = _fields(value, {"width", "height", "bytes", "sha256", "method"})
    width, height, size = fields["width"], fields["height"], fields["bytes"]
    if type(width) is not int or type(height) is not int or type(size) is not int:
        raise invalid()
    width_i, height_i, size_i = width, height, size
    digest = fields["sha256"]
    if (
        not 1 <= width_i <= 4096
        or not 1 <= height_i <= 4096
        or width_i * height_i > 16_000_000
        or not 1 <= size_i <= 16 * 1024 * 1024
        or not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or fields["method"] != "pipewire_window"
    ):
        raise invalid()
    return dict(fields)


def frame_event(value: object, expected: dict[str, str]) -> dict[str, object]:
    fields = _fields(value, {"version", "binding", "kind", "capture_id", "receipt"})
    if fields["version"] != VERSION or fields["kind"] != "frame" or binding(fields["binding"]) != binding(expected):
        raise invalid()
    return {"capture_id": uuid_value(fields["capture_id"]), "receipt": receipt(fields["receipt"])}


def encode(value: dict[str, object]) -> bytes:
    data = json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("ascii") + b"\n"
    if len(data) > MAX_FRAME:
        raise invalid()
    return data


def decode(data: bytes) -> dict[str, object]:
    if not data or len(data) > MAX_FRAME or not data.endswith(b"\n") or b"\n" in data[:-1] or b"\r" in data:
        raise invalid()

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in items:
            if key in value:
                raise invalid()
            value[key] = item
        return value

    def constant(_: str) -> NoReturn:
        raise invalid()

    try:
        decoded = json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError):
        raise invalid() from None
    if not isinstance(decoded, dict):
        raise invalid()
    return cast(dict[str, object], decoded)


def owner(value: object) -> str:
    fields = _fields(value, {"consent", "view_id"})
    if fields["consent"] is not True:
        raise invalid()
    return uuid_value(fields["view_id"])


def capture_request(value: object) -> dict[str, str]:
    fields = _fields(value, {"consent", "view_id", "grant_id", "session_id"})
    if fields["consent"] is not True:
        raise invalid()
    return {key: uuid_value(fields[key]) for key in ("view_id", "grant_id", "session_id")}


def backend_status(value: object, expected_grant: str | None = None) -> dict[str, object]:
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
    fields = _fields(value, keys)
    if not isinstance(fields["phase"], str) or fields["phase"] not in PHASES:
        raise invalid()
    uuid_value(fields["grant_id"])
    if expected_grant is not None and fields["grant_id"] != uuid_value(expected_grant):
        raise invalid()
    revision = fields["revision"]
    if (
        type(revision) is not int
        or revision < 0
        or type(fields["clean"]) is not bool
        or type(fields["cleanup_pending"]) is not bool
    ):
        raise invalid()
    capture_availability(fields)
    code = fields["error_code"]
    if code is not None and (
        not isinstance(code, str) or re.fullmatch(r"(?:portal|desktop)_[a-z0-9_]{1,100}", code) is None
    ):
        raise invalid()
    return dict(fields)
