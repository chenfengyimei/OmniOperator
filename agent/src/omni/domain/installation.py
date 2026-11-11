"""Immutable local installation receipt written once per home; no I/O and no upgrade execution."""

from __future__ import annotations

import datetime as dt
import re
from typing import cast

from .contracts import BusinessError

FORMAT = "omni-installation-1"
FIELDS = frozenset({"format", "created_at", "schema_version", "code_head"})
SCHEMA = re.compile(r"v2_[0-9]{4}")


def invalid() -> BusinessError:
    return BusinessError("installation_invalid", "Use an exact, bounded installation receipt", status=422)


def _text(value: object, maximum: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum or any(ord(c) < 32 for c in value):
        raise invalid()
    return value


def _schema(value: object) -> str:
    text = _text(value, 8)
    if SCHEMA.fullmatch(text) is None:
        raise invalid()
    return text


def _created(value: object) -> str:
    text = _text(value, 40)
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError as exc:
        raise invalid() from exc
    if parsed.tzinfo is None:
        raise invalid()
    return text


def make_installation(created_at: object, schema_version: object, code_head: object) -> dict[str, str]:
    return {
        "format": FORMAT,
        "created_at": _created(created_at),
        "schema_version": _schema(schema_version),
        "code_head": _schema(code_head),
    }


def validate_installation(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise invalid()
    data = cast(dict[object, object], value)
    keys = {key for key in data if isinstance(key, str)}
    if len(keys) != len(data) or keys != set(FIELDS):
        raise invalid()
    expected = make_installation(data["created_at"], data["schema_version"], data["code_head"])
    if data["format"] != FORMAT or data != expected:
        raise invalid()
    return expected
