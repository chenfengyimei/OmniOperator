"""Bounded desktop discovery and read-only UI contracts; no native I/O or action authorization."""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
import uuid
from dataclasses import dataclass, field
from typing import cast

from .contracts import BusinessError

VERSION = "omni-desktop-ipc-6"
CAPABILITIES = ("window_identity", "accessibility", "screen_capture", "foreground_control")
PLATFORMS = frozenset({"win32", "darwin", "linux"})
OPERATIONS = frozenset({"probe", "targets", "verify", "observe", "capture", "act", "foreground"})
OWNER_OPERATIONS = (OPERATIONS - {"act", "foreground"}) | {"begin"}
BACKENDS = {"win32": frozenset({"win32"}), "darwin": frozenset({"ax"}), "linux": frozenset({"atspi", "x11", "portal"})}
MAX_FRAME = 65536
MAX_IDENTITY = 512
MAX_REASON = 200
MAX_HINTS = 16
MAX_HINT_LENGTH = 1000
MAX_NODES, MAX_DEPTH, MAX_NODE_TEXT, MAX_TEXT_BYTES = 128, 8, 512, 16384
MAX_CAPTURE_BYTES, MAX_CAPTURE_PIXELS, MAX_CAPTURE_EDGE = 16 * 1024 * 1024, 16_000_000, 8192
LINUX_ERRORS = frozenset(
    {
        "desktop_input_unverified",
        "desktop_input_held",
        "desktop_user_intervened",
        "desktop_observation_changed",
        "desktop_action_revoked",
        "desktop_password_control",
        "desktop_action_unverified",
        "desktop_dependency_missing",
        "desktop_dependency_incompatible",
        "desktop_bus_address_denied",
        "desktop_bus_peer_unverified",
        "desktop_bus_identity_changed",
        "desktop_bus_message_limit",
        "desktop_bus_disconnected",
        "desktop_bus_response_invalid",
        "desktop_session_unverified",
        "desktop_headless",
        "desktop_target_unverified",
        "desktop_target_changed",
        "desktop_target_session_unverified",
        "desktop_accessibility_unavailable",
        "desktop_cleanup_pending",
        "desktop_x11_protocol_invalid",
        "desktop_x11_protocol_limit",
        "desktop_x11_disconnected",
        "desktop_x11_auth_unavailable",
        "desktop_x11_display_unverified",
        "desktop_x11_session_unavailable",
        "desktop_x11_peer_unverified",
        "desktop_x11_extension_unavailable",
        "desktop_x11_target_unverified",
        "desktop_x11_window_list_unavailable",
        "desktop_x11_pixel_format_unsupported",
        "desktop_capture_blank",
        "desktop_capture_invalid",
    }
)
TARGET_KEYS = frozenset({"platform", "backend", "native_id", "pid", "process_created", "application_id", "session_id"})
CAPABILITY_KEYS = frozenset(
    {"id", "supported", "installed", "permission", "available", "error_code", "limitations", "recovery"}
)


def invalid() -> BusinessError:
    return BusinessError("desktop_invalid", "Use an exact, bounded desktop identity and discovery protocol", status=422)


def _object(value: object, keys: set[str] | frozenset[str]) -> dict[str, object]:
    if not isinstance(value, dict):
        raise invalid()
    fields = cast(dict[str, object], value)
    if set(fields) != keys:
        raise invalid()
    return fields


def _text(value: object, maximum: int, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or not value.strip()
        or any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value)
    ):
        raise invalid()
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise invalid() from exc
    return value


def _choice(value: object, choices: tuple[str, ...] | frozenset[str]) -> str:
    if not isinstance(value, str) or value not in choices:
        raise invalid()
    return value


def _boolean(value: object) -> bool:
    if type(value) is not bool:
        raise invalid()
    return value


