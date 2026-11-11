"""Execution-body retention policy; time is injected, no erasure or file operations."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from collections.abc import Iterable, Mapping
from typing import cast

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
        valid = not after or str(uuid.UUID(after)) == after
    except (ValueError, AttributeError):
        valid = False
    if not valid or type(limit) is not int or not 1 <= limit <= 50:
        raise BusinessError("invalid_retention_query", "Use a full UUID cursor and 1 to 50 runs", status=422)


def body_fingerprint(records: Iterable[Mapping[str, object]]) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    count = size = 0
    for record in records:
        encoded = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        count += 1
        size += len(encoded)
    return digest.hexdigest(), count, size


def validate_retention_plan(plan: dict[str, object], now: str, *, allow_future: bool = False) -> str:
    """Only bounded, explicit plans are executable; previews are never executable."""
    try:
        if set(plan) != {"format", "id", "instance_id", "schema_version", "policy", "created_at", "cutoff", "targets"}:
            raise ValueError
        if plan["format"] != PLAN_FORMAT or plan["policy"] != RETENTION_POLICY:
            raise ValueError
        for key in ("id", "instance_id"):
            value = cast(str, plan[key])
            if str(uuid.UUID(value)) != value:
                raise ValueError
        created_at = cast(str, plan["created_at"])
        created = dt.datetime.fromisoformat(created_at)
        cutoff = dt.datetime.fromisoformat(cast(str, plan["cutoff"]))
        current = dt.datetime.fromisoformat(now)
        if any(value.tzinfo is None for value in (created, cutoff, current)):
            raise ValueError
        if (created > current and not allow_future) or plan["cutoff"] != retention_cutoff(created_at):
            raise ValueError
        schema_version = plan["schema_version"]
        if not isinstance(schema_version, str) or len(schema_version) > 40:
            raise ValueError
        raw_targets = plan["targets"]
        if not isinstance(raw_targets, list):
            raise ValueError
        targets = cast(list[dict[str, object]], raw_targets)
        if not 1 <= len(targets) <= 50:
            raise ValueError
        seen: set[str] = set()
        for target in targets:
            if set(target) != {"agent_id", "run_id", "fingerprint", "retention_revision"}:
                raise ValueError
            for key in ("agent_id", "run_id"):
                value = cast(str, target[key])
                if str(uuid.UUID(value)) != value:
                    raise ValueError
            run_id = cast(str, target["run_id"])
            if run_id in seen:
                raise ValueError
            seen.add(run_id)
            sha = cast(str, target["fingerprint"])
            if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
                raise ValueError
            revision = cast(int, target["retention_revision"])
            if type(target["retention_revision"]) is not int or revision < 1:
                raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        raise BusinessError(
            "invalid_retention_plan", "Use an intact, bounded execution-retention plan", status=422
        ) from exc
    return body_fingerprint([plan])[0]
