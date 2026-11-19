"""Bounded fuzzy search-replace patch rules; no filesystem, network or storage I/O."""

from __future__ import annotations

import re
from collections.abc import Callable

PATCH_STRING_CHARS = 64 * 1024
Span = tuple[int, int]


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