def _integer(value: object, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise invalid()
    return value


def _uuid(value: object) -> str:
    if not isinstance(value, str) or len(value) != 36:
        raise invalid()
    try:
        if str(uuid.UUID(value)) != value:
            raise invalid()
    except ValueError as exc:
        raise invalid() from exc
    return value


def desktop_object(value: object, keys: set[str] | frozenset[str]) -> dict[str, object]:
    return _object(value, keys)


def desktop_text(value: object, maximum: int, *, nullable: bool = False) -> str | None:
    return _text(value, maximum, nullable=nullable)


def desktop_choice(value: object, choices: tuple[str, ...] | frozenset[str]) -> str:
    return _choice(value, choices)


def desktop_integer(value: object, minimum: int, maximum: int) -> int:
    return _integer(value, minimum, maximum)


def desktop_uuid(value: object) -> str:
    return _uuid(value)


def _code(value: object, *, nullable: bool = True) -> str | None:
    code = _text(value, MAX_REASON, nullable=nullable)
    if code is not None and re.fullmatch(r"[a-z][a-z0-9_]*", code) is None:
        raise invalid()
    return code


def _canonical(value: object) -> bytes:
    try:
        data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode(
            "utf-8"
        )
    except (ValueError, TypeError, UnicodeError, OverflowError) as exc:
        raise invalid() from exc
    if len(data) + 1 > MAX_FRAME:
        raise invalid()
    return data


def validate_target(data: object) -> dict[str, object]:
    value = _object(data, TARGET_KEYS)
    platform = _choice(value["platform"], PLATFORMS)
    backend = _choice(value["backend"], BACKENDS[platform])
    created_value = value["process_created"]
    if type(created_value) not in {int, float}:
        raise invalid()
    try:
        created = float(cast(float, created_value))
    except (ValueError, OverflowError) as exc:
        raise invalid() from exc
    if not math.isfinite(created) or created <= 0:
        raise invalid()
    return {
        "platform": platform,
        "backend": backend,
        "native_id": _text(value["native_id"], MAX_IDENTITY),
        "pid": _integer(value["pid"], 1, 4294967295),
        "process_created": created,
        "application_id": _text(value["application_id"], MAX_IDENTITY),
        "session_id": _text(value["session_id"], MAX_IDENTITY),
    }


def target_fingerprint(target: object) -> str:
    return hashlib.sha256(_canonical(validate_target(target))).hexdigest()


def encode_native_target(target: object) -> dict[str, object]:
    result = validate_target(target)
    # Windows PowerShell/.NET Framework decimal JSON conversion is not bit-preserving.
    result["process_created"] = "ieee754:" + struct.pack(">d", cast(float, result["process_created"])).hex()
    return result


def decode_native_target(target: object) -> dict[str, object]:
    candidate: object = target
    if isinstance(target, dict):
        fields = cast(dict[str, object], target)
        created = fields.get("process_created")
        if isinstance(created, str):
            if re.fullmatch(r"ieee754:[0-9a-f]{16}", created) is None:
                raise invalid()
            candidate = {**fields, "process_created": struct.unpack(">d", bytes.fromhex(created[8:]))[0]}
    return validate_target(candidate)


def parse_request(data: object) -> dict[str, object]:
    keys = {"version", "request_id", "operation", "target", "limit"}
    if isinstance(data, dict):
        fields = cast(dict[str, object], data)
        if fields.get("operation") == "capture":
            keys.add("output_path")
        if fields.get("operation") in {"act", "foreground"}:
            keys.add("action")
    value = _object(cast(object, data), keys)
    if value["version"] != VERSION:
        raise invalid()
    operation = _choice(value["operation"], OPERATIONS)
    limit = _integer(value["limit"], 1, 50)
    target = None
    if operation in {"verify", "observe", "capture", "act", "foreground"}:
        if limit != 1:
            raise invalid()
        target = decode_native_target(value["target"])
    elif value["target"] is not None or (operation == "probe" and limit != 1):
        raise invalid()
    result: dict[str, object] = {
        "version": VERSION,
        "request_id": _uuid(value["request_id"]),
        "operation": operation,
        "target": target,
        "limit": limit,
    }
    if operation == "capture":
        # This is a host-generated IPC destination, never an owner API field.
        result["output_path"] = _text(value["output_path"], 4000)
    if operation == "act":
        from .desktop_actions import semantic

        result["action"] = semantic(value["action"])
    if operation == "foreground":
        from .foreground import action

        result["action"] = action(value["action"])
    if operation in {"act", "foreground"}:
        assert target is not None
        action = result["action"]
        if not isinstance(action, dict):
            raise invalid()
        node = cast(dict[str, object], action).get("node")
        if not isinstance(node, dict):
            raise invalid()
        proof_backend = cast(dict[str, object], node).get("backend")
        if proof_backend != {"win32": None, "darwin": "ax", "linux": "atspi"}[cast(str, target["platform"])]:
            raise invalid()
        if target["platform"] == "linux" and (operation != "act" or target["backend"] != "atspi"):
            raise invalid()
    _canonical(result)
    return result


def validate_capture(data: object, target: object) -> dict[str, object]:
    value = _object(data, {"target", "width", "height", "bytes", "sha256", "method"})
    actual = validate_target(value["target"])
    expected_method = {"win32": "print_window", "darwin": "screen_capture_kit", "linux": "x11_composite"}.get(
        cast(str, actual["platform"])
    )
    if actual != validate_target(target) or expected_method is None or value["method"] != expected_method:
        raise invalid()
    if actual["platform"] == "linux" and actual["backend"] != "x11":
        raise invalid()
    width = _integer(value["width"], 1, MAX_CAPTURE_EDGE)
    height = _integer(value["height"], 1, MAX_CAPTURE_EDGE)
    if width * height > MAX_CAPTURE_PIXELS:
        raise invalid()
    digest = _text(value["sha256"], 64)
    if digest is None or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise invalid()
    return {
        "target": actual,
        "width": width,
        "height": height,
        "bytes": _integer(value["bytes"], 1, MAX_CAPTURE_BYTES),
        "sha256": digest,
        "method": expected_method,
    }


@dataclass(frozen=True)
class CapturedFrame:
    content: bytes = field(repr=False)
    width: int
    height: int
    sha256: str
    method: str = "print_window"

    def validate(self) -> None:
        # Pixel decoding belongs to the file adapter; the application verifies its receipt.
        if (
            type(self.content) is not bytes
            or not 1 <= len(self.content) <= MAX_CAPTURE_BYTES
            or type(self.width) is not int
            or type(self.height) is not int
            or not 1 <= self.width <= MAX_CAPTURE_EDGE
            or not 1 <= self.height <= MAX_CAPTURE_EDGE
            or self.width * self.height > MAX_CAPTURE_PIXELS
            or self.method
            not in {"print_window", "screen_capture_kit", "x11_composite", "pipewire_window", "adb_screencap"}
            or hashlib.sha256(self.content).hexdigest() != self.sha256
        ):
            raise invalid()


def _hints(value: object) -> list[str]:
    if not isinstance(value, list):
        raise invalid()
    items = cast(list[object], value)
    if len(items) > MAX_HINTS:
        raise invalid()
    result: list[str] = []
    for item in items:
        text = _text(item, MAX_HINT_LENGTH)
        assert text is not None
        result.append(text)
    return result


def validate_capabilities(data: object) -> dict[str, object]:
    value = _object(data, {"version", "platform", "session", "capabilities", "acceptance"})
    if value["version"] != VERSION or value["acceptance"] != "pending":
        raise invalid()
    platform = _choice(value["platform"], PLATFORMS)
    session_fields = _object(value["session"], {"kind", "identity"})
    session: dict[str, object] = {
        "kind": _choice(session_fields["kind"], frozenset({"interactive", "headless", "unknown"})),
        "identity": _text(session_fields["identity"], MAX_IDENTITY, nullable=True),
    }
    raw_capabilities = value["capabilities"]
    if not isinstance(raw_capabilities, list):
        raise invalid()
    capabilities = cast(list[object], raw_capabilities)
    if len(capabilities) != len(CAPABILITIES):
        raise invalid()
    indexed: dict[str, dict[str, object]] = {}
    for item in capabilities:
        entry = _object(item, CAPABILITY_KEYS)
        identity = _choice(entry["id"], CAPABILITIES)
        if identity in indexed:
            raise invalid()
        supported, installed, available = (
            _boolean(entry["supported"]),
            _boolean(entry["installed"]),
            _boolean(entry["available"]),
        )
        permission = _choice(entry["permission"], frozenset({"granted", "denied", "unknown", "not_applicable"}))
        if available and (
            not supported
            or not installed
            or permission not in {"granted", "not_applicable"}
            or session["kind"] != "interactive"
        ):
            raise invalid()
        indexed[identity] = {
            "id": identity,
            "supported": supported,
            "installed": installed,
            "permission": permission,
            "available": available,
            "error_code": _code(entry["error_code"]),
            "limitations": _hints(entry["limitations"]),
            "recovery": _hints(entry["recovery"]),
        }
    result: dict[str, object] = {
        "version": VERSION,
        "platform": platform,
        "session": session,
        "capabilities": [indexed[identity] for identity in CAPABILITIES],
        "acceptance": "pending",
    }
    _canonical(result)
    return result


def validate_targets(data: object, limit: int = 50) -> dict[str, object]:
    limit = _integer(limit, 1, 50)
    raw = _object(data, {"items", "truncated"})
    items_raw = raw["items"]
    if not isinstance(items_raw, list):
        raise invalid()
    items = cast(list[object], items_raw)
    if len(items) > limit:
        raise invalid()
    targets = [validate_target(item) for item in items]
    if len({target_fingerprint(target) for target in targets}) != len(targets):
        raise invalid()
    result: dict[str, object] = {"items": targets, "truncated": _boolean(raw["truncated"])}
    _canonical(result)
    return result


def validate_verification(data: object, target: object) -> dict[str, object]:
    expected = validate_target(target)
    raw = _object(data, {"matches", "target", "reason"})
    matches = _boolean(raw["matches"])
    actual = None if raw["target"] is None else validate_target(raw["target"])
    reason = _code(raw["reason"], nullable=matches)
    if matches and (actual != expected or reason is not None):
        raise invalid()
    result: dict[str, object] = {"matches": matches, "target": actual, "reason": reason}
    _canonical(result)
    return result


def validate_observation(data: object, target: object) -> dict[str, object]:
    """Treat helper UI data as untrusted, including hierarchy, text and target proof."""
    expected = validate_target(target)
    linux = expected["platform"] == "linux"
    raw = _object(data, {"target", "nodes", "truncated"} | ({"coordinate_space"} if linux else set()))
    if linux and raw["coordinate_space"] != "window":
        raise invalid()
    actual = validate_target(raw["target"])
    nodes_raw = raw["nodes"]
    if actual != validate_target(target) or not isinstance(nodes_raw, list):
        raise invalid()
    raw_nodes = cast(list[object], nodes_raw)
    if not 1 <= len(raw_nodes) <= MAX_NODES:
        raise invalid()
    nodes: list[dict[str, object]] = []
    depths: dict[int, int] = {}
    text_bytes = 0
    for index, item in enumerate(raw_nodes, 1):
        keys = {"id", "parent", "role", "name", "text", "password", "enabled", "offscreen", "bounds"}
        actionable = isinstance(item, dict) and "reference" in item
        if actionable:
            keys |= {"reference", "actions"}
        entry = _object(cast(object, item), keys)
        identity = _integer(entry["id"], index, index)
        parent = entry["parent"]
        if index == 1:
            if parent is not None:
                raise invalid()
            depth = 1
        else:
            parent = _integer(parent, 1, index - 1)
            depth = depths[parent] + 1
        if depth > MAX_DEPTH:
            raise invalid()
        depths[identity] = depth
        texts: dict[str, str] = {}
        for key in ("name", "text"):
            text_value = entry[key]
            if not isinstance(text_value, str) or len(text_value) > MAX_NODE_TEXT:
                raise invalid()
            if any((ord(c) < 32 and c not in "\t\r\n") or 127 <= ord(c) <= 159 for c in text_value):
                raise invalid()
            try:
                text_bytes += len(text_value.encode("utf-8"))
            except UnicodeError as exc:
                raise invalid() from exc
            texts[key] = text_value
        password = _boolean(entry["password"])
        if text_bytes > MAX_TEXT_BYTES or (password and (texts["name"] or texts["text"])):
            raise invalid()
        bounds_raw = entry["bounds"]
        if not isinstance(bounds_raw, list):
            raise invalid()
        bounds = cast(list[object], bounds_raw)
        if len(bounds) != 4:
            raise invalid()
        if any(type(v) not in {int, float} or abs(cast(float, v)) > 10_000_000 or not math.isfinite(cast(float, v)) for v in bounds):
            raise invalid()
        if cast(float, bounds[2]) < 0 or cast(float, bounds[3]) < 0:
            raise invalid()
        nodes.append(
            {
                "id": identity,
                "parent": parent,
                "role": _text(entry["role"], 80),
                **texts,
                "password": password,
                "enabled": _boolean(entry["enabled"]),
                "offscreen": _boolean(entry["offscreen"]),
                "bounds": bounds,
            }
        )
        if actionable:
            from .desktop_actions import KINDS, node_proof
            from .foreground import KINDS as FOREGROUND_KINDS

            actions_raw = entry["actions"]
            if not isinstance(actions_raw, list):
                raise invalid()
            actions = cast(list[object], actions_raw)
            if (
                len(actions) > 6
                or any(a not in KINDS + FOREGROUND_KINDS for a in actions)
                or len(set(actions)) != len(actions)
            ):
                raise invalid()
            reference = node_proof(entry["reference"]) if entry["reference"] is not None else None
            if (
                reference is not None
                and reference.get("backend") != {"win32": None, "darwin": "ax", "linux": "atspi"}[cast(str, actual["platform"])]
            ):
                raise invalid()
            if linux and (actual["backend"] != "atspi" or any(a not in KINDS for a in actions)):
                raise invalid()
            if actions and (reference is None or password or not entry["enabled"] or entry["offscreen"]):
                raise invalid()
            nodes[-1].update(reference=reference, actions=actions)
    result: dict[str, object] = {"target": actual, "nodes": nodes, "truncated": _boolean(raw["truncated"])}
    if linux:
        result["coordinate_space"] = "window"
    _canonical(result)
    return result


def parse_response(data: object, request: object) -> dict[str, object]:
    expected = parse_request(request)
    value = _object(data, {"version", "request_id", "operation", "ok", "result", "error_code"})
    if (
        value["version"] != VERSION
        or _uuid(value["request_id"]) != expected["request_id"]
        or value["operation"] != expected["operation"]
    ):
        raise invalid()
    ok = _boolean(value["ok"])
    error = _code(value["error_code"])
    result: object = None
    if not ok:
        if value["result"] is not None or error is None:
            raise invalid()
        result = None
    else:
        if error is not None:
            raise invalid()
        raw_payload = value["result"]
        payload: object = raw_payload
        if expected["operation"] in {"observe", "capture"} and isinstance(raw_payload, dict):
            fields = cast(dict[str, object], raw_payload)
            if "target" in fields:
                payload = {**fields, "target": decode_native_target(fields["target"])}
        if expected["operation"] == "probe":
            result = validate_capabilities(value["result"])
        elif expected["operation"] == "targets":
            result = validate_targets(value["result"], cast(int, expected["limit"]))
        elif expected["operation"] == "observe":
            result = validate_observation(payload, expected["target"])
        elif expected["operation"] == "capture":
            result = validate_capture(payload, expected["target"])
        elif expected["operation"] in {"act", "foreground"}:
            from .desktop_actions import action_result

            action = expected["action"]
            if not isinstance(action, dict):
                raise invalid()
            action_result(payload, cast(str | None, cast(dict[str, object], action).get("kind")))
            result = payload
        else:
            result = validate_verification(value["result"], expected["target"])
    normalized: dict[str, object] = {
        "version": VERSION,
        "request_id": expected["request_id"],
        "operation": expected["operation"],
        "ok": ok,
        "result": result,
        "error_code": error,
    }
    _canonical(normalized)
    return normalized


def parse_owner(operation: object, data: object) -> dict[str, object]:
    operation = _choice(operation, OWNER_OPERATIONS)
    keys = (
        {"consent", "view_id"}
        if operation in {"begin", "probe"}
        else {"consent", "view_id", "limit"}
        if operation == "targets"
        else {"consent", "view_id", "snapshot_id", "target_id"}
    )
    if operation == "capture":
        keys.add("session_id")
    value = _object(data, keys)
    if value["consent"] is not True:
        raise invalid()
    view_id = _uuid(value["view_id"])
    if operation in {"begin", "probe"}:
        return {"consent": True, "view_id": view_id}
    if operation == "targets":
        return {"consent": True, "view_id": view_id, "limit": _integer(value["limit"], 1, 50)}
    result: dict[str, object] = {
        "consent": True,
        "view_id": view_id,
        "snapshot_id": _uuid(value["snapshot_id"]),
        "target_id": _uuid(value["target_id"]),
    }
    if operation == "capture":
        result["session_id"] = _uuid(value["session_id"])
    return result

