"""Bounded fuzzy search-replace patch rules; no filesystem, network or storage I/O."""

from __future__ import annotations

import re
from collections.abc import Callable

PATCH_STRING_CHARS = 64 * 1024
Span = tuple[int, int]
UNICODE_MAP = {
    "\u201c": '"',
    "\u201d": '"',
    "\u2018": "'",
    "\u2019": "'",
    "\u2014": "--",
    "\u2013": "-",
    "\u2026": "...",
    "\u00a0": " ",
    "\u2212": "-",
    "\u2000": " ",
    "\u2001": " ",
    "\u2002": " ",
    "\u2003": " ",
    "\u2004": " ",
    "\u2005": " ",
    "\u2006": " ",
    "\u2007": " ",
    "\u2008": " ",
    "\u2009": " ",
    "\u200a": " ",
    "\u202f": " ",
    "\u205f": " ",
    "\u3000": " ",
}


class PatchError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _strip_line(line: str) -> str:
    return line.strip()


def _normalize_line(line: str) -> str:
    return re.sub(r"[ \t]+", " ", line).strip()


def _exact_spans(content: str, old_string: str) -> list[Span]:
    return [match.span() for match in re.finditer(re.escape(old_string), content)]


def _line_spans(content: str, old_string: str, transform: Callable[[str], str]) -> list[Span]:
    content_lines = content.splitlines(keepends=True)
    old_lines = old_string.splitlines()
    count = len(old_lines)
    if count == 0:
        return []
    offsets = [0]
    for line in content_lines:
        offsets.append(offsets[-1] + len(line))
    targets = [transform(line) for line in old_lines]
    spans: list[Span] = []
    index = 0
    while index <= len(content_lines) - count:
        if all(transform(content_lines[index + offset]) == targets[offset] for offset in range(count)):
            spans.append((offsets[index], offsets[index + count]))
            index += count
        else:
            index += 1
    return spans


def _trimmed_spans(content: str, old_string: str) -> list[Span]:
    return _line_spans(content, old_string, _strip_line)


def _whitespace_spans(content: str, old_string: str) -> list[Span]:
    return _line_spans(content, old_string, _normalize_line)


def _escape_spans(content: str, old_string: str) -> list[Span]:
    unescaped = old_string.replace("\\n", "\n").replace("\\t", "\t").replace("\\r", "\r")
    if unescaped == old_string:
        return []
    return _exact_spans(content, unescaped)


def _unicode_normalize(text: str) -> str:
    for char, repl in UNICODE_MAP.items():
        text = text.replace(char, repl)
    return text


def _build_orig_to_norm_map(original: str) -> list[int]:
    result: list[int] = []
    norm_pos = 0
    for char in original:
        result.append(norm_pos)
        repl = UNICODE_MAP.get(char)
        norm_pos += len(repl) if repl is not None else 1
    result.append(norm_pos)
    return result


def _invert_norm_map(orig_to_norm: list[int]) -> dict[int, int]:
    inverted: dict[int, int] = {}
    for orig_pos, norm_pos in enumerate(orig_to_norm[:-1]):
        inverted.setdefault(norm_pos, orig_pos)
    return inverted


def _norm_end_to_orig(orig_to_norm: list[int], orig_start: int, norm_end: int) -> int:
    orig_len = len(orig_to_norm) - 1
    orig_end = orig_start
    while orig_end < orig_len and orig_to_norm[orig_end] < norm_end:
        orig_end += 1
    return orig_end


def _map_positions_norm_to_orig(orig_to_norm: list[int], norm_matches: list[Span]) -> list[Span]:
    norm_to_orig_start = _invert_norm_map(orig_to_norm)
    results: list[Span] = []
    for norm_start, norm_end in norm_matches:
        if norm_start in norm_to_orig_start:
            orig_start = norm_to_orig_start[norm_start]
            results.append((orig_start, _norm_end_to_orig(orig_to_norm, orig_start, norm_end)))
    return results


def _unicode_spans(content: str, old_string: str) -> list[Span]:
    norm_pattern = _unicode_normalize(old_string)
    norm_content = _unicode_normalize(content)
    if norm_content == content and norm_pattern == old_string:
        return []
    norm_matches = _exact_spans(norm_content, norm_pattern) or _trimmed_spans(norm_content, norm_pattern)
    if not norm_matches:
        return []
    return _map_positions_norm_to_orig(_build_orig_to_norm_map(content), norm_matches)


def is_already_applied(content: str, old_string: str, new_string: str) -> bool:
    """Conservative: new_string non-trivial (>=8 chars) present exactly and old_string gone."""
    if not new_string or len(new_string.strip()) < 8 or new_string not in content:
        return False
    return old_string == new_string or old_string not in content


def patch_replace(
    content: str, old_string: object, new_string: object, *, replace_all: bool = False
) -> tuple[str, int, str]:
    """Unique fuzzy search-replace via an ordered normalizing strategy chain.

    Returns (new_content, match_count, strategy). Ambiguous matches are rejected unless
    replace_all; blank/identical anchors and no-match are explicit errors, never silent edits.
    """
    if not isinstance(old_string, str) or not isinstance(new_string, str):
        raise PatchError("patch_invalid", "old_string and new_string must be strings")
    if not old_string or not old_string.strip():
        raise PatchError("patch_invalid", "old_string must be non-blank text to match")
    if len(old_string) > PATCH_STRING_CHARS or len(new_string) > PATCH_STRING_CHARS:
        raise PatchError("patch_invalid", "old_string/new_string exceed the bounded length")
    if old_string == new_string:
        raise PatchError("patch_identical", "old_string and new_string are identical")
    strategies: tuple[tuple[str, Callable[[str, str], list[Span]]], ...] = (
        ("exact", _exact_spans),
        ("line_trimmed", _trimmed_spans),
        ("line_whitespace", _whitespace_spans),
        ("escape_normalized", _escape_spans),
        ("unicode_normalized", _unicode_spans),
    )
    for name, find in strategies:
        spans = find(content, old_string)
        if not spans:
            continue
        if len(spans) > 1 and not replace_all:
            raise PatchError(
                "patch_ambiguous",
                f"Found {len(spans)} matches for old_string; provide more context or use replace_all",
            )
        patched = content
        for start, end in reversed(spans):
            patched = patched[:start] + new_string + patched[end:]
        return patched, len(spans), name
    if is_already_applied(content, old_string, new_string):
        raise PatchError("patch_already_applied", "The replacement is already present and old_string is gone")
    raise PatchError("patch_no_match", "Could not find a match for old_string in the file")
