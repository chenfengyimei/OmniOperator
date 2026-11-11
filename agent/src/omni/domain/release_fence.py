"""Persisted startup exclusion contract shared by immutable installations; no IO or mutable state."""

from __future__ import annotations

import re
import uuid
from typing import cast

from .contracts import BusinessError

FORMAT = "omni-release-fence-1"
FIELDS = frozenset({"format", "release_id", "plan_sha256", "role"})
ROLES = frozenset({"source", "target"})


def _invalid() -> BusinessError:
    return BusinessError("release_fence_invalid", "Use the exact reviewed release startup fence", status=422)


def _text(value: object, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value)
    ):
        raise _invalid()
    return value


def _fields(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise _invalid()
    data = cast(dict[object, object], value)
    keys = {key for key in data if isinstance(key, str)}
    if len(keys) != len(data) or keys != set(FIELDS):
        raise _invalid()
    return {key: data[key] for key in keys}


def _release(value: object) -> str:
    text = _text(value, 36)
    try:
        if str(uuid.UUID(text)) != text:
            raise _invalid()
    except ValueError as exc:
        raise _invalid() from exc
    return text


def _digest(value: object) -> str:
    text = _text(value, 64)
    if re.fullmatch(r"[0-9a-f]{64}", text) is None:
        raise _invalid()
    return text


def fence(identity: str, sha256: str, role: str) -> dict[str, str]:
    if role not in ROLES:
        raise _invalid()
    return {
        "format": FORMAT,
        "release_id": _release(identity),
        "plan_sha256": _digest(sha256),
        "role": role,
    }


def validate_fence(value: object) -> dict[str, str]:
    record = _fields(value)
    role = record["role"]
    expected = fence(
        _release(record["release_id"]),
        _digest(record["plan_sha256"]),
        role if isinstance(role, str) else "",
    )
    if record != expected:
        raise _invalid()
    return expected
