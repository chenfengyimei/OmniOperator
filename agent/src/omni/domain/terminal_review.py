"""Owner observations of unknown terminal effects; never a receipt correction or replay authority."""

from __future__ import annotations

import hashlib
import json
from typing import cast

from .contracts import BusinessError
from .terminals import digest, identity, integer

POLICY = "terminal-review-1"
DECISIONS = frozenset({"applied", "not_applied", "still_unknown"})
MAX_REVIEWS = 100


def invalid() -> BusinessError:
    return BusinessError(
        "terminal_review_invalid",
        "Use the currently inspected terminal observation and bounded owner evidence",
        status=422,
    )


def fingerprint(value: object) -> str:
    try:
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode(
            "utf-8"
        )
        if len(raw) > 16384:
            raise ValueError
        return hashlib.sha256(raw).hexdigest()
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise invalid() from None


def parse_review(value: object) -> dict[str, object]:
    try:
        if not isinstance(value, dict):
            raise ValueError
        data = cast(dict[object, object], value)
        keys = {key for key in data if isinstance(key, str)}
        if len(keys) != len(data) or keys != {
            "review_id",
            "revision",
            "history_epoch",
            "fingerprint",
            "policy",
            "decision",
            "evidence",
            "acknowledge_limits",
        }:
            raise ValueError
        if data["policy"] != POLICY or data["acknowledge_limits"] is not True:
            raise ValueError
        decision = data["decision"]
        if not isinstance(decision, str) or decision not in DECISIONS:
            raise ValueError
        evidence = data["evidence"]
        if not isinstance(evidence, str) or not 1 <= len(evidence) <= 2000 or not evidence.strip() or "\0" in evidence:
            raise ValueError
        evidence.encode("utf-8")
        return {
            "review_id": identity(data["review_id"]),
            "revision": integer(data["revision"], 0, MAX_REVIEWS),
            "history_epoch": identity(data["history_epoch"]),
            "fingerprint": digest(data["fingerprint"]),
            "policy": POLICY,
            "decision": decision,
            "evidence": evidence,
            "acknowledge_limits": True,
        }
    except (ValueError, TypeError, KeyError, UnicodeError, BusinessError):
        raise invalid() from None


def request_fingerprint(value: object) -> str:
    normalized = parse_review(value)
    evidence = normalized.pop("evidence")
    evidence_text = evidence if isinstance(evidence, str) else ""
    return fingerprint({**normalized, "evidence_sha256": hashlib.sha256(evidence_text.encode("utf-8")).hexdigest()})


def page(after: str = "", limit: int = 20) -> None:
    if after:
        identity(after)
    integer(limit, 1, 50)
