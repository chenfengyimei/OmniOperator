"""Explicit owner terminal requests and metadata identities; no model tools, shell execution or transcript storage."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import cast

from .contracts import BusinessError
from .history import MAX_INTEGER

MAX_INPUT_BYTES = 16_384
SHELLS = frozenset({"powershell", "pwsh", "cmd", "bash", "zsh", "sh"})
KINDS = frozenset({"open", "input", "resize", "close"})
OPERATION_STATES = frozenset({"prepared", "accepted", "not_sent", "unknown"})
SESSION_STATES = frozenset({"starting", "running", "closing", "exited", "unknown"})


def invalid() -> BusinessError:
    return BusinessError("terminal_invalid", "Use an exact, bounded owner terminal request", status=422)


def identity(value: object) -> str:
    try:
        if not isinstance(value, str) or len(value) != 36 or str(uuid.UUID(value)) != value:
            raise ValueError
        return value
    except (ValueError, TypeError, AttributeError) as exc:
        raise invalid() from exc


def integer(value: object, minimum: int = 0, maximum: int = MAX_INTEGER) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise invalid()
    return value


def geometry(cols: object, rows: object) -> tuple[int, int]:
    return integer(cols, 20, 500), integer(rows, 5, 200)


def digest(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise invalid()
    return value


def _exact(data: object, keys: set[str]) -> dict[str, object]:
    if not isinstance(data, dict):
        raise invalid()
    value = cast(dict[str, object], data)
    if set(value) != keys:
        raise invalid()
    return value


def parse_open(data: object) -> dict[str, object]:
    value = _exact(data, {"operation_id", "shell", "cols", "rows", "trust"})
    shell = value["shell"]
    if value["trust"] is not True or not isinstance(shell, str) or shell not in SHELLS:
        raise invalid()
    cols, rows = geometry(value["cols"], value["rows"])
    return {
        "operation_id": identity(value["operation_id"]),
        "shell": shell,
        "cols": cols,
        "rows": rows,
        "trust": True,
    }


def parse_input(data: object) -> dict[str, object]:
    value = _exact(data, {"operation_id", "connection_id", "data"})
    text = value["data"]
    try:
        if not isinstance(text, str) or not 1 <= len(text.encode("utf-8")) <= MAX_INPUT_BYTES:
            raise ValueError
        if len(json.dumps(text, ensure_ascii=False).encode("utf-8")) > 49152:
            raise ValueError
    except (ValueError, TypeError, UnicodeError) as exc:
        raise invalid() from exc
    return {
        "operation_id": identity(value["operation_id"]),
        "connection_id": identity(value["connection_id"]),
        "data": text,
    }


def parse_resize(data: object) -> dict[str, object]:
    value = _exact(data, {"operation_id", "connection_id", "cols", "rows"})
    cols, rows = geometry(value["cols"], value["rows"])
    return {
        "operation_id": identity(value["operation_id"]),
        "connection_id": identity(value["connection_id"]),
        "cols": cols,
        "rows": rows,
    }


def parse_close(data: object) -> dict[str, object]:
    value = _exact(data, {"operation_id", "revision"})
    return {"operation_id": identity(value["operation_id"]), "revision": integer(value["revision"], 1)}


def payload_fingerprint(kind: str, data: object) -> str:
    parsers = {"open": parse_open, "input": parse_input, "resize": parse_resize, "close": parse_close}
    if kind not in parsers:
        raise invalid()
    normalized = parsers[kind](data)
    raw = json.dumps(
        {"kind": kind, "request": normalized},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def page(after: int = 0, limit: int = 20) -> None:
    integer(after)
    integer(limit, 1, 50)
