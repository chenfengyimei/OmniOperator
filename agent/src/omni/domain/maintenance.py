"""Bounded local maintenance requests, ownership claims and lease times; no ORM, I/O or execution."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import unicodedata
import uuid
from dataclasses import dataclass

from .contracts import BusinessError
from .documents import TEXT_INDEX_VERSION
from .history import MAX_INTEGER

MAX_PENDING = 64
MAX_ACTIVE = 2
MAX_ATTEMPTS = 3
LEASE_SECONDS = 60
ACTIVE = frozenset({"queued", "claimed", "running", "cancelling"})
TERMINAL = frozenset({"succeeded", "failed", "cancelled"})
KINDS = frozenset({"document_index_rebuild", "document_erasure_resume"})
INDEX_KIND = "full_text"
INDEX_VERSION = TEXT_INDEX_VERSION
INDEX_KEYS = frozenset({"kind", "key", "document_id", "revision"})
ERASURE_KEYS = frozenset({"kind", "key", "erasure_job_id"})


def _uuid(value: object) -> str:
    if not isinstance(value, str) or len(value) != 36 or str(uuid.UUID(value)) != value:
        raise ValueError
    return value


def parse_request(data: object) -> dict:
    """Only queue an exact document revision or resume an existing erasure identity; never carry source text."""
    try:
        if not isinstance(data, dict):
            raise ValueError
        kind = data.get("kind")
        if not isinstance(kind, str) or kind not in KINDS:
            raise ValueError
        if set(data) != (INDEX_KEYS if kind == "document_index_rebuild" else ERASURE_KEYS):
            raise ValueError
        key = data["key"]
        if (
            not isinstance(key, str)
            or not 1 <= len(key) <= 200
            or not key.strip()
            or any(unicodedata.category(char) == "Cc" for char in key)
        ):
            raise ValueError
        key.encode("utf-8")
        if kind == "document_index_rebuild":
            revision = data["revision"]
            if type(revision) is not int or not 1 <= revision <= MAX_INTEGER:
                raise ValueError
            return {"kind": kind, "key": key, "document_id": _uuid(data["document_id"]), "revision": revision}
        return {"kind": kind, "key": key, "erasure_job_id": _uuid(data["erasure_job_id"])}
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, UnicodeError) as exc:
        raise BusinessError(
            "maintenance_invalid", "Use a bounded, explicit local document maintenance request", status=422
        ) from exc


def request_fingerprint(data: object) -> str:
    """The request key and exact target form one stable identity; validation and hashing never mutate input."""
    normalized = parse_request(data)
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def page(after: int = 0, limit: int = 50) -> None:
    if type(after) is not int or not 0 <= after <= MAX_INTEGER or type(limit) is not int or not 1 <= limit <= 100:
        raise BusinessError(
            "maintenance_invalid", "Use a nonnegative integer cursor and a page of 1 to 100 jobs", status=422
        )


def lease_until(now: str) -> str:
    try:
        if not isinstance(now, str) or not 1 <= len(now) <= 40:
            raise ValueError
        parsed = dt.datetime.fromisoformat(now)
        if parsed.tzinfo is None:
            raise ValueError
        return (parsed.astimezone(dt.timezone.utc) + dt.timedelta(seconds=LEASE_SECONDS)).isoformat(
            timespec="microseconds"
        )
    except (ValueError, TypeError, OverflowError) as exc:
        raise BusinessError(
            "maintenance_invalid", "A timezone-aware lease time within the supported range is required", status=422
        ) from exc


@dataclass(frozen=True)
class Claim:
    """Captured attempt ownership; repositories validate its live boot, token, fence and target before writes."""

    agent_id: str
    job_id: str
    attempt_id: str
    token: str
    boot_id: str
    fence: int
    kind: str
    document_id: str
    revision_id: str
    source_revision: int
    erasure_job_id: str | None
    index_version: str
