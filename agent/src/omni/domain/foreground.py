"""Single-use foreground authority and bounded input, independent of native APIs."""

from __future__ import annotations

from .desktop import _choice, _object, _uuid, invalid

POLICY = "desktop-foreground-1"
KINDS = ("type_text", "press_key", "click")
KEYS = ("Enter", "Tab", "Escape", "Backspace", "Delete", "Home", "End", "Left", "Right", "Up", "Down")
TIMEOUT_S = 15.0


def input_value(kind: object, value: object) -> str:
    kind = _choice(kind, KINDS)
    if not isinstance(value, str):
        raise invalid()
    if kind == "press_key":
        return _choice(value, KEYS)
    if kind == "click":
        if value:
            raise invalid()
        return ""
    if not 1 <= len(value) <= 128 or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value):
        raise invalid()
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise invalid() from None
    return value


def action(value: object) -> dict:
    from .desktop_actions import node_proof

    value = _object(value, {"kind", "text", "node", "policy", "takeover", "grant_id", "run_id", "operation_id"})
    if value["takeover"] is not True or value["policy"] != POLICY:
        raise invalid()
    return {
        "kind": _choice(value["kind"], KINDS),
        "text": input_value(value["kind"], value["text"]),
        "node": node_proof(value["node"]),
        "policy": POLICY,
        "takeover": True,
        **{key: _uuid(value[key]) for key in ("grant_id", "run_id", "operation_id")},
    }


def ready(request: dict) -> dict:
    return {
        "version": request["version"],
        "request_id": request["request_id"],
        "operation": "foreground",
        "phase": "ready",
        **{key: request["action"][key] for key in ("grant_id", "run_id", "operation_id")},
    }


def permit(request: dict) -> dict:
    return {**ready(request), "phase": "permit"}
