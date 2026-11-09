"""Exact semantic action contracts; observations never grant write authority."""

from __future__ import annotations

from .contracts import ActionResult, Effect, Outcome, ToolSpec
from .desktop import _choice, _integer, _object, _text, _uuid, invalid
from .foreground import KINDS as FOREGROUND_KINDS
from .foreground import POLICY as FOREGROUND_POLICY
from .foreground import input_value

POLICY = "desktop-semantic-1"
KINDS = ("invoke", "select", "set_text")


def runtime_id(value: object) -> list[int]:
    if not isinstance(value, list) or not 1 <= len(value) <= 32:
        raise invalid()
    return [_integer(item, -2147483648, 2147483647) for item in value]


def node_proof(value: object) -> dict:
    if isinstance(value, dict) and value.get("backend") == "atspi":
        value = _object(
            value, {"backend", "path", "ancestry_sha256", "role", "name_sha256", "value_sha256", "actions_sha256"}
        )
        path = value["path"]
        if not isinstance(path, list) or len(path) > 7:
            raise invalid()
        for key in ("ancestry_sha256", "name_sha256", "value_sha256", "actions_sha256"):
            if (
                not isinstance(value[key], str)
                or len(value[key]) != 64
                or any(c not in "0123456789abcdef" for c in value[key])
            ):
                raise invalid()
        return {**value, "path": [_integer(item, 0, 127) for item in path], "role": _text(value["role"], 80)}
    if isinstance(value, dict) and value.get("backend") == "ax":
        value = _object(value, {"backend", "path", "ancestry_sha256", "role", "name_sha256"})
        path = value["path"]
        if not isinstance(path, list) or len(path) > 7:
            raise invalid()
        checked = [_integer(item, 0, 127) for item in path]
        for key in ("ancestry_sha256", "name_sha256"):
            digest = value[key]
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise invalid()
        return {**value, "path": checked, "role": _text(value["role"], 80)}
    value = _object(value, {"root", "runtime", "role", "name_sha256"})
    digest = value["name_sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise invalid()
    return {
        "root": runtime_id(value["root"]),
        "runtime": runtime_id(value["runtime"]),
        "role": _text(value["role"], 80),
        "name_sha256": digest,
    }


def semantic(value: object) -> dict:
    value = _object(value, {"kind", "text", "node", "policy"})
    if _choice(value["kind"], KINDS) not in KINDS or value["policy"] != POLICY:
        raise invalid()
    text = value["text"]
    if not isinstance(text, str) or len(text) > 512 or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in text):
        raise invalid()
    try:
        text.encode("utf-8")
    except UnicodeError:
        raise invalid() from None
    if value["kind"] != "set_text" and text:
        raise invalid()
    return {"kind": value["kind"], "text": text, "node": node_proof(value["node"]), "policy": POLICY}


def linux_ready(request: dict, *, permitted: bool = False) -> dict:
    if (
        request["operation"] != "act"
        or request["target"]["platform"] != "linux"
        or request["target"]["backend"] != "atspi"
    ):
        raise invalid()
    semantic(request["action"])
    return {
        "version": request["version"],
        "request_id": request["request_id"],
        "operation": "act",
        "phase": "permit" if permitted else "ready",
    }


def owner_action(value: object) -> dict:
    foreground = isinstance(value, dict) and value.get("kind") in FOREGROUND_KINDS
    value = _object(
        value,
        {
            "consent",
            "view_id",
            "snapshot_id",
            "target_id",
            "observation_id",
            "node_id",
            "session_id",
            "request_id",
            "kind",
            "text",
        }
        | ({"takeover"} if foreground else set()),
    )
    if value["consent"] is not True:
        raise invalid()
    checked = {
        key: _uuid(value[key])
        for key in ("view_id", "snapshot_id", "target_id", "observation_id", "node_id", "session_id", "request_id")
    }
    text = value["text"]
    if foreground:
        if value["takeover"] is not True:
            raise invalid()
        return {
            "consent": True,
            **checked,
            "kind": value["kind"],
            "text": input_value(value["kind"], text),
            "takeover": True,
        }
    # Share text validation with the wire contract.
    semantic(
        {
            "kind": value["kind"],
            "text": text,
            "node": {"root": [1], "runtime": [1], "role": "x", "name_sha256": "0" * 64},
            "policy": POLICY,
        }
    )
    return {"consent": True, **checked, "kind": value["kind"], "text": text}


def action_result(value: object, kind: str | None = None) -> ActionResult:
    value = _object(value, {"status", "effect", "error_code", "verification"})
    code = value["error_code"]
    if not isinstance(code, str) or len(code) > 100 or any(c not in "abcdefghijklmnopqrstuvwxyz_" for c in code):
        raise invalid()
    status, effect, verification = value["status"], value["effect"], value["verification"]
    if not all(isinstance(item, str) for item in (status, effect, verification)):
        raise invalid()
    if (status, effect, verification) not in {
        ("ok", "applied", "value_matches"),
        ("ok", "applied", "selected"),
        ("ok", "applied", "text_appended"),
        ("denied", "none", "not_dispatched"),
        ("unknown", "unknown", "not_verified"),
    } or ((status == "ok") != (not code)):
        raise invalid()
    if (
        kind is not None
        and status == "ok"
        and verification != {"set_text": "value_matches", "select": "selected", "type_text": "text_appended"}.get(kind)
    ):
        raise invalid()
    return ActionResult(
        Outcome(status),
        "Semantic postcondition verified"
        if status == "ok"
        else "Desktop action requires independent verification"
        if status == "unknown"
        else "Desktop action was not dispatched",
        Effect(effect),
        error_code=code,
        verification=verification,
    )


SPEC = ToolSpec(
    "desktop_semantic",
    "Owner-selected native desktop action; independent approval required",
    {
        "type": "object",
        "properties": {
            "binding_id": {"type": "string"},
            "kind": {"enum": list(KINDS + FOREGROUND_KINDS)},
            "text": {"type": "string", "maxLength": 512},
            "target_application": {"type": "string"},
            "target_role": {"type": "string"},
            "policy": {"enum": [POLICY, FOREGROUND_POLICY]},
            "takeover": {"const": True},
        },
        "required": ["binding_id", "kind", "text", "target_application", "target_role", "policy"],
        "additionalProperties": False,
        "oneOf": [
            {
                "properties": {"kind": {"enum": list(KINDS)}, "policy": {"const": POLICY}},
                "not": {"required": ["takeover"]},
            },
            {
                "properties": {"kind": {"enum": list(FOREGROUND_KINDS)}, "policy": {"const": FOREGROUND_POLICY}},
                "required": ["takeover"],
            },
        ],
    },
    writes=True,
    timeout_s=20,
    resource="desktop",
)
