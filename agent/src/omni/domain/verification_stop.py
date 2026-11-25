"""Scoped completion evidence and bounded follow-up after unverified writes."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import cast

from .contracts import JSON

MAX_VERIFY_NUDGES = 2
MAX_NUDGE_TOOLS = 8
FILE_WRITES = frozenset({"files_write", "files_patch"})


def _document(value: JSON) -> dict[str, JSON]:
    return value if isinstance(value, dict) else {}


def _digest(value: JSON) -> bool:
    return isinstance(value, str) and re.fullmatch("[a-f0-9]{64}", value) is not None


def completion_report(
    actions: Sequence[Mapping[str, JSON]], artifacts: Mapping[str, list[JSON]], *, limited: bool
) -> dict[str, JSON]:
    """Derive scoped evidence in dispatch order from durable workspace and object receipts."""
    items: dict[str, dict[str, JSON]] = {}
    latest: dict[str, str] = {}

    def check_version(path: str, digest: JSON, check_id: str, scope: str) -> None:
        item = items[latest[path]]
        match = _digest(digest) and digest == item.get("version")
        item.update(state="verified" if match else "needs_review", check_action_id=check_id if match else None)
        item["scope"] = scope if match else "workspace_file"

    for action in actions:
        identity, tool = str(action["id"]), str(action["tool"])
        result = _document(action.get("result"))
        data = _document(result.get("data"))
        path = data.get("workspace_path")
        if tool == "files_read" and action["state"] == "ok" and isinstance(path, str) and path in latest:
            check_version(path, data.get("sha256"), identity, "file_readback")
        if not action["writes"]:
            continue
        state, effect = action["state"], action["effect"]
        if state == "ok" and effect == "none" and not artifacts.get(identity):
            continue
        item: dict[str, JSON] = {"action_id": identity, "tool": tool, "state": "needs_review", "scope": "action"}
        items[identity] = item
        if state == "unknown" or effect == "unknown":
            item["state"] = "unknown"
            for target in latest:
                check_version(target, None, identity, "workspace_file")
        elif tool in FILE_WRITES and state == "ok" and effect == "applied" and isinstance(path, str):
            previous = latest.get(path)
            if previous:
                items.pop(previous)
            latest[path] = identity
            item.update(scope="workspace_file", target=path, version=data.get("sha256"))
        elif tool == "shell_execute" and effect == "applied":
            exit_code = data.get("exit_code")
            passed = state == "ok" and type(exit_code) is int and exit_code == 0
            versions = _document(data.get("verified_files")) if passed else {}
            for target in latest:
                check_version(target, versions.get(target), identity, "command_exit_and_file_version")
            item.update(scope="command_exit", state="verified" if passed else "needs_review", exit_code=exit_code)
        elif state == "ok" and artifacts.get(identity):
            item.update(scope="published_objects", state="verified", objects=artifacts[identity])

    rows = list(items.values())
    verified = sum(row["state"] == "verified" for row in rows)
    requires_review = limited or verified < len(rows)
    if requires_review:
        status = "partial" if verified and all(row["state"] != "unknown" for row in rows) else "needs_review"
    else:
        status = "verified" if verified else "not_required"
    return {
        "state": status,
        "verified": verified,
        "total": len(rows),
        "history_limited": limited,
        "items": cast(list[JSON], rows[-24:]),
    }


def _verification(entry: Mapping[str, JSON]) -> str:
    value = entry.get("verification")
    return value.strip() if isinstance(value, str) else ""


def unverified_writes(writes: Sequence[Mapping[str, JSON]]) -> list[str]:
    """Tool names of applied writes whose receipt carries no verification evidence, in order."""
    names: list[str] = []
    for entry in writes:
        if _verification(entry):
            continue
        tool = entry.get("tool")
        name = tool.strip() if isinstance(tool, str) else ""
        if name and name not in names:
            names.append(name)
    return names


def build_verify_nudge(
    writes: Sequence[Mapping[str, JSON]], attempts: int, max_attempts: int = MAX_VERIFY_NUDGES
) -> str | None:
    """Return a synthetic follow-up when applied writes lack fresh verification evidence.

    ``writes`` are this run's applied write receipts ({tool, verification}); ``attempts`` is the
    number of nudges already issued. Returns None when nothing is unverified or the bounded
    follow-up budget is exhausted, so the caller finishes with the candidate answer.
    """
    if attempts >= max_attempts:
        return None
    pending = unverified_writes(writes)
    if not pending:
        return None
    shown = pending[:MAX_NUDGE_TOOLS]
    lines = [f"- {name}" for name in shown]
    if len(pending) > MAX_NUDGE_TOOLS:
        lines.append(f"- ... and {len(pending) - MAX_NUDGE_TOOLS} more")
    listed = "\n".join(lines)
    return (
        "[System: You applied changes this turn, but these write actions carry no verification "
        "evidence in their structured receipts:\n\n"
        f"{listed}\n\n"
        "Run a concrete verification now (read the change back, run the relevant check or observe "
        "the postcondition), repair anything that fails, and report the evidence you actually "
        "observed. Your final answer text and task-list checkboxes are NOT success evidence; only "
        "a structured action verification counts. If verification is not possible, explain the "
        "concrete blocker instead of claiming the work is fully verified.]"
    )
