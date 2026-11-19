"""Bounded event-retention previews and explicit plans; performs no I/O, deletion or sequence reconstruction."""

from __future__ import annotations

import base64
import datetime as dt
import json
import re
import uuid
from typing import NoReturn, TypedDict, cast

from .contracts import BusinessError
from .history import MAX_CURSOR_LENGTH, MAX_INTEGER
from .retention import body_fingerprint

EVENT_DAYS = 180
POLICY = "agent-event-retention-v1"
FORMAT = "omni-event-retention-preview-1"
PLAN_FORMAT = "omni-event-retention-plan-1"
KINDS = frozenset(
    {
        "run.queued",
        "run.running",
        "run.waiting_approval",
        "run.cancelling",
        "run.succeeded",
        "run.failed",
        "run.cancelled",
        "action.prepared",
        "action.completed",
        "model.completed",
    }
)
KEYS = frozenset({"format", "policy", "epoch", "agent_id", "revision", "as_of", "high_water", "position"})
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
TARGET_IDS = ("agent_id", "run_id", "prune_plan_id")
TARGET_KEYS = frozenset(
    {*TARGET_IDS, "sequence", "seq", "kind", "run_retention_revision", "body_sha256", "fingerprint"}
)
MAX_PAYLOAD_BYTES = 768


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
    """Freeze the UTC cutoff once; clock rollback cannot make an existing preview younger."""
    try:
        point, current = _time(as_of), _time(now)
        if point > current:
            raise ValueError
        cutoff = point - dt.timedelta(days=EVENT_DAYS)
        return point.isoformat(timespec="microseconds"), cutoff.isoformat(timespec="microseconds")
    except (ValueError, TypeError, OverflowError) as exc:
        raise BusinessError(
            "invalid_event_retention_time",
            "Event retention requires a timezone-aware preview time not after now",
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
    point, _ = preview_time(as_of, as_of)
    cap, after = _position(high_water), _position(position)
    if after > cap:
        raise ValueError
    return {
        "format": FORMAT,
        "policy": POLICY,
        "epoch": _identity(epoch),
        "agent_id": _identity(agent_id),
        "revision": _position(revision),
        "as_of": point,
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
    raise BusinessError("event_retention_invalid", "Use an intact, bounded event-retention cursor or plan", status=422)


def encode_preview_cursor(epoch: str, agent_id: str, revision: int, as_of: str, high_water: int, position: int) -> str:
    """Bind the events scope, initial sequence ceiling and fixed preview time independently of audit history."""
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
    """New events do not move the ceiling; deletion generations and restoration require a fresh preview."""
    try:
        expected_epoch, expected_agent = _identity(epoch), _identity(agent_id)
        expected_revision, current_cap = _position(revision), _position(current_high_water)
        if (
            not isinstance(cast(object, as_of), str)
            or not isinstance(cast(object, value), str)
            or not 1 <= len(value) <= MAX_CURSOR_LENGTH
        ):
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
        if set(fields) != set(KEYS) or fields["format"] != FORMAT or fields["policy"] != POLICY:
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
            "history_scope_mismatch", "Cursor belongs to a different event-retention preview", status=422
        )
    if payload["epoch"] != expected_epoch or payload["revision"] != expected_revision:
        raise BusinessError(
            "history_cursor_expired", "History changed; start a fresh event-retention preview", status=410
        )
    if cap > current_cap:
        _invalid()
    return {"as_of": canonical_as_of, "cutoff": cutoff, "high_water": cap, "position": position}


def validate_event_plan(plan: dict[str, object], now: str, *, allow_future: bool = False) -> str:
    """Cancellation tolerates wall-clock rollback without relaxing event identity, provenance or ordering."""
    try:
        if set(plan) != set(PLAN_KEYS) or type(allow_future) is not bool:
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
            or cutoff != point - dt.timedelta(days=EVENT_DAYS)
        ):
            raise ValueError
        _position(plan["trim_revision"])
        cap = _position(plan["high_water"])
        targets = plan["targets"]
        if not isinstance(targets, list):
            raise ValueError
        rows = cast(list[object], targets)
        if not 1 <= len(rows) <= 50:
            raise ValueError
        seen: set[tuple[str, int]] = set()
        previous = 0
        for target in rows:
            if not isinstance(target, dict):
                raise ValueError
            entry = cast(dict[str, object], target)
            if set(entry) != set(TARGET_KEYS):
                raise ValueError
            for key in TARGET_IDS:
                _identity(entry[key])
            if entry["agent_id"] != plan["agent_id"]:
                raise ValueError
            sequence, seq = _position(entry["sequence"]), _position(entry["seq"])
            if not previous < sequence <= cap or seq < 1 or _position(entry["run_retention_revision"]) < 1:
                raise ValueError
            previous = sequence
            identity = (_identity(entry["run_id"]), seq)
            if identity in seen:
                raise ValueError
            seen.add(identity)
            kind = entry["kind"]
            if not isinstance(kind, str) or kind not in KINDS:
                raise ValueError
            for key in ("body_sha256", "fingerprint"):
                digest = entry[key]
                if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                    raise ValueError
        return body_fingerprint([plan])[0]
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, UnicodeError) as exc:
        raise BusinessError(
            "event_retention_invalid", "Use an intact, bounded Agent event-retention plan", status=422
        ) from exc
