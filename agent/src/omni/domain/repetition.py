"""Conservative model-output repetition degeneration detection; pure rules, no I/O.

A model stuck in a repetition loop can echo one fragment across its whole output budget. This
flags repetition-dominated text so the run aborts with a clear error instead of burning budget
or publishing the loop. Deliberately conservative: only long verbatim repeats covering a
majority of a sufficiently long fragment trip it; short or legitimately repetitive answers pass.
It never repairs, retries or replays anything.
"""

from __future__ import annotations

import math

MIN_FRAGMENT_LENGTH = 400
REPEAT_WINDOW = 60
MIN_REPEAT_COUNT = 5
DOMINANCE_RATIO = 0.5
# A completed (stop-path) reply discards a finished answer, so it only aborts at runaway scale:
# real loops run tens of thousands of characters, while asked-for repeats and synthetic bounded
# payloads stay small and must be delivered.
STOP_PATH_MIN_CHARS = 16_000
RUNAWAY_DISTINCT_LINE_RATIO = 0.5


def is_repetition_dominated(text: str) -> bool:
    """True when exact repeats cover at least half of a long fragment (a repetition loop)."""
    n = len(text)
    if n < MIN_FRAGMENT_LENGTH:
        return False
    return _line_dominated(text, n) or _window_dominated(text, n)


def is_runaway_repetition(text: str) -> bool:
    """Repetition-dominated AND, when line structure exists, at most half the lines distinct.

    Legitimately repetitive but correct output (distinct batch rows) shares prefixes yet keeps
    every line distinct, so it does not qualify.
    """
    if not is_repetition_dominated(text):
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < MIN_REPEAT_COUNT:
        return True
    return len(set(lines)) <= len(lines) * RUNAWAY_DISTINCT_LINE_RATIO


def is_degenerate_reply(text: str) -> bool:
    """Stop-path guard for a completed model reply: abort only at runaway repetition scale."""
    return len(text) >= STOP_PATH_MIN_CHARS and is_runaway_repetition(text)


def _line_dominated(text: str, n: int) -> bool:
    counts: dict[str, int] = {}
    for line in text.splitlines():
        norm = line.strip()
        if norm:
            counts[norm] = counts.get(norm, 0) + 1
    return any(c >= MIN_REPEAT_COUNT and c * len(line) >= n * DOMINANCE_RATIO for line, c in counts.items())


def _window_dominated(text: str, n: int) -> bool:
    needed = max(MIN_REPEAT_COUNT, math.ceil(n * DOMINANCE_RATIO / REPEAT_WINDOW))
    counts: dict[str, int] = {}
    for i in range(n - REPEAT_WINDOW + 1):
        key = text[i : i + REPEAT_WINDOW]
        c = counts.get(key, 0) + 1
        if c >= needed:
            return True
        counts[key] = c
    return False
