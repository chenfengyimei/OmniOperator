"""Canonical, bounded history cursors bind a stream and its retention generation; performs no I/O."""

from __future__ import annotations

import base64
import json
import re
import uuid
from typing import NoReturn, cast

from .contracts import BusinessError

FORMAT = "omni-history-cursor-1"
MAX_CURSOR_LENGTH = 1024
MAX_PAYLOAD_BYTES = 768
MAX_INTEGER = 2**63 - 1
KINDS = frozenset({"events", "audit", "instance_audit"})
KEYS = frozenset({"format", "epoch", "kind", "scope", "revision", "position", "filters"})


def _uuid(value: object) -> str:
    if not isinstance(value, str) or len(value) != 36 or str(uuid.UUID(value)) != value:
        raise ValueError
    return value


def _integer(value: object) -> int:
    if type(value) is not int or not 0 <= value <= MAX_INTEGER:
        raise ValueError
    return value


def _payload(
    epoch: object, kind: object, scope: object, revision: object, position: object, filters: object
) -> dict[str, object]:
    if not isinstance(kind, str) or kind not in KINDS:
        raise ValueError
    normalized: dict[str, str] = {}
    if filters is not None:
        if not isinstance(filters, dict):
            raise ValueError
        supplied = cast(dict[str, object], filters)
        if supplied:
            if kind != "events" or len(supplied) != 1 or "run_id" not in supplied:
                raise ValueError
            normalized["run_id"] = _uuid(supplied["run_id"])
    return {
        "format": FORMAT,
        "epoch": _uuid(epoch),
        "kind": kind,
        "scope": _uuid(scope),
        "revision": _integer(revision),
        "position": _integer(position),
        "filters": normalized,
    }


def _canonical(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode(
        "ascii"
    )


def _base64(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _reject_constant(value: str) -> NoReturn:
    raise ValueError


def _invalid() -> NoReturn:
    raise BusinessError("invalid_history_cursor", "Use an intact, bounded cursor for this history query", status=422)


def encode_cursor(
    epoch: str, kind: str, scope: str, revision: int, position: int, *, filters: dict[str, str] | None = None
) -> str:
    """Only emit one canonical representation; empty filters and omitted filters have the same meaning."""
    try:
        raw = _canonical(_payload(epoch, kind, scope, revision, position, filters))
        value = _base64(raw)
        if len(raw) > MAX_PAYLOAD_BYTES or len(value) > MAX_CURSOR_LENGTH:
            raise ValueError
        return value
    except (ValueError, TypeError, OverflowError, UnicodeError):
        _invalid()


def decode_cursor(
    value: str,
    *,
    epoch: str,
    kind: str,
    scope: str,
    revision: int,
    high_water: int,
    filters: dict[str, str] | None = None,
) -> int:
    """Scope misuse is invalid; changed instance or trim generations require a fresh history query."""
    try:
        expected = _payload(epoch, kind, scope, revision, 0, filters)
        _integer(high_water)
        if not 1 <= len(value) <= MAX_CURSOR_LENGTH:
            raise ValueError
        if re.fullmatch(r"[A-Za-z0-9_-]+", value) is None:
            raise ValueError
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        if len(raw) > MAX_PAYLOAD_BYTES or _base64(raw) != value:
            raise ValueError
        decoded: object = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_pairs, parse_constant=_reject_constant
        )
        if not isinstance(decoded, dict):
            raise ValueError
        fields = cast(dict[str, object], decoded)
        if set(fields) != set(KEYS) or fields["format"] != FORMAT:
            raise ValueError
        payload = _payload(
            fields["epoch"], fields["kind"], fields["scope"], fields["revision"], fields["position"], fields["filters"]
        )
        if _canonical(payload) != raw:
            raise ValueError
        position = _integer(payload["position"])
    except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError):
        _invalid()
    if any(payload[key] != expected[key] for key in ("kind", "scope", "filters")):
        raise BusinessError("history_scope_mismatch", "Cursor belongs to a different history query", status=422)
    if any(payload[key] != expected[key] for key in ("epoch", "revision")):
        raise BusinessError("history_cursor_expired", "History changed; start a fresh history query", status=410)
    if position > high_water:
        _invalid()
    return position
