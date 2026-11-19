"""Bounded workspace search rules (content substring/regex + filename glob); no filesystem, network or storage I/O."""

from __future__ import annotations

import fnmatch
import importlib
import re
from collections.abc import Callable, Iterable
from typing import Any, Protocol, cast


class _RegexParser(Protocol):
    MAXREPEAT: int

    def parse(self, pattern: str) -> Iterable[tuple[object, Any]]: ...


# re._parser has no typeshed stubs; the typed protocol keeps the safety gate fully checked.
_re_parser = cast(_RegexParser, importlib.import_module("re._parser"))

SEARCH_QUERY_CHARS = 200
SEARCH_FILE_MATCHES = 5
SEARCH_SNIPPET_CHARS = 200
SEARCH_OUTPUT_MODES = ("content", "files_with_matches", "count")
SEARCH_CONTEXT_LINES = 3
SEARCH_MAX_OFFSET = 1000
SEARCH_ORDERS = ("discovery", "modified")
SEARCH_REGEX_MAX_REPEAT = 1024
_REGEX_REPEATS = frozenset({"MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"})


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


def normalize_context(value: object) -> int:
    """Bounded neighbor lines around each content match (0 disables); keeps the response within the IPC frame."""
    if value is None:
        return 0
    if type(value) is not int or not 0 <= value <= SEARCH_CONTEXT_LINES:
        raise ValueError("Search context must be an integer from 0 to 3")
    return value


def normalize_offset(value: object) -> int:
    """Bounded result offset for pagination (0 disables); skips the first N matches before the 50-result page."""
    if value is None:
        return 0
    if type(value) is not int or not 0 <= value <= SEARCH_MAX_OFFSET:
        raise ValueError("Search offset must be an integer from 0 to 1000")
    return value


def normalize_order(value: object) -> str:
    """Result ordering: discovery (default, bounded traversal order) or modified (newest-first by file mtime)."""
    if value is None:
        return "discovery"
    if not isinstance(value, str) or value not in SEARCH_ORDERS:
        raise ValueError("Search order must be discovery or modified")
    return value


def normalize_file_glob(value: object) -> str | None:
    """Optional bounded include glob for content search (rg --glob); None disables the filter."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Search file_glob must be a string")
    pattern = value.strip()
    if not 1 <= len(pattern) <= SEARCH_QUERY_CHARS or any(ord(c) < 32 for c in pattern):
        raise ValueError("Search file_glob must be 1-200 characters without control bytes")
    return pattern


def normalize_use_regex(value: object) -> bool:
    """Opt-in regex mode for content search; default False keeps the substring contract byte-identical."""
    if value is None:
        return False
    if type(value) is not bool:
        raise ValueError("Search use_regex must be a boolean")
    return value


def _reject_unbounded_regex(items: Iterable[tuple[object, Any]], guarded: bool) -> None:
    # guarded means inside a quantifier or lookaround, where another quantifier/alternation
    # could make matching super-linear on a 256KB single-line file.
    for op, av in items:
        name = str(op)
        if name in _REGEX_REPEATS:
            if guarded:
                raise ValueError("Search regex must not nest quantifiers or quantify alternations/lookarounds")
            _, hi, sub = av
            if hi != _re_parser.MAXREPEAT and hi > SEARCH_REGEX_MAX_REPEAT:
                raise ValueError("Search regex repeat bound exceeds 1024")
            _reject_unbounded_regex(sub, True)
        elif name == "BRANCH":
            if guarded:
                raise ValueError("Search regex must not nest quantifiers or quantify alternations/lookarounds")
            for branch in av[1]:
                _reject_unbounded_regex(branch, guarded)
        elif name in {"ASSERT", "ASSERT_NOT"}:
            if guarded:
                raise ValueError("Search regex must not nest quantifiers or quantify alternations/lookarounds")
            _reject_unbounded_regex(av[1], True)
        elif name == "SUBPATTERN":
            _reject_unbounded_regex(av[3], guarded)
        elif name == "ATOMIC_GROUP":
            _reject_unbounded_regex(av, guarded)
        elif name in {"GROUPREF", "GROUPREF_EXISTS"}:
            raise ValueError("Search regex must not use backreferences")


def compile_search_regex(query: str) -> re.Pattern[str]:
    """Case-sensitive content regex behind a conservative static safety gate (not full ripgrep parity).

    Rejects backreferences/conditionals, nested quantifiers, alternations or lookarounds under a
    quantifier, quantifiers/alternations inside lookarounds, and finite repeats above 1024, so the
    accepted patterns stay near-linear on the caller's 256KB-bounded files.
    """
    try:
        parsed = _re_parser.parse(query)
    except re.error as exc:
        raise ValueError("Search regex is not a valid pattern") from exc
    _reject_unbounded_regex(parsed, False)
    return re.compile(query)


def match_path_glob(relative: str, pattern: str) -> bool:
    """Case-sensitive glob against the workspace-relative POSIX path (rg --glob include semantics, linear time)."""
    return fnmatch.fnmatchcase(relative, pattern)


def _line_hits(lines: list[str], matches: Callable[[str], bool], context: int) -> list[dict[str, object]]:
    hits: list[dict[str, object]] = []
    for index, line in enumerate(lines):
        if matches(line):
            hit: dict[str, object] = {"line": index + 1, "text": line.strip()[:SEARCH_SNIPPET_CHARS]}
            if context > 0:
                hit["before"] = [
                    {"line": j + 1, "text": lines[j].strip()[:SEARCH_SNIPPET_CHARS]}
                    for j in range(max(0, index - context), index)
                ]
                hit["after"] = [
                    {"line": j + 1, "text": lines[j].strip()[:SEARCH_SNIPPET_CHARS]}
                    for j in range(index + 1, min(len(lines), index + 1 + context))
                ]
            hits.append(hit)
            if len(hits) >= SEARCH_FILE_MATCHES:
                break
    return hits


def match_lines(text: str, query: str, context: int = 0) -> list[dict[str, object]]:
    """Case-insensitive substring matches with 1-based line numbers and bounded snippets.

    At most SEARCH_FILE_MATCHES hits per file so one large file cannot exhaust the result budget.
    With context>0 each hit also carries bounded before/after neighbor lines (clamped to file edges).
    """
    needle = query.casefold()
    return _line_hits(text.splitlines(), lambda line: needle in line.casefold(), context)


def regex_match_lines(text: str, pattern: re.Pattern[str], context: int = 0) -> list[dict[str, object]]:
    """Case-sensitive regex line matches with the same bounded shape/cap as substring match_lines."""
    return _line_hits(text.splitlines(), lambda line: pattern.search(line) is not None, context)


def match_name(filename: str, pattern: str) -> bool:
    """Case-sensitive glob against the file basename; a directory prefix in the pattern is stripped (rg --files semantics)."""
    return fnmatch.fnmatchcase(filename, pattern.rsplit("/", 1)[-1])


def count_matches(text: str, query: str) -> int:
    """Total matching lines in one file (case-insensitive substring); the file is already size-bounded by the caller."""
    needle = query.casefold()
    return sum(1 for line in text.splitlines() if needle in line.casefold())


def regex_count_matches(text: str, pattern: re.Pattern[str]) -> int:
    """Total regex-matching lines in one file (case-sensitive); the file is already size-bounded by the caller."""
    return sum(1 for line in text.splitlines() if pattern.search(line) is not None)
