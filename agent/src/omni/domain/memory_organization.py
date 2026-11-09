"""Bounded exact-fact organization requests and owner decisions; no memory writes, model calls or I/O."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid

from .contracts import BusinessError
from .history import MAX_INTEGER

POLICY = "exact-fact-duplicates-v1"
BUDGET_MICRO = 0
MAX_SOURCES = 32
MAX_CANDIDATES = 31
MAX_SOURCE_BYTES = 262_144
MAX_ANCHORS = 8
DECISIONS = frozenset({"apply", "reject", "undo"})


def _uuid(value: object) -> str:
    if not isinstance(value, str) or len(value) != 36 or str(uuid.UUID(value)) != value:
        raise ValueError
    return value


def _positive(value: object) -> int:
    if type(value) is not int or not 1 <= value <= MAX_INTEGER:
        raise ValueError
    return value


def parse_request(data: object) -> dict:
    """Canonical source identities fix the reviewed input; repeated equal identities never expand its scope."""
    try:
        if not isinstance(data, dict) or set(data) != {"key", "sources"}:
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
        sources = data["sources"]
        if not isinstance(sources, list) or not 2 <= len(sources) <= MAX_SOURCES:
            raise ValueError
        revisions: dict[str, int] = {}
        for source in sources:
            if not isinstance(source, dict) or set(source) != {"memory_id", "revision"}:
                raise ValueError
            identity, revision = _uuid(source["memory_id"]), _positive(source["revision"])
            if identity in revisions and revisions[identity] != revision:
                raise ValueError
            revisions[identity] = revision
        if len(revisions) < 2:
            raise ValueError
        return {
            "key": key,
            "sources": [{"memory_id": identity, "revision": revisions[identity]} for identity in sorted(revisions)],
        }
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, UnicodeError) as exc:
        raise BusinessError(
            "memory_organization_invalid", "Select 2 to 32 exact memory revisions and a bounded request key", status=422
        ) from exc


def request_fingerprint(data: object) -> str:
    normalized = parse_request(data)
    raw = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode(
        "utf-8"
    )
    return hashlib.sha256(raw).hexdigest()


def parse_decision(data: object) -> dict:
    """Every owner action binds the observed candidate revision, immutable fingerprint and restoration epoch."""
    try:
        if not isinstance(data, dict) or set(data) != {"revision", "fingerprint", "epoch", "decision"}:
            raise ValueError
        revision, epoch = _positive(data["revision"]), _uuid(data["epoch"])
        digest, decision = data["fingerprint"], data["decision"]
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError
        if not isinstance(decision, str) or decision not in DECISIONS:
            raise ValueError
        return {"revision": revision, "fingerprint": digest, "epoch": epoch, "decision": decision}
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, UnicodeError) as exc:
        raise BusinessError(
            "memory_organization_invalid",
            "Use the observed memory-organization revision and explicit decision",
            status=422,
        ) from exc


def page(after: int = 0, limit: int = 50) -> None:
    if type(after) is not int or not 0 <= after <= MAX_INTEGER or type(limit) is not int or not 1 <= limit <= 50:
        raise BusinessError(
            "memory_organization_invalid", "Use a nonnegative cursor and a page of 1 to 50 batches", status=422
        )
