"""Bounded derived-memory indexing and related-fact proposals; no I/O or fact rewriting."""

from __future__ import annotations

import hashlib
import json
import uuid

from .contracts import BusinessError

POLICY = "local-related-facts-v1"
PAGE = 32
CANDIDATES = 512
MAX_BYTES = 262_144
THRESHOLD = 850_000


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def paging(after: str, limit: int) -> None:
    try:
        if not isinstance(after, str) or (after and str(uuid.UUID(after)) != after):
            raise ValueError
        if type(limit) is not int or not 1 <= limit <= PAGE:
            raise ValueError
    except (ValueError, TypeError, AttributeError) as exc:
        raise BusinessError("invalid_memory_query", "Use a UUID cursor and page of 1 to 32", status=422) from exc


def query_value(query: str, limit: int) -> None:
    if (
        not isinstance(query, str)
        or not 1 <= len(query.strip()) <= 500
        or type(limit) is not int
        or not 1 <= limit <= 20
    ):
        raise BusinessError("invalid_memory_query", "Use 1 to 500 query characters and at most 20 hits", status=422)


def merge(text: list[dict], vectors: list[dict], limit: int) -> list[dict]:
    scores: dict[str, float] = {}
    rows: dict[str, dict] = {}
    origins: dict[str, list[str]] = {}
    for mode, values in (("full_text", text), ("vector", vectors)):
        for position, row in enumerate(values, 1):
            key = row["id"]
            rows[key] = {**rows.get(key, {}), **row}
            scores[key] = scores.get(key, 0) + 1 / (60 + position)
            origins.setdefault(key, []).append(mode)
    return [
        {**rows[k], "score": scores[k], "matched_by": origins[k]}
        for k in sorted(scores, key=lambda k: (-scores[k], k))[:limit]
    ]
