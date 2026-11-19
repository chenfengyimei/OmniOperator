"""Bounded conversation policy; whole turns are the smallest compression unit."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence

CONTEXT_CHARS = 48_000
SUMMARY_CHARS = 4_000
SUMMARY_INPUT_CHARS = 32_000
SUMMARY_BATCH_MESSAGES = 64
SUMMARY_CALLS_PER_TURN = 3
RECENT_TURNS = 8
SUMMARY_TRIGGER_MESSAGES = 32
# In-run tool transcript compaction triggers well below the hard character limit so a long
# run can keep going; the most recent complete tool rounds are always preserved intact.
RUN_COMPACT_TRIGGER_CHARS = CONTEXT_CHARS // 2
RUN_COMPACT_KEEP_ROUNDS = 2


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def source_digest(epoch: int, parent_id: str | None, messages: Sequence[Mapping[str, object]]) -> str:
    payload = [epoch, parent_id, [[row["id"], row["content"]] for row in messages]]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def turns(messages: Sequence[Mapping[str, object]]) -> list[list[Mapping[str, object]]]:
    groups: list[list[Mapping[str, object]]] = []
    for row in messages:
        if not groups or groups[-1][0]["run_id"] != row["run_id"]:
            groups.append([])
        groups[-1].append(row)
    return groups


def batch(messages: Sequence[Mapping[str, object]]) -> list[Mapping[str, object]]:
    chosen: list[Mapping[str, object]] = []
    size = 0
    for group in turns(messages):
        count = sum(len(_text(row["content"])) + 64 for row in group)
        if size + count > SUMMARY_INPUT_CHARS or len(chosen) + len(group) > SUMMARY_BATCH_MESSAGES:
            break
        chosen.extend(group)
        size += count
    return chosen


def tail(messages: Sequence[Mapping[str, object]], budget: int) -> list[Mapping[str, object]]:
    chosen: list[Mapping[str, object]] = []
    for group in reversed(turns(messages)):
        size = sum(len(_text(row["content"])) + 64 for row in group)
        if size > budget:
            break
        chosen[0:0] = group
        budget -= size
    return chosen


def tool_round_starts(messages: Sequence[Mapping[str, object]]) -> list[int]:
    """Indices of assistant messages carrying tool_calls; each starts one complete tool round.

    A round owns its assistant message and every following tool result, so compressing whole
    rounds never leaves half a tool pair behind.
    """
    return [i for i, m in enumerate(messages) if m.get("role") == "assistant" and m.get("tool_calls")]


def run_compaction_window(
    messages: Sequence[Mapping[str, object]], keep_rounds: int = RUN_COMPACT_KEEP_ROUNDS
) -> tuple[int, int] | None:
    """Return ``(head_end, cut)`` for in-run compaction, or None when nothing can be compressed.

    ``messages[head_end:cut]`` are the old complete tool rounds to summarize; ``messages[cut:]``
    (the most recent ``keep_rounds`` rounds plus any tail runtime notice) stay intact. The frozen
    system prefix and the initial history/user head below ``head_end`` are never touched.
    """
    starts = tool_round_starts(messages)
    if len(starts) <= keep_rounds:
        return None
    return starts[0], starts[-keep_rounds]


def bounded_tool_summary_sources(
    messages: Sequence[Mapping[str, object]], budget: int = SUMMARY_INPUT_CHARS
) -> list[dict[str, object]]:
    """Build summary sources from live tool-round records within a bounded character budget.

    Live transcript records have no persisted id; synthetic ids keep the summary payload shape.
    Content is truncated at the budget so the summary request itself stays bounded.
    """
    sources: list[dict[str, object]] = []
    used = 0
    for index, m in enumerate(messages):
        remaining = budget - used
        if remaining <= 0:
            break
        content = m["content"] if isinstance(m["content"], str) else json.dumps(m["content"], ensure_ascii=False)
        if len(content) > remaining:
            content = content[:remaining]
        sources.append({"id": f"run-tool-{index}", "role": m["role"], "content": content})
        used += len(content) + 64
    return sources
