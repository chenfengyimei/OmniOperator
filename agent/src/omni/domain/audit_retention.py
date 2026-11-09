"""Agent audit-retention subjects, frozen previews and explicit plans; performs no I/O or deletion."""

from __future__ import annotations

import base64
import datetime as dt
import json
import re
import uuid
from collections.abc import Mapping
from types import MappingProxyType
from typing import NoReturn, TypedDict, cast

from .contracts import BusinessError
from .history import MAX_CURSOR_LENGTH, MAX_INTEGER
from .retention import body_fingerprint

AUDIT_DAYS = 180
POLICY = "agent-audit-retention-v1"
FORMAT = "omni-audit-retention-preview-1"
PLAN_FORMAT = "omni-audit-retention-plan-1"
SUBJECTS: Mapping[str, str] = MappingProxyType(
    {
        "run.completed": "run",
        "action.ok": "action",
        "action.error": "action",
        "action.denied": "action",
        "action.cancelled": "action",
    }
)
KEYS = frozenset({"format", "policy", "epoch", "agent_id", "revision", "as_of", "high_water", "position"})
MAX_PAYLOAD_BYTES = 768
PLAN_KEYS = frozenset(
    {
        "format",
        "id",
        "instance_id",
        "agent_id",
        "schema_version",
        "policy",
        "created_at",
        "as_of",
        "cutoff",
        "epoch",
        "trim_revision",
        "high_water",
        "targets",
    }
)
TARGET_IDS = ("audit_id", "agent_id", "operation_id", "run_id", "prune_plan_id")
TARGET_KEYS = frozenset(
    {*TARGET_IDS, "sequence", "kind", "subject_kind", "run_retention_revision", "body_sha256", "fingerprint"}
)


class PreviewPosition(TypedDict):
    as_of: str
    cutoff: str
    high_water: int
    position: int


def _time(value: object) -> dt.datetime:
    if not isinstance(value, str) or not 1 <= len(value) <= 40:
        raise ValueError
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError
    return parsed.astimezone(dt.timezone.utc)


def preview_time(as_of: str, now: str) -> tuple[str, str]:
    """Use the same fixed UTC instant on every page; a clock rollback requires a new preview."""
    try:
        point, current = _time(as_of), _time(now)
        if point > current:
            raise ValueError
        cutoff = point - dt.timedelta(days=AUDIT_DAYS)
        return point.isoformat(timespec="microseconds"), cutoff.isoformat(timespec="microseconds")
    except (ValueError, TypeError, OverflowError) as exc:
        raise BusinessError(
            "invalid_audit_retention_time",
            "Audit retention requires a timezone-aware preview time not after now",
            status=422,
        ) from exc


def _identity(value: object) -> str:
    if not isinstance(value, str) or len(value) != 36 or str(uuid.UUID(value)) != value:
        raise ValueError
    return value


def _position(value: object) -> int:
    if type(value) is not int or not 0 <= value <= MAX_INTEGER:
        raise ValueError
    return value


def _payload(
    epoch: object, agent_id: object, revision: object, as_of: object, high_water: object, position: object
) -> dict[str, object]:
    if not isinstance(as_of, str):
        raise ValueError
    canonical_as_of, _ = preview_time(as_of, as_of)
    cap, after = _position(high_water), _position(position)
    if after > cap:
        raise ValueError
    return {
        "format": FORMAT,
        "policy": POLICY,
        "epoch": _identity(epoch),
        "agent_id": _identity(agent_id),
        "revision": _position(revision),
        "as_of": canonical_as_of,
        "high_water": cap,
        "position": after,
    }


