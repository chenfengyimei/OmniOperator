"""Pure rules for the verify-on-stop gate: applied writes without verification evidence.

When the model tries to finish right after applying writes whose structured receipts carry no
verification, the turn loop appends a bounded follow-up instead of accepting the stop. The only
success evidence is the persisted action receipt's ``verification`` field; the model's final answer
text and task-list checkboxes are never treated as proof. This module only decides and shapes the
nudge — it never waits, dispatches or touches storage.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .contracts import JSON

MAX_VERIFY_NUDGES = 2
MAX_NUDGE_TOOLS = 8


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
