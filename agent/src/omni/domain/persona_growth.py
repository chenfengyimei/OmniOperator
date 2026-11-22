"""Deterministic persona-growth rendering from approved reflection entries; no I/O."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass

MAX_GROWTH_BYTES = 1_048_576
MAX_GROWTH_LINES = 2_000
HEADER = "# 人格成长（派生视图）\n\n> 由已批准的反思条目生成；不是事实来源，不读取来源正文，不进入模型上下文。\n\n"
EMPTY = "（暂无已批准的反思）\n"
TRUNCATED = "<!-- truncated: {remaining} more approved entries -->\n"
MAX_SENTENCE = 240

_NEWLINES = re.compile(r"\r\n|\r|\n")


@dataclass(frozen=True)
class GrowthEntry:
    """One approved reflection sentence and its owner decision day."""

    approved_day: str
    journal_id: str
    sentence: str


@dataclass(frozen=True)
class RenderedGrowth:
    """The exact bytes a valid cache must contain, plus bounded counts."""

    content: str
    sha256: str
    bytes: int
    approved: int
    included: int
    truncated: bool


def display_sentence(value: str) -> str:
    """Collapse line breaks so one ledger sentence stays on one file line."""
    return _NEWLINES.sub(" ", value)


def _entry_line(entry: GrowthEntry) -> str:
    return f"- [{entry.approved_day}] {display_sentence(entry.sentence)}\n"


def _checked(entries: Iterable[GrowthEntry]) -> list[GrowthEntry]:
    values = list(entries)
    for entry in values:
        if len(entry.approved_day) != 10 or not entry.approved_day.isascii():
            raise ValueError("approved_day must be an ISO date")
        if not 1 <= len(entry.sentence) <= MAX_SENTENCE:
            raise ValueError("sentence must be 1-240 characters")
        if not entry.sentence.strip():
            raise ValueError("sentence must not be blank")
    return values


def ordered(entries: Iterable[GrowthEntry]) -> list[GrowthEntry]:
    """Newest approval day first; equal days keep a stable journal order."""
    values = _checked(entries)
    values.sort(key=lambda entry: entry.journal_id)
    values.sort(key=lambda entry: entry.approved_day, reverse=True)
    return values


def render(entries: Iterable[GrowthEntry]) -> RenderedGrowth:
    """Render a deterministic cache body; the caller compares it byte for byte."""
    values = ordered(entries)
    header_lines = HEADER.count("\n")
    body: list[str] = []
    included = 0
    used = len(HEADER.encode())
    for entry in values:
        line = _entry_line(entry)
        size = len(line.encode())
        if used + size > MAX_GROWTH_BYTES or header_lines + included + 1 > MAX_GROWTH_LINES:
            break
        body.append(line)
        used += size
        included += 1
    remaining = len(values) - included
    marker = ""
    if remaining:
        while True:
            candidate = TRUNCATED.format(remaining=remaining)
            fits = (
                used + len(candidate.encode()) <= MAX_GROWTH_BYTES and header_lines + included + 1 <= MAX_GROWTH_LINES
            )
            if fits:
                marker = candidate
                break
            if not body:
                break
            dropped = body.pop()
            used -= len(dropped.encode())
            included -= 1
            remaining += 1
    if not values:
        content = HEADER + EMPTY
    else:
        content = HEADER + "".join(body) + marker
    encoded = content.encode()
    return RenderedGrowth(
        content=content,
        sha256=hashlib.sha256(encoded).hexdigest(),
        bytes=len(encoded),
        approved=len(values),
        included=included,
        truncated=bool(marker),
    )


__all__ = [
    "EMPTY",
    "HEADER",
    "MAX_GROWTH_BYTES",
    "MAX_GROWTH_LINES",
    "MAX_SENTENCE",
    "TRUNCATED",
    "GrowthEntry",
    "RenderedGrowth",
    "display_sentence",
    "ordered",
    "render",
]