def _canonical(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode(
        "ascii"
    )


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


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
    raise BusinessError(
        "invalid_audit_retention_cursor", "Use an intact, bounded cursor for this audit-retention preview", status=422
    )


def encode_preview_cursor(epoch: str, agent_id: str, revision: int, as_of: str, high_water: int, position: int) -> str:
    """Bind a preview to its original time, global sequence ceiling, Agent and retention generation."""
    try:
        raw = _canonical(_payload(epoch, agent_id, revision, as_of, high_water, position))
        value = _encode(raw)
        if len(raw) > MAX_PAYLOAD_BYTES or len(value) > MAX_CURSOR_LENGTH:
            raise ValueError
        return value
    except (ValueError, TypeError, OverflowError, UnicodeError):
        _invalid()


def decode_preview_cursor(
    value: str,
    *,
    epoch: str,
    agent_id: str,
    revision: int,
    current_high_water: int,
    now: str,
    as_of: str = "",
) -> PreviewPosition:
    """New audit rows do not move the ceiling; trimming and restoration invalidate the preview."""
    try:
        expected_epoch, expected_agent = _identity(epoch), _identity(agent_id)
        expected_revision, current_cap = _position(revision), _position(current_high_water)
        if not isinstance(as_of, str) or not isinstance(value, str) or not 1 <= len(value) <= MAX_CURSOR_LENGTH:
            raise ValueError
        if re.fullmatch(r"[A-Za-z0-9_-]+", value) is None:
            raise ValueError
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        if len(raw) > MAX_PAYLOAD_BYTES or _encode(raw) != value:
            raise ValueError
        decoded: object = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_pairs, parse_constant=_reject_constant
        )
        if not isinstance(decoded, dict):
            raise ValueError
        fields = cast(dict[str, object], decoded)
        if set(fields) != KEYS or fields["format"] != FORMAT or fields["policy"] != POLICY:
            raise ValueError
        payload = _payload(
            fields["epoch"],
            fields["agent_id"],
            fields["revision"],
            fields["as_of"],
            fields["high_water"],
            fields["position"],
        )
        if _canonical(payload) != raw:
            raise ValueError
        point = cast(str, payload["as_of"])
        cap, position = _position(payload["high_water"]), _position(payload["position"])
    except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError):
        _invalid()
    canonical_as_of, cutoff = preview_time(point, now)
    if payload["agent_id"] != expected_agent or (as_of and preview_time(as_of, now)[0] != canonical_as_of):
        raise BusinessError(
            "history_scope_mismatch", "Cursor belongs to a different audit-retention preview", status=422
        )
    if payload["epoch"] != expected_epoch or payload["revision"] != expected_revision:
        raise BusinessError(
            "history_cursor_expired", "History changed; start a fresh audit-retention preview", status=410
        )
    if cap > current_cap:
        _invalid()
    return {"as_of": canonical_as_of, "cutoff": cutoff, "high_water": cap, "position": position}


def validate_audit_plan(plan: dict, now: str, *, allow_future: bool = False) -> str:
    """Cancellation tolerates clock rollback; ownership, fixed time and target integrity remain mandatory."""
    try:
        if not isinstance(plan, dict) or set(plan) != PLAN_KEYS or type(allow_future) is not bool:
            raise ValueError
        if plan["format"] != PLAN_FORMAT or plan["policy"] != POLICY:
            raise ValueError
        for key in ("id", "instance_id", "agent_id", "epoch"):
            _identity(plan[key])
        version = plan["schema_version"]
        if not isinstance(version, str) or not 1 <= len(version) <= 40:
            raise ValueError
        created, point, cutoff, current = (
            _time(plan["created_at"]),
            _time(plan["as_of"]),
            _time(plan["cutoff"]),
            _time(now),
        )
        for key, value in (("created_at", created), ("as_of", point), ("cutoff", cutoff)):
            if plan[key] != value.isoformat(timespec="microseconds"):
                raise ValueError
        if (
            point > created
            or (created > current and not allow_future)
            or cutoff != point - dt.timedelta(days=AUDIT_DAYS)
        ):
            raise ValueError
        _position(plan["trim_revision"])
        cap = _position(plan["high_water"])
        targets = plan["targets"]
        if not isinstance(targets, list) or not 1 <= len(targets) <= 50:
            raise ValueError
        seen: set[str] = set()
        previous = 0
        for target in targets:
            if not isinstance(target, dict) or set(target) != TARGET_KEYS:
                raise ValueError
            for key in TARGET_IDS:
                _identity(target[key])
            if target["agent_id"] != plan["agent_id"] or target["audit_id"] in seen:
                raise ValueError
            seen.add(target["audit_id"])
            sequence = _position(target["sequence"])
            if not previous < sequence <= cap:
                raise ValueError
            previous = sequence
            if _position(target["run_retention_revision"]) < 1:
                raise ValueError
            kind = target["kind"]
            if not isinstance(kind, str) or kind not in SUBJECTS or target["subject_kind"] != SUBJECTS[kind]:
                raise ValueError
            if target["subject_kind"] == "run" and target["operation_id"] != target["run_id"]:
                raise ValueError
            for key in ("body_sha256", "fingerprint"):
                digest = target[key]
                if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                    raise ValueError
        return body_fingerprint([plan])[0]
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, UnicodeError) as exc:
        raise BusinessError(
            "audit_retention_invalid", "Use an intact, bounded Agent audit-retention plan", status=422
        ) from exc
