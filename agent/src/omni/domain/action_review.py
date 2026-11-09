"""Owner observations about unknown writes; does not settle actions, authorize replay or release model costs."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import uuid

from .contracts import BusinessError
from .history import MAX_INTEGER
from .retention import body_fingerprint

POLICY = ACTION_POLICY = "action-review-1"
DECISIONS = frozenset({"applied", "not_applied", "still_unknown"})
TERMINAL_RUNS = frozenset({"succeeded", "failed", "cancelled", "interrupted"})
PROOF_KEYS = frozenset(
    {
        "policy",
        "agent_id",
        "action_id",
        "run_id",
        "run_state",
        "session_id",
        "session_epoch",
        "tool",
        "writes",
        "state",
        "effect",
        "policy_revision",
        "arguments_sha256",
        "result_sha256",
        "ended_at",
        "history_epoch",
        "source",
    }
)
PLUGIN_KEYS = frozenset(
    {
        "kind",
        "binding_id",
        "extension_id",
        "extension_revision_id",
        "tool_id",
        "grant_id",
        "authority_epoch",
        "history_epoch",
        "package_sha256",
        "catalog_sha256",
        "spec_sha256",
    }
)


def _error() -> BusinessError:
    return BusinessError(
        "action_review_invalid", "Use an exact owner observation and the currently inspected action proof", status=422
    )


def _uuid(value: object) -> str:
    if not isinstance(value, str) or len(value) != 36 or str(uuid.UUID(value)) != value:
        raise ValueError
    return value


def _integer(value: object, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= MAX_INTEGER:
        raise ValueError
    return value


def _sha(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def evidence_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def parse_review(data: object) -> dict:
    try:
        if not isinstance(data, dict) or set(data) != {
            "review_id",
            "revision",
            "history_epoch",
            "fingerprint",
            "policy",
            "decision",
            "evidence",
        }:
            raise ValueError
        if data["policy"] != POLICY or not isinstance(data["decision"], str) or data["decision"] not in DECISIONS:
            raise ValueError
        evidence = data["evidence"]
        if not isinstance(evidence, str) or not 1 <= len(evidence) <= 2000 or not evidence.strip() or "\0" in evidence:
            raise ValueError
        evidence.encode("utf-8")
        return {
            "review_id": _uuid(data["review_id"]),
            "revision": _integer(data["revision"]),
            "history_epoch": _uuid(data["history_epoch"]),
            "fingerprint": _sha(data["fingerprint"]),
            "policy": POLICY,
            "decision": data["decision"],
            "evidence": evidence,
        }
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, UnicodeError) as exc:
        raise _error() from exc


def request_fingerprint(data: object) -> str:
    value = parse_review(data)
    evidence = value.pop("evidence")
    value["evidence_sha256"] = evidence_sha256(evidence)
    return hashlib.sha256(_canonical(value)).hexdigest()


def proof_fingerprint(proof: object) -> str:
    try:
        if not isinstance(proof, dict) or set(proof) != PROOF_KEYS or proof["policy"] != POLICY:
            raise ValueError
        for key in ("agent_id", "action_id", "run_id", "session_id", "history_epoch"):
            _uuid(proof[key])
        if (
            not isinstance(proof["run_state"], str)
            or proof["run_state"] not in TERMINAL_RUNS
            or proof["writes"] is not True
        ):
            raise ValueError
        if proof["state"] not in {
            "denied",
            "ok",
            "error",
            "cancelled",
            "unknown",
        } or proof["effect"] not in {"none", "applied", "unknown"}:
            raise ValueError
        if proof["state"] != "unknown" and proof["effect"] != "unknown":
            raise ValueError
        if not isinstance(proof["tool"], str) or re.fullmatch(r"[A-Za-z0-9_-]{1,100}", proof["tool"]) is None:
            raise ValueError
        _integer(proof["policy_revision"], 1)
        _integer(proof["session_epoch"], 1)
        _sha(proof["arguments_sha256"])
        _sha(proof["result_sha256"])
        ended_at = proof["ended_at"]
        if not isinstance(ended_at, str) or not 1 <= len(ended_at) <= 40:
            raise ValueError
        ended = dt.datetime.fromisoformat(ended_at)
        if ended.tzinfo is None or ended.utcoffset() is None:
            raise ValueError
        source = proof["source"]
        if not isinstance(source, dict):
            raise ValueError
        if source.get("kind") == "builtin_unversioned":
            if set(source) != {"kind"} or proof["tool"].startswith("ext__"):
                raise ValueError
        elif source.get("kind") == "plugin":
            if set(source) != PLUGIN_KEYS or not proof["tool"].startswith("ext__"):
                raise ValueError
            for key in ("binding_id", "extension_id", "extension_revision_id", "tool_id", "grant_id", "history_epoch"):
                _uuid(source[key])
            _integer(source["authority_epoch"], 1)
            for key in ("package_sha256", "catalog_sha256", "spec_sha256"):
                _sha(source[key])
        else:
            raise ValueError
        if len(_canonical(proof)) > 16_384:
            raise ValueError
        return body_fingerprint([proof])[0]
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, UnicodeError, RecursionError) as exc:
        raise _error() from exc


def page(after: int = 0, limit: int = 20) -> None:
    try:
        _integer(after)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise _error() from exc
