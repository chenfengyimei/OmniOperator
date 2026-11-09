"""Bounded conversation policy; whole turns are the smallest compression unit."""

from __future__ import annotations

import hashlib
import json

CONTEXT_CHARS = 48_000
SUMMARY_CHARS = 4_000
SUMMARY_INPUT_CHARS = 32_000
SUMMARY_BATCH_MESSAGES = 64
SUMMARY_CALLS_PER_TURN = 3
RECENT_TURNS = 8
SUMMARY_TRIGGER_MESSAGES = 32


def source_digest(epoch: int, parent_id: str | None, messages: list[dict]) -> str:
    payload = [epoch, parent_id, [[row["id"], row["content"]] for row in messages]]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def turns(messages: list[dict]) -> list[list[dict]]:
    groups: list[list[dict]] = []
    for row in messages:
        if not groups or groups[-1][0]["run_id"] != row["run_id"]:
            groups.append([])
        groups[-1].append(row)
    return groups


def batch(messages: list[dict]) -> list[dict]:
    chosen: list[dict] = []
    size = 0
    for group in turns(messages):
        count = sum(len(row["content"]) + 64 for row in group)
        if size + count > SUMMARY_INPUT_CHARS or len(chosen) + len(group) > SUMMARY_BATCH_MESSAGES:
            break
        chosen.extend(group)
        size += count
    return chosen


def tail(messages: list[dict], budget: int) -> list[dict]:
    chosen: list[dict] = []
    for group in reversed(turns(messages)):
        size = sum(len(row["content"]) + 64 for row in group)
        if size > budget:
            break
        chosen[0:0] = group
        budget -= size
    return chosen
