"""Single-use foreground authority and bounded input, independent of native APIs."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import cast

from .contracts import BusinessError

POLICY = "desktop-foreground-1"
KINDS = ("type_text", "press_key", "click")
KEYS = ("Enter", "Tab", "Escape", "Backspace", "Delete", "Home", "End", "Left", "Right", "Up", "Down")
FIELDS = frozenset({"kind", "text", "node", "policy", "takeover", "grant_id", "run_id", "operation_id"})
TIMEOUT_S = 15.0


def _invalid() -> BusinessError:
    return BusinessError("desktop_invalid", "Use an exact, bounded desktop identity and discovery protocol", status=422)


def _choice(value: object, choices: Sequence[str]) -> str:
    if not isinstance(value, str) or value not in choices:
        raise _invalid()
    return value


def _text(value: object, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value)
    ):
        raise _invalid()
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise _invalid() from exc
    return value


def _fields(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise _invalid()
    data = cast(dict[object, object], value)
    keys = {key for key in data if isinstance(key, str)}
    if len(keys) != len(data) or keys != set(FIELDS):
        raise _invalid()
    return {key: data[key] for key in keys}


def _uuid(value: object) -> str:
    text = _text(value, 36)
    try:
        if str(uuid.UUID(text)) != text:
            raise _invalid()
    except ValueError as exc:
        raise _invalid() from exc
    return text


def input_value(kind: object, value: object) -> str:
    selected = _choice(kind, KINDS)
    if not isinstance(value, str):
        raise _invalid()
    if selected == "press_key":
        return _choice(value, KEYS)
    if selected == "click":
        if value:
            raise _invalid()
        return ""
    return _text(value, 128)


def action(value: object) -> dict[str, object]:
    from .desktop_actions import node_proof

    record = _fields(value)
    if record["takeover"] is not True or record["policy"] != POLICY:
        raise _invalid()
    kind = _choice(record["kind"], KINDS)
    return {
        "kind": kind,
        "text": input_value(kind, record["text"]),
        "node": node_proof(record["node"]),
        "policy": POLICY,
        "takeover": True,
        **{key: _uuid(record[key]) for key in ("grant_id", "run_id", "operation_id")},
    }


def ready(request: Mapping[str, object]) -> dict[str, object]:
    nested = request.get("action")
    if not isinstance(nested, Mapping):
        raise _invalid()
    action_value = cast(Mapping[str, object], nested)
    return {
        "version": request.get("version"),
        "request_id": _uuid(request.get("request_id")),
        "operation": "foreground",
        "phase": "ready",
        **{key: _uuid(action_value.get(key)) for key in ("grant_id", "run_id", "operation_id")},
    }


def permit(request: Mapping[str, object]) -> dict[str, object]:
    return {**ready(request), "phase": "permit"}
