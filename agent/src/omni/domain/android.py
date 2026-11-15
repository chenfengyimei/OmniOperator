"""Android identities, bounded UI observations and approved input contracts; no native I/O."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping, Sequence
from typing import cast
from urllib.parse import urlsplit

from .android_unicode import text_value
from .contracts import BusinessError, ToolSpec

POLICY = "android-input-1"
KINDS = ("tap", "toggle", "append_ascii", "append_unicode", "long_press", "swipe", "key", "launch")
METHODS = {
    "android_probe": False,
    "android_observe": False,
    "android_capture": False,
    "android_apps": False,
    "android_ready": False,
    "android_commit": True,
    "android_unicode_prepare": False,
    "android_unicode_begin": True,
    "android_unicode_ready": False,
    "android_unicode_commit": True,
    "android_unicode_restore": True,
    "android_unicode_environment": False,
}


def invalid(code: str = "android_invalid") -> BusinessError:
    return BusinessError(code, "Android identity, authority or request is invalid", status=422)


def identity(value: object) -> str:
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError
        return value
    except ValueError:
        raise invalid() from None


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def connection(value: Mapping[str, object]) -> dict[str, object]:
    if value.get("driver") != "android" or value.get("credential_ref"):
        raise invalid("android_configuration_invalid")
    url = urlsplit(str(value.get("endpoint", "")))
    try:
        port = url.port
    except ValueError:
        raise invalid() from None
    if (
        url.scheme != "http"
        or url.hostname != "127.0.0.1"
        or url.path not in {"", "/"}
        or url.username
        or url.password
        or url.query
        or url.fragment
        or port is None
        or not 1 <= port <= 65535
    ):
        raise invalid("android_local_server_required")
    serial = value.get("account")
    if not isinstance(serial, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", serial):
        raise invalid("android_serial_required")
    return {"port": port, "serial": serial}


def device(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise invalid()
    fields = cast(dict[str, object], value)
    if set(fields) != {"serial", "transport_id", "boot_id", "user_id", "fingerprint"}:
        raise invalid()
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", str(fields["serial"])):
        raise invalid()
    for key in ("transport_id", "user_id"):
        item = fields[key]
        if type(item) is not int:
            raise invalid()
        minimum = 1 if key == "transport_id" else 0
        if not minimum <= item <= 2147483647:
            raise invalid()
    identity(fields["boot_id"])
    fingerprint = fields["fingerprint"]
    if not isinstance(fingerprint, str) or not 1 <= len(fingerprint) <= 512 or any(ord(c) < 32 for c in fingerprint):
        raise invalid()
    return dict(fields)


def action_request(value: object) -> dict[str, object]:
    fields = {"consent", "grant_id", "observation_id", "node_id", "session_id", "request_id", "kind", "text"}
    if isinstance(value, dict) and "params" in cast(dict[str, object], value):
        fields.add("params")
    if not isinstance(value, dict):
        raise invalid()
    provided = cast(dict[str, object], value)
    if set(provided) != fields or provided["consent"] is not True:
        raise invalid()
    result = {key: identity(provided[key]) for key in fields - {"consent", "kind", "text", "params"}}
    kind, text = provided["kind"], provided["text"]
    if not isinstance(kind, str) or kind not in KINDS or not isinstance(text, str):
        raise invalid()
    # ADB input text cannot reliably deliver Unicode. No IME installation or shell interpolation.
    if kind == "append_ascii":
        if not 1 <= len(text) <= 256 or not re.fullmatch(r"[A-Za-z0-9 .,_@+-]+", text):
            raise invalid("android_text_unsupported")
    elif kind == "append_unicode":
        text_value(text)
    elif text:
        raise invalid()
    params = input_params(kind, provided.get("params", {}))
    return {**result, "consent": True, "kind": kind, "text": text, "params": params}


def package(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 200
        or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+", value)
    ):
        raise invalid("android_package_invalid")
    return value


def input_params(kind: str, value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise invalid()
    fields = cast(dict[str, object], value)
    expected = {
        "long_press": {"duration_ms"},
        "swipe": {"direction", "duration_ms"},
        "key": {"key"},
        "launch": {"package"},
        "append_unicode": {"helper_revision", "restore_ime"},
    }.get(kind, set())
    if set(fields) != expected:
        raise invalid()
    if "duration_ms" in expected:
        duration = fields["duration_ms"]
        if type(duration) is not int:
            raise invalid()
        minimum = 500 if kind == "long_press" else 100
        if not minimum <= duration <= 1500:
            raise invalid()
    if kind == "swipe" and fields["direction"] not in {"up", "down", "left", "right"}:
        raise invalid()
    if kind == "key" and fields["key"] not in {
        "BACK",
        "HOME",
        "ENTER",
        "TAB",
        "DPAD_UP",
        "DPAD_DOWN",
        "DPAD_LEFT",
        "DPAD_RIGHT",
    }:
        raise invalid()
    if kind == "launch":
        package(fields["package"])
    if kind == "append_unicode":
        revision, restore = fields["helper_revision"], fields["restore_ime"]
        if type(revision) is not int:
            raise invalid()
        if revision < 1 or restore is not True:
            raise invalid()
    return dict(fields)


RESTORE_SPEC = ToolSpec(
    "android_restore_ime",
    "Owner-approved recovery of a captured temporary IME selection; no text input",
    {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "environment_id", "revision", "target_serial", "original_ime"],
        "properties": {
            "kind": {"const": "restore_ime"},
            "environment_id": {"type": "string"},
            "revision": {"type": "integer"},
            "target_serial": {"type": "string"},
            "original_ime": {"type": "string"},
        },
    },
    writes=True,
    timeout_s=25,
    resource="android",
)


def access_request(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise invalid()
    fields = cast(dict[str, object], value)
    if set(fields) != {"grant_id", "target_serial", "target_user", "packages", "kinds", "ttl_seconds"}:
        raise invalid()
    identity(fields["grant_id"])
    serial = fields["target_serial"]
    if not isinstance(serial, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", serial):
        raise invalid()
    user = fields["target_user"]
    if type(user) is not int:
        raise invalid()
    if not 0 <= user <= 2147483647:
        raise invalid()
    ttl = fields["ttl_seconds"]
    if type(ttl) is not int:
        raise invalid()
    if not 60 <= ttl <= 900:
        raise invalid()
    packages, kinds = fields["packages"], fields["kinds"]
    if not isinstance(packages, list):
        raise invalid()
    package_rows = cast(list[object], packages)
    if not 1 <= len(package_rows) <= 16 or len(set(map(str, package_rows))) != len(package_rows):
        raise invalid()
    if not isinstance(kinds, list):
        raise invalid()
    kind_rows = cast(list[object], kinds)
    if len(kind_rows) > len(KINDS) or any(k not in KINDS for k in kind_rows) or len(set(kind_rows)) != len(kind_rows):
        raise invalid()
    return {**fields, "packages": [package(p) for p in package_rows], "kinds": list(kind_rows)}


def scroll_verified(
    before: Sequence[Mapping[str, object]],
    after: Sequence[Mapping[str, object]],
    container: Mapping[str, object],
    direction: str,
) -> bool:
    """Require two stable child identities moving along the approved axis; changed text alone is insufficient."""
    path = container["path"]
    if not isinstance(path, list):
        return False
    prefix = cast(list[object], path)

    def children(nodes: Sequence[Mapping[str, object]]) -> dict[tuple[object, ...], list[Mapping[str, object]]]:
        result: dict[tuple[object, ...], list[Mapping[str, object]]] = {}
        for node in nodes:
            node_path = node.get("path")
            if node.get("password") or not isinstance(node_path, list):
                continue
            steps = cast(list[object], node_path)
            if len(steps) <= len(prefix) or steps[: len(prefix)] != prefix:
                continue
            key = tuple(node.get(k, "") for k in ("resource_id", "class", "text", "description"))
            if not key[0] and not key[2] and not key[3]:
                continue
            result.setdefault(key, []).append(node)
        return result

    old, new = children(before), children(after)
    moved = 0
    axis = 1 if direction in {"up", "down"} else 0
    sign = -1 if direction in {"up", "left"} else 1
    for key in old.keys() & new.keys():
        if len(old[key]) != 1 or len(new[key]) != 1:
            continue
        first, second = old[key][0].get("bounds"), new[key][0].get("bounds")
        if not isinstance(first, list) or not isinstance(second, list):
            continue
        left, right = cast(list[object], first), cast(list[object], second)
        if len(left) < 2 or len(right) < 2:
            continue
        a, b = left[axis], left[1 - axis]
        c, d = right[axis], right[1 - axis]
        if type(a) is not int or type(b) is not int or type(c) is not int or type(d) is not int:
            return False
        delta = c - a
        perpendicular = d - b
        if abs(perpendicular) > 2 or delta * sign < -2:
            return False
        if delta * sign >= 2:
            moved += 1
    return moved >= 2


SPEC = ToolSpec(
    "android_input",
    "Owner-selected Android input with explicit approval and fresh identity checks",
    {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "binding_id",
            "grant_id",
            "kind",
            "text",
            "target_application",
            "policy",
            "target_serial",
            "target_user",
            "target_resource",
            "observation_sha256",
        ],
        "properties": {
            "binding_id": {"type": "string"},
            "grant_id": {"type": "string"},
            "kind": {"enum": list(KINDS)},
            "params": {"type": "object"},
            "text": {"type": "string", "maxLength": 256},
            "target_application": {"type": "string"},
            "policy": {"const": POLICY},
            "target_serial": {"type": "string"},
            "target_user": {"type": "integer"},
            "target_resource": {"type": "string"},
            "observation_sha256": {"type": "string"},
        },
    },
    writes=True,
    timeout_s=25,
    resource="android",
)

ACCESS_SPEC = ToolSpec(
    "android_request_access",
    "Request owner approval for this run to observe specified Android apps and request individually approved inputs. The owner must supply the device grant ID and identity.",
    {
        "type": "object",
        "additionalProperties": False,
        "required": ["grant_id", "target_serial", "target_user", "packages", "kinds", "ttl_seconds"],
        "properties": {
            "grant_id": {"type": "string"},
            "target_serial": {"type": "string"},
            "target_user": {"type": "integer"},
            "packages": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 16},
            "kinds": {"type": "array", "items": {"enum": list(KINDS)}, "maxItems": len(KINDS)},
            "ttl_seconds": {"type": "integer", "minimum": 60, "maximum": 900},
        },
    },
    writes=True,
    timeout_s=10,
    resource="android-authority",
)
OBSERVE_SPEC = ToolSpec(
    "android_observe",
    "Observe only apps authorized for this run. Requires access_id returned by approved android_request_access; private channels cannot use it.",
    {
        "type": "object",
        "additionalProperties": False,
        "required": ["access_id"],
        "properties": {"access_id": {"type": "string"}, "operation": {"enum": ["ui", "apps"]}},
    },
    timeout_s=25,
    resource="android",
)
MODEL_SPEC = ToolSpec(
    "android_act",
    "Request an individually approved input bound to this run's Android observation; copy exact target fields from android_observe. Never retry unknown results.",
    {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "access_id",
            "observation_id",
            "node_id",
            "kind",
            "text",
            "params",
            "target_serial",
            "target_user",
            "target_application",
            "target_resource",
            "observation_sha256",
        ],
        "properties": {
            "access_id": {"type": "string"},
            "observation_id": {"type": "string"},
            "node_id": {"type": "string"},
            "kind": {"enum": list(KINDS)},
            "text": {"type": "string", "maxLength": 256},
            "params": {"type": "object"},
            "target_serial": {"type": "string"},
            "target_user": {"type": "integer"},
            "target_application": {"type": "string"},
            "target_resource": {"type": "string"},
            "observation_sha256": {"type": "string"},
        },
    },
    writes=True,
    timeout_s=25,
    resource="android",
)
