"""Bounded artifact-reference release plans; validates identities without accessing storage or files."""

from __future__ import annotations

import datetime as dt
import uuid

from .contracts import BusinessError
from .retention import body_fingerprint

POLICY = "execution-artifact-reference-v1"
FORMAT = "omni-retention-reference-plan-1"
PLAN_KEYS = frozenset({"format", "id", "instance_id", "schema_version", "policy", "created_at", "targets"})
TARGET_IDS = ("agent_id", "run_id", "action_id", "reference_id", "object_id", "prune_plan_id")
TARGET_KEYS = frozenset({*TARGET_IDS, "object_revision", "object_sha256", "object_bytes", "fingerprint"})


def _uuid(value: object) -> None:
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError


def validate_reference_plan(plan: dict, now: str, *, allow_future: bool = False) -> str:
    """Cancellation may tolerate clock rollback; all structural checks remain mandatory."""
    try:
        if not isinstance(plan, dict) or set(plan) != PLAN_KEYS or type(allow_future) is not bool:
            raise ValueError
        if plan["format"] != FORMAT or plan["policy"] != POLICY:
            raise ValueError
        _uuid(plan["id"])
        _uuid(plan["instance_id"])
        if not isinstance(plan["schema_version"], str) or not 1 <= len(plan["schema_version"]) <= 40:
            raise ValueError
        created = dt.datetime.fromisoformat(plan["created_at"])
        current = dt.datetime.fromisoformat(now)
        if created.tzinfo is None or current.tzinfo is None or (created > current and not allow_future):
            raise ValueError
        targets = plan["targets"]
        if not isinstance(targets, list) or not 1 <= len(targets) <= 50:
            raise ValueError
        seen = set()
        for target in targets:
            if not isinstance(target, dict) or set(target) != TARGET_KEYS:
                raise ValueError
            for key in TARGET_IDS:
                _uuid(target[key])
            if target["reference_id"] in seen:
                raise ValueError
            seen.add(target["reference_id"])
            if type(target["object_revision"]) is not int or target["object_revision"] < 1:
                raise ValueError
            if type(target["object_bytes"]) is not int or not 0 <= target["object_bytes"] <= 2**63 - 1:
                raise ValueError
            for key in ("fingerprint", "object_sha256"):
                digest = target[key]
                if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                    raise ValueError
        return body_fingerprint([plan])[0]
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, UnicodeError) as exc:
        raise BusinessError(
            "reference_release_invalid", "Use an intact, bounded artifact-reference release plan", status=422
        ) from exc
