"""Execution-body retention policy; time is injected, no erasure or file operations."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from collections.abc import Iterable

from .contracts import BusinessError

BODY_DAYS = 30
RETENTION_POLICY = "execution-body-v1"
PLAN_FORMAT = "omni-execution-retention-plan-1"
TERMINAL_RUNS = frozenset({"succeeded", "failed", "cancelled", "interrupted"})


def retention_cutoff(now: str) -> str:
    try:
        value = dt.datetime.fromisoformat(now)
        if value.tzinfo is None:
            raise ValueError
        return (value.astimezone(dt.timezone.utc) - dt.timedelta(days=BODY_DAYS)).isoformat(timespec="microseconds")
    except (ValueError, TypeError, OverflowError) as exc:
        raise BusinessError("invalid_retention_time", "Retention requires timezone-aware time", status=422) from exc


def retention_page(after: str, limit: int) -> None:
    try:
        valid = isinstance(after, str) and (not after or str(uuid.UUID(after)) == after)
    except (ValueError, AttributeError):
        valid = False
    if not valid or type(limit) is not int or not 1 <= limit <= 50:
        raise BusinessError("invalid_retention_query", "Use a full UUID cursor and 1 to 50 runs", status=422)


def body_fingerprint(records: Iterable[dict]) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    count = size = 0
    for record in records:
        encoded = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        count += 1
        size += len(encoded)
    return digest.hexdigest(), count, size


def validate_retention_plan(plan: dict, now: str, *, allow_future: bool = False) -> str:
    """Only bounded, explicit plans are executable; previews are never executable."""
    try:
        if set(plan) != {"format", "id", "instance_id", "schema_version", "policy", "created_at", "cutoff", "targets"}:
            raise ValueError
        if plan["format"] != PLAN_FORMAT or plan["policy"] != RETENTION_POLICY:
            raise ValueError
        for key in ("id", "instance_id"):
            if str(uuid.UUID(plan[key])) != plan[key]:
                raise ValueError
        created = dt.datetime.fromisoformat(plan["created_at"])
        cutoff = dt.datetime.fromisoformat(plan["cutoff"])
        current = dt.datetime.fromisoformat(now)
        if any(value.tzinfo is None for value in (created, cutoff, current)):
            raise ValueError
        if (created > current and not allow_future) or plan["cutoff"] != retention_cutoff(plan["created_at"]):
            raise ValueError
        if not isinstance(plan["schema_version"], str) or len(plan["schema_version"]) > 40:
            raise ValueError
        targets = plan["targets"]
        if not isinstance(targets, list) or not 1 <= len(targets) <= 50:
            raise ValueError
        seen = set()
        for target in targets:
            if set(target) != {"agent_id", "run_id", "fingerprint", "retention_revision"}:
                raise ValueError
            for key in ("agent_id", "run_id"):
                if str(uuid.UUID(target[key])) != target[key]:
                    raise ValueError
            if target["run_id"] in seen:
                raise ValueError
            seen.add(target["run_id"])
            sha = target["fingerprint"]
            if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
                raise ValueError
            if type(target["retention_revision"]) is not int or target["retention_revision"] < 1:
                raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        raise BusinessError(
            "invalid_retention_plan", "Use an intact, bounded execution-retention plan", status=422
        ) from exc
    return body_fingerprint([plan])[0]
