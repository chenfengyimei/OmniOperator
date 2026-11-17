"""Exact private read-only X11 lease IPC; opaque aliases never authorize a desktop action."""

from __future__ import annotations

import json
import re
import uuid
from typing import cast

from .contracts import BusinessError
from .desktop import MAX_FRAME, validate_target

VERSION = "omni-linux-window-lease-1"
MAX_GROUPS, MAX_TARGETS = 2, 50
MAX_TOTAL_BYTES = 1024 * 1024
LIFETIME_S, MAX_COMMANDS = 120, 32
OPERATIONS = frozenset({"bind", "verify", "close"})
Payload = dict[str, object]


def invalid() -> BusinessError:
    return BusinessError("desktop_protocol_invalid", "Use one bounded, matched window lease request", status=503)


def _uuid(value: object) -> str:
    if not isinstance(value, str) or len(value) != 36:
        raise invalid()
    try:
        if str(uuid.UUID(value)) != value:
            raise invalid()
    except ValueError:
        raise invalid() from None
    return value


def _pairs(values: list[tuple[str, object]]) -> Payload:
    result: Payload = {}
    for key, value in values:
        if key in result:
            raise invalid()
        result[key] = value
    return result


def _constant(_value: str) -> None:
    raise invalid()


def decode(frame: bytes) -> Payload:
    if not 1 <= len(frame) <= MAX_FRAME or not frame.endswith(b"\n") or b"\n" in frame[:-1] or b"\r" in frame:
        raise invalid()
    try:
        value: object = json.loads(frame, object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise invalid() from None
    if not isinstance(value, dict):
        raise invalid()
    return cast(Payload, value)


def encode(value: Payload) -> bytes:
    try:
        frame = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii") + b"\n"
    except (ValueError, TypeError, RecursionError):
        raise invalid() from None
    if len(frame) > MAX_FRAME:
        raise invalid()
    return frame


def _target(value: object, *, leased: bool) -> Payload:
    target = validate_target(value)
    native_id = cast(str, target["native_id"])
    if target["platform"] != "linux" or target["backend"] != "x11":
        raise invalid()
    if leased:
        if not native_id.startswith("x11-live:"):
            raise invalid()
        _uuid(native_id[len("x11-live:") :])
    elif re.fullmatch(r"x11:[0-9a-f]{8}:[0-9a-f]{64}", native_id) is None:
        raise invalid()
    return target


def parse_request(value: object) -> Payload:
    if not isinstance(value, dict):
        raise invalid()
    raw = cast(Payload, value)
    operation = raw.get("operation")
    if not isinstance(operation, str) or operation not in OPERATIONS or raw.get("version") != VERSION:
        raise invalid()
    keys = {"version", "request_id", "operation"}
    if operation == "bind":
        keys.add("targets")
    elif operation == "verify":
        keys.add("target")
    if set(raw) != keys:
        raise invalid()
    result: Payload = {"version": VERSION, "request_id": _uuid(raw["request_id"]), "operation": operation}
    if operation == "bind":
        targets = raw["targets"]
        if not isinstance(targets, list) or not 1 <= len(cast(list[object], targets)) <= MAX_TARGETS:
            raise invalid()
        checked = [_target(target, leased=False) for target in cast(list[object], targets)]
        if len({cast(str, target["native_id"]) for target in checked}) != len(checked):
            raise invalid()
        result["targets"] = checked
    elif operation == "verify":
        result["target"] = _target(raw["target"], leased=True)
    return result


def parse_response(value: object, request: Payload) -> Payload:
    request = parse_request(request)
    if not isinstance(value, dict):
        raise invalid()
    raw = cast(Payload, value)
    if set(raw) != {"version", "request_id", "operation", "ok", "result", "error_code"} or any(
        raw[field] != request[field] for field in ("version", "request_id", "operation")
    ):
        raise invalid()
    if raw["ok"] is not True or raw["error_code"] is not None:
        raise invalid()
    operation = request["operation"]
    if operation == "bind":
        result = raw["result"]
        originals = cast(list[Payload], request["targets"])
        if not isinstance(result, list) or len(cast(list[object], result)) != len(originals):
            raise invalid()
        targets = [_target(target, leased=True) for target in cast(list[object], result)]
        if len({cast(str, target["native_id"]) for target in targets}) != len(targets):
            raise invalid()
        for target, original in zip(targets, originals, strict=True):
            if any(target[key] != original[key] for key in original if key != "native_id"):
                raise invalid()
        return {**raw, "result": targets}
    if operation == "verify":
        original = _target(raw["result"], leased=False)
        target = cast(Payload, request["target"])
        if any(original[key] != target[key] for key in target if key != "native_id"):
            raise invalid()
        return {**raw, "result": original}
    if (
        not isinstance(raw["result"], dict)
        or set(cast(Payload, raw["result"])) != {"closed"}
        or cast(Payload, raw["result"])["closed"] is not True
    ):
        raise invalid()
    return raw
