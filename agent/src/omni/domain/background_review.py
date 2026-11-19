"""Bounded post-turn review proposals and owner decisions; no model calls, memory writes or I/O.

A finished turn may spawn one background reviewer that replays the turn and proposes memory facts or
skill edits worth keeping. The reviewer only ever READS through a whitelist and STAGES write intents as
pending proposals; applying a proposal still goes through the existing evidence-anchored memory and
review-before-enable skill chains, so a cited source is never itself an authorization.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from typing import cast

from .contracts import BusinessError
from .history import MAX_INTEGER

POLICY = "post-turn-review-v1"
PURPOSE = "review"
# Independent bounded budget: a review never inherits the turn's step budget and never replays a call.
MAX_CALLS = 8
MAX_PROPOSALS = 16
MAX_CONTENT_CHARS = 20_000
MAX_QUOTE_CHARS = 2_000
MAX_SECTION_CHARS = 200
DECISIONS = frozenset({"apply", "reject", "undo"})
# The reviewer may inspect through these read-only tools; everything else is denied at dispatch.
READ_TOOLS = frozenset({"memory_search", "skills_list", "skill_view", "skill_history"})
# Write intents are intercepted into pending proposals, never dispatched autonomously.
WRITE_TOOLS = frozenset(
    {
        "memory_remember",
        "memory_forget",
        "skill_create",
        "skill_rewrite",
        "skill_patch",
        "skill_rollback",
        "skill_archive",
        "skill_restore",
    }
)
MEMORY_TOOLS = frozenset({"memory_remember", "memory_forget"})

REVIEW_PROMPT = (
    "You are a background reviewer evaluating one finished turn. Decide whether any durable memory fact "
    "or procedural skill edit is worth keeping for future sessions.\n"
    "You may inspect existing memory and skills with the read-only tools (memory_search, skills_list, "
    "skill_view, skill_history). To propose a change, call the matching memory or skill tool; your call "
    "is STAGED as a pending proposal for the owner to review, never applied automatically.\n"
    "Memory: propose a fact only with an exact quote copied from a source message in this turn "
    "(memory_remember requires content, quote and the source message_id). One fact, one proposal; do not "
    "duplicate what existing memory already holds.\n"
    "Skills: propose a class-level procedure or pitfall a future session would benefit from; prefer "
    "patching an existing skill over creating a narrow one-session skill.\n"
    "Do NOT capture environment-dependent failures, negative claims about tools, transient errors that "
    "resolved, or one-off task narratives. If nothing is worth saving, reply exactly 'Nothing to save.' "
    "and stop. Other tools are denied; do not attempt them."
)


def _uuid(value: object) -> str:
    if not isinstance(value, str) or len(value) != 36 or str(uuid.UUID(value)) != value:
        raise ValueError
    return value


def _positive(value: object) -> int:
    number = cast(int, value)
    if type(value) is not int or not 1 <= number <= MAX_INTEGER:
        raise ValueError
    return number


def _bounded_text(value: object, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError
    if any(unicodedata.category(char) == "Cc" and char not in "\n\r\t" for char in value):
        raise ValueError
    value.encode("utf-8")
    return value


def review_key(run_id: str) -> str:
    """One stable idempotency key per finished run; re-triggering never expands scope or re-pays."""
    return "review:" + _uuid(run_id)


def parse_request(data: object) -> dict[str, object]:
    try:
        if not isinstance(data, dict):
            raise ValueError
        record = cast(dict[str, object], data)
        if set(record) != {"run_id"}:
            raise ValueError
        run_id = _uuid(record["run_id"])
        return {"run_id": run_id, "key": review_key(run_id)}
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, UnicodeError) as exc:
        raise BusinessError(
            "background_review_invalid", "Select one finished run to review", status=422
        ) from exc


def request_fingerprint(data: object) -> str:
    normalized = parse_request(data)
    raw = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode(
        "utf-8"
    )
    return hashlib.sha256(raw).hexdigest()


def classify_tool(name: str) -> str:
    """Dispatch class for one reviewer tool call: read, staged write proposal, or denied."""
    if name in READ_TOOLS:
        return "read"
    if name in WRITE_TOOLS:
        return "write"
    return "deny"


def parse_proposal(name: str, arguments: object) -> dict[str, object]:
    """Normalize one intercepted write intent into a bounded pending proposal; never applies it."""
    try:
        if name not in WRITE_TOOLS or not isinstance(arguments, dict):
            raise ValueError
        args = cast(dict[str, object], arguments)
        kind = "memory" if name in MEMORY_TOOLS else "skill"
        proposal: dict[str, object] = {"kind": kind, "tool": name}
        if name == "memory_remember":
            proposal["content"] = _bounded_text(args.get("content"), MAX_CONTENT_CHARS)
            proposal["quote"] = _bounded_text(args.get("quote"), MAX_QUOTE_CHARS)
            message_id = args.get("message_id")
            proposal["message_id"] = _uuid(message_id) if message_id is not None else None
        elif name == "memory_forget":
            proposal["memory_id"] = _uuid(args.get("memory_id"))
            proposal["revision"] = _positive(args.get("revision"))
        elif name in {"skill_create", "skill_rewrite"}:
            proposal["content"] = _bounded_text(args.get("content"), MAX_CONTENT_CHARS)
            proposal["skill_id"] = _uuid(args["skill_id"]) if name == "skill_rewrite" else None
            proposal["revision"] = _positive(args.get("revision", 0)) if args.get("revision") else 0
        elif name == "skill_patch":
            proposal["skill_id"] = _uuid(args.get("skill_id"))
            proposal["section"] = _bounded_text(args.get("section"), MAX_SECTION_CHARS)
            proposal["text"] = _bounded_text(args.get("text"), MAX_CONTENT_CHARS)
            proposal["revision"] = _positive(args.get("revision", 0)) if args.get("revision") else 0
        elif name == "skill_rollback":
            proposal["skill_id"] = _uuid(args.get("skill_id"))
            proposal["revision_id"] = _uuid(args.get("revision_id"))
            proposal["revision"] = _positive(args.get("revision", 0)) if args.get("revision") else 0
        else:  # skill_archive / skill_restore
            proposal["skill_id"] = _uuid(args.get("skill_id"))
            proposal["revision"] = _positive(args.get("revision", 0)) if args.get("revision") else 0
        return proposal
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, UnicodeError) as exc:
        raise BusinessError(
            "background_review_invalid", "A review proposal must be a bounded memory or skill edit", status=422
        ) from exc


def proposal_fingerprint(batch_id: str, agent_id: str, epoch: str, proposal: dict[str, object]) -> str:
    raw = json.dumps(
        {"batch_id": batch_id, "agent_id": agent_id, "history_epoch": epoch, "policy": POLICY, "proposal": proposal},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def parse_decision(data: object) -> dict[str, object]:
    """Every owner action binds the observed candidate revision, immutable fingerprint and epoch."""
    try:
        if not isinstance(data, dict):
            raise ValueError
        record = cast(dict[str, object], data)
        if set(record) != {"revision", "fingerprint", "epoch", "decision"}:
            raise ValueError
        revision, epoch = _positive(record["revision"]), _uuid(record["epoch"])
        digest, decision = record["fingerprint"], record["decision"]
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError
        if not isinstance(decision, str) or decision not in DECISIONS:
            raise ValueError
        return {"revision": revision, "fingerprint": digest, "epoch": epoch, "decision": decision}
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, UnicodeError) as exc:
        raise BusinessError(
            "background_review_invalid", "Use the observed review revision and explicit decision", status=422
        ) from exc


def page(after: int = 0, limit: int = 50) -> None:
    if type(after) is not int or not 0 <= after <= MAX_INTEGER or type(limit) is not int or not 1 <= limit <= 50:
        raise BusinessError(
            "background_review_invalid", "Use a nonnegative cursor and a page of 1 to 50 reviews", status=422
        )