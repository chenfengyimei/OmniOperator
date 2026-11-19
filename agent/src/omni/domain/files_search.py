"""Bounded workspace search rules (content substring + filename glob); no filesystem, network or storage I/O."""

from __future__ import annotations

import fnmatch

SEARCH_QUERY_CHARS = 200
SEARCH_FILE_MATCHES = 5
SEARCH_SNIPPET_CHARS = 200
SEARCH_OUTPUT_MODES = ("content", "files_with_matches", "count")


def normalize_query(value: object) -> str:
    """A bounded search query: stripped, 1-200 characters, no control bytes."""
    if not isinstance(value, str):
        raise ValueError("Search query must be a string")
    query = value.strip()
    if not 1 <= len(query) <= SEARCH_QUERY_CHARS or any(ord(c) < 32 for c in query):
        raise ValueError("Search query must be 1-200 characters without control bytes")
    return query


def normalize_output_mode(value: object) -> str:
    """Content-search result shape: matching lines (default), distinct paths, or per-file match counts."""
    if value is None:
        return "content"
    if not isinstance(value, str) or value not in SEARCH_OUTPUT_MODES:
        raise ValueError("Search output_mode must be content, files_with_matches or count")
    return value


def match_lines(text: str, query: str) -> list[dict[str, object]]:
    """Case-insensitive substring matches with 1-based line numbers and bounded snippets.

    At most SEARCH_FILE_MATCHES hits per file so one large file cannot exhaust the result budget.
    """
    needle = query.casefold()
    hits: list[dict[str, object]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if needle in line.casefold():
            hits.append({"line": lineno, "text": line.strip()[:SEARCH_SNIPPET_CHARS]})
            if len(hits) >= SEARCH_FILE_MATCHES:
                break
    return hits


def match_name(filename: str, pattern: str) -> bool:
    """Case-sensitive glob against the file basename; a directory prefix in the pattern is stripped (rg --files semantics)."""
    return fnmatch.fnmatchcase(filename, pattern.rsplit("/", 1)[-1])


def count_matches(text: str, query: str) -> int:
    """Total matching lines in one file (case-insensitive substring); the file is already size-bounded by the caller."""
    needle = query.casefold()
    return sum(1 for line in text.splitlines() if needle in line.casefold())
