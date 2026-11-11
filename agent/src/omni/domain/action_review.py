"""Owner observations about unknown writes; does not settle actions, authorize replay or release model costs."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import uuid
from typing import cast

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


def parse_review(data: object) -> dict[str, object]:
    try:
        if not isinstance(data, dict):
            raise ValueError
        record = cast(dict[str, object], data)
        if set(record) != {
            "review_id",
            "revision",
            "history_epoch",
            "fingerprint",
            "policy",
            "decision",
            "evidence",
        }:
            raise ValueError
        decision = record["decision"]
        if record["policy"] != POLICY or not isinstance(decision, str) or decision not in DECISIONS:
            raise ValueError
        evidence = record["evidence"]
        if not isinstance(evidence, str) or not 1 <= len(evidence) <= 2000 or not evidence.strip() or "\0" in evidence:
            raise ValueError
        evidence.encode("utf-8")
        return {
            "review_id": _uuid(record["review_id"]),
            "revision": _integer(record["revision"]),
            "history_epoch": _uuid(record["history_epoch"]),
            "fingerprint": _sha(record["fingerprint"]),
            "policy": POLICY,
            "decision": decision,
            "evidence": evidence,
        }
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, UnicodeError) as exc:
        raise _error() from exc


def request_fingerprint(data: object) -> str:
    value = parse_review(data)
    evidence = value.pop("evidence")
    value["evidence_sha256"] = evidence_sha256(cast(str, evidence))
    return hashlib.sha256(_canonical(value)).hexdigest()


def proof_fingerprint(proof: object) -> str:
    try:
        if not isinstance(proof, dict):
            raise ValueError
        record = cast(dict[str, object], proof)
        if set(record) != set(PROOF_KEYS) or record["policy"] != POLICY:
            raise ValueError
        for key in ("agent_id", "action_id", "run_id", "session_id", "history_epoch"):
            _uuid(record[key])
        if (
            not isinstance(record["run_state"], str)
            or record["run_state"] not in TERMINAL_RUNS
            or record["writes"] is not True
        ):
            raise ValueError
        if record["state"] not in {
            "denied",
            "ok",
            "error",
            "cancelled",
            "unknown",
        } or record["effect"] not in {"none", "applied", "unknown"}:
            raise ValueError
        if record["state"] != "unknown" and record["effect"] != "unknown":
            raise ValueError
        tool = record["tool"]
        if not isinstance(tool, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,100}", tool) is None:
            raise ValueError
        _integer(record["policy_revision"], 1)
        _integer(record["session_epoch"], 1)
        _sha(record["arguments_sha256"])
        _sha(record["result_sha256"])
        ended_at = record["ended_at"]
        if not isinstance(ended_at, str) or not 1 <= len(ended_at) <= 40:
            raise ValueError
        ended = dt.datetime.fromisoformat(ended_at)
        if ended.tzinfo is None or ended.utcoffset() is None:
            raise ValueError
        source = record["source"]
        if not isinstance(source, dict):
            raise ValueError
        source_record = cast(dict[str, object], source)
        if source_record.get("kind") == "builtin_unversioned":
            if set(source_record) != {"kind"} or tool.startswith("ext__"):
                raise ValueError
        elif source_record.get("kind") == "plugin":
            if set(source_record) != set(PLUGIN_KEYS) or not tool.startswith("ext__"):
                raise ValueError
            for key in ("binding_id", "extension_id", "extension_revision_id", "tool_id", "grant_id", "history_epoch"):
                _uuid(source_record[key])
            _integer(source_record["authority_epoch"], 1)
            for key in ("package_sha256", "catalog_sha256", "spec_sha256"):
                _sha(source_record[key])
        else:
            raise ValueError
        if len(_canonical(record)) > 16_384:
            raise ValueError
        return body_fingerprint([record])[0]
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, UnicodeError, RecursionError) as exc:
        raise _error() from exc


def page(after: int = 0, limit: int = 20) -> None:
    try:
        _integer(after)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise _error() from exc
