"""Exact semantic action contracts; observations never grant write authority."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from .contracts import ActionResult, Effect, Outcome, ToolSpec
from .desktop import desktop_choice, desktop_integer, desktop_object, desktop_text, desktop_uuid, invalid
from .foreground import KINDS as FOREGROUND_KINDS
from .foreground import POLICY as FOREGROUND_POLICY
from .foreground import input_value

POLICY = "desktop-semantic-1"
KINDS = ("invoke", "select", "set_text")


def runtime_id(value: object) -> list[int]:
    if not isinstance(value, list):
        raise invalid()
    rows = cast(list[object], value)
    if not 1 <= len(rows) <= 32:
        raise invalid()
    return [desktop_integer(item, -2147483648, 2147483647) for item in rows]


def node_proof(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        fields = cast(dict[str, object], value)
        if fields.get("backend") == "atspi":
            checked = desktop_object(
                fields,
                {"backend", "path", "ancestry_sha256", "role", "name_sha256", "value_sha256", "actions_sha256"},
            )
            path = checked["path"]
            if not isinstance(path, list):
                raise invalid()
            steps = cast(list[object], path)
            if len(steps) > 7:
                raise invalid()
            for key in ("ancestry_sha256", "name_sha256", "value_sha256", "actions_sha256"):
                digest = checked[key]
                if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                    raise invalid()
            return {
                **checked,
                "path": [desktop_integer(item, 0, 127) for item in steps],
                "role": desktop_text(checked["role"], 80),
            }
        if fields.get("backend") == "ax":
            checked = desktop_object(fields, {"backend", "path", "ancestry_sha256", "role", "name_sha256"})
            path = checked["path"]
            if not isinstance(path, list):
                raise invalid()
            steps = cast(list[object], path)
            if len(steps) > 7:
                raise invalid()
            parsed = [desktop_integer(item, 0, 127) for item in steps]
            for key in ("ancestry_sha256", "name_sha256"):
                digest = checked[key]
                if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                    raise invalid()
            return {**checked, "path": parsed, "role": desktop_text(checked["role"], 80)}
    checked = desktop_object(cast(object, value), {"root", "runtime", "role", "name_sha256"})
    digest = checked["name_sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise invalid()
    return {
        "root": runtime_id(checked["root"]),
        "runtime": runtime_id(checked["runtime"]),
        "role": desktop_text(checked["role"], 80),
        "name_sha256": digest,
    }


def semantic(value: object) -> dict[str, object]:
    checked = desktop_object(value, {"kind", "text", "node", "policy"})
    if desktop_choice(checked["kind"], KINDS) not in KINDS or checked["policy"] != POLICY:
        raise invalid()
    text = checked["text"]
    if not isinstance(text, str) or len(text) > 512 or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in text):
        raise invalid()
    try:
        text.encode("utf-8")
    except UnicodeError:
        raise invalid() from None
    if checked["kind"] != "set_text" and text:
        raise invalid()
    return {"kind": checked["kind"], "text": text, "node": node_proof(checked["node"]), "policy": POLICY}


def linux_ready(request: Mapping[str, object], *, permitted: bool = False) -> dict[str, object]:
    target = request["target"]
    if not isinstance(target, dict):
        raise invalid()
    fields = cast(dict[str, object], target)
    if request["operation"] != "act" or fields.get("platform") != "linux" or fields.get("backend") != "atspi":
        raise invalid()
    semantic(request["action"])
    return {
        "version": request["version"],
        "request_id": request["request_id"],
        "operation": "act",
        "phase": "permit" if permitted else "ready",
    }


def owner_action(value: object) -> dict[str, object]:
    source = cast(dict[str, object], value) if isinstance(value, dict) else None
    foreground = source is not None and source.get("kind") in FOREGROUND_KINDS
    checked = desktop_object(
        cast(object, value),
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
    if checked["consent"] is not True:
        raise invalid()
    identities = {
        key: desktop_uuid(checked[key])
        for key in ("view_id", "snapshot_id", "target_id", "observation_id", "node_id", "session_id", "request_id")
    }
    text = checked["text"]
    if foreground:
        if checked["takeover"] is not True:
            raise invalid()
        return {
            "consent": True,
            **identities,
            "kind": checked["kind"],
            "text": input_value(checked["kind"], text),
            "takeover": True,
        }
    # Share text validation with the wire contract.
    semantic(
        {
            "kind": checked["kind"],
            "text": text,
            "node": {"root": [1], "runtime": [1], "role": "x", "name_sha256": "0" * 64},
            "policy": POLICY,
        }
    )
    return {"consent": True, **identities, "kind": checked["kind"], "text": text}


def action_result(value: object, kind: str | None = None) -> ActionResult:
    checked = desktop_object(value, {"status", "effect", "error_code", "verification"})
    code = checked["error_code"]
    if not isinstance(code, str) or len(code) > 100 or any(c not in "abcdefghijklmnopqrstuvwxyz_" for c in code):
        raise invalid()
    raw_status, raw_effect, raw_verification = checked["status"], checked["effect"], checked["verification"]
    if not all(isinstance(item, str) for item in (raw_status, raw_effect, raw_verification)):
        raise invalid()
    status = cast(str, raw_status)
    effect = cast(str, raw_effect)
    verification = cast(str, raw_verification)
    if (status, effect, verification) not in {
        ("ok", "applied", "value_matches"),
        ("ok", "applied", "selected"),
        ("ok", "applied", "text_appended"),
        ("ok", "applied", "key_verified"),
        ("denied", "none", "not_dispatched"),
        ("unknown", "unknown", "not_verified"),
    } or ((status == "ok") != (not code)):
        raise invalid()
    if (
        kind is not None
        and status == "ok"
        and verification
        != {
            "set_text": "value_matches",
            "select": "selected",
            "type_text": "text_appended",
            "press_key": "key_verified",
        }.get(kind)
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
