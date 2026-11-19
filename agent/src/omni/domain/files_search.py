"""Bounded workspace text-search rules; no filesystem, network or storage I/O."""

from __future__ import annotations

SEARCH_QUERY_CHARS = 200
SEARCH_FILE_MATCHES = 5
SEARCH_SNIPPET_CHARS = 200


def normalize_query(value: object) -> str:
    """A bounded search query: stripped, 1-200 characters, no control bytes."""
    if not isinstance(value, str):
        raise ValueError("Search query must be a string")
    query = value.strip()
    if not 1 <= len(query) <= SEARCH_QUERY_CHARS or any(ord(c) < 32 for c in query):
        raise ValueError("Search query must be 1-200 characters without control bytes")
    return query


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
