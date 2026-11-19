"""Bounded transcript locate rules; pure logic, no I/O. Anchors come from the source transcript, never guessed."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from .contracts import JSON, BusinessError

MAX_QUERY_CHARS = 200
MAX_LOCATE_RESULTS = 8
DEFAULT_LOCATE_RESULTS = 5
MIN_SCORE = 0.34
EXACT_BONUS = 0.25
MAX_SNIPPET_CHARS = 240


def invalid(code: str = "media_locate_invalid") -> BusinessError:
    return BusinessError(code, "Media locate query or limit is invalid", status=422)


def normalize_query(value: object) -> str:
    if not isinstance(value, str):
        raise invalid()
    query = value.strip()
    if not 1 <= len(query) <= MAX_QUERY_CHARS or any(ord(c) < 32 for c in query):
        raise invalid()
    return query


def normalize_limit(value: object) -> int:
    if value is None:
        return DEFAULT_LOCATE_RESULTS
    if type(value) is not int or not 1 <= value <= MAX_LOCATE_RESULTS:
        raise invalid()
    return value


def _lcs_length(query: str, text: str) -> int:
    """Longest common subsequence length; rolling row keeps memory bounded by the query."""
    if not query or not text:
        return 0
    previous = [0] * (len(text) + 1)
    for symbol in query:
        current = [0]
        for index, other in enumerate(text):
            if symbol == other:
                current.append(previous[index] + 1)
            else:
                current.append(max(current[index], previous[index + 1]))
        previous = current
    return previous[-1]


def score_segment(query: str, text: str) -> float:
    """LCS coverage of the query plus an exact-substring bonus, mirroring the reference locate scoring."""
    folded_query, folded_text = query.casefold(), text.casefold()
    if not folded_query:
        return 0.0
    if folded_query in folded_text:
        return 1.0 + EXACT_BONUS
    return _lcs_length(folded_query, folded_text) / len(folded_query)


def match_segments(query: str, segments: Sequence[Sequence[object]], limit: int) -> list[dict[str, JSON]]:
    """Best-matching anchored segments above the score floor, ordered by score then source order."""
    scored: list[tuple[float, int, float, float, str]] = []
    for index, row in enumerate(segments):
        start, end, text = cast(float, row[0]), cast(float, row[1]), cast(str, row[2])
        score = score_segment(query, text)
        if score >= MIN_SCORE:
            scored.append((score, index, start, end, text))
    scored.sort(key=lambda item: (-item[0], item[1]))
    matches: list[dict[str, JSON]] = []
    for score, index, start, end, text in scored[:limit]:
        matches.append(
            {
                "segment_index": index,
                "start": start,
                "end": end,
                "score": round(score, 4),
                "text": text[:MAX_SNIPPET_CHARS],
                "truncated": len(text) > MAX_SNIPPET_CHARS,
            }
        )
    return matches


def _stamp(seconds: float) -> str:
    return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"


def render_locate(query: str, kind: str, matches: Sequence[Mapping[str, JSON]], total_segments: int) -> str:
    header = f'locate query="{query}" kind={kind} matches={len(matches)} of {total_segments} segments'
    if not matches:
        return header + "\nNo segment matched the query above the score floor."
    lines = [header]
    for match in matches:
        start, end = cast(float, match["start"]), cast(float, match["end"])
        score, text = cast(float, match["score"]), cast(str, match["text"])
        anchor = f"[{_stamp(start)}-{_stamp(end)}]" if kind == "asr" else "[untimed]"
        suffix = "..." if match["truncated"] else ""
        lines.append(f"{anchor} score={score:.2f} {text}{suffix}")
    return "\n".join(lines)
