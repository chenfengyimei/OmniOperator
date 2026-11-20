"""Bounded fuzzy search-replace patch rules; no filesystem, network or storage I/O."""

from __future__ import annotations

import bisect
import re
from collections.abc import Callable, Sequence
from difflib import SequenceMatcher

PATCH_STRING_CHARS = 64 * 1024
Span = tuple[int, int]
SIMILARITY_STRATEGIES = frozenset({"block_anchor", "context_aware"})
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


def _window_spans(content: str, n: int, accept: Callable[[int], bool]) -> list[Span]:
    content_lines = content.splitlines(keepends=True)
    if n <= 0 or n > len(content_lines):
        return []
    offsets = [0]
    for line in content_lines:
        offsets.append(offsets[-1] + len(line))
    return [(offsets[i], offsets[i + n]) for i in range(len(content_lines) - n + 1) if accept(i)]


def _block_anchor_spans(content: str, old_string: str) -> list[Span]:
    pattern_lines = _unicode_normalize(old_string).splitlines()
    n = len(pattern_lines)
    if n < 2:
        return []
    first_line = pattern_lines[0].strip()
    last_line = pattern_lines[-1].strip()
    norm_content_lines = [_unicode_normalize(line) for line in content.splitlines()]
    if n > len(norm_content_lines):
        return []
    potential = {
        i
        for i in range(len(norm_content_lines) - n + 1)
        if norm_content_lines[i].strip() == first_line and norm_content_lines[i + n - 1].strip() == last_line
    }
    if not potential:
        return []
    threshold = 0.50 if len(potential) == 1 else 0.70
    pattern_middle = "\n".join(pattern_lines[1:-1])

    def accept(i: int) -> bool:
        if i not in potential:
            return False
        if n <= 2:
            return True
        content_middle = "\n".join(norm_content_lines[i + 1 : i + n - 1])
        return SequenceMatcher(None, content_middle, pattern_middle).ratio() >= threshold

    return _window_spans(content, n, accept)


def _context_aware_spans(content: str, old_string: str) -> list[Span]:
    pattern_lines = old_string.splitlines()
    n = len(pattern_lines)
    content_cmp = content.splitlines()
    if n <= 0 or n > len(content_cmp):
        return []
    first_pat = pattern_lines[0].strip()
    last_pat = pattern_lines[-1].strip()

    def sim(a: str, b: str) -> float:
        return 1.0 if a == b else SequenceMatcher(None, a, b).ratio()

    def accept(i: int) -> bool:
        block = content_cmp[i : i + n]
        if sim(first_pat, block[0].strip()) < 0.80 or sim(last_pat, block[-1].strip()) < 0.80:
            return False
        return all(not p.strip() or sim(p.strip(), c.strip()) >= 0.80 for p, c in zip(pattern_lines, block))

    return _window_spans(content, n, accept)


def is_already_applied(content: str, old_string: str, new_string: str) -> bool:
    """Conservative: new_string non-trivial (>=8 chars) present exactly and old_string gone."""
    if not new_string or len(new_string.strip()) < 8 or new_string not in content:
        return False
    return old_string == new_string or old_string not in content


_BACKSLASH_RUN = re.compile(r"\\+")
_QUOTE_NOTE = (
    "escape-drift auto-corrected: stripped a spurious backslash before a quote/apostrophe "
    "in old_string/new_string (a JSON-double-escaping wire artifact) before matching."
)
_BACKSLASH_NOTE = (
    "escape-drift auto-corrected: halved doubled backslash runs in old_string "
    "(a JSON-double-escaping wire artifact) before matching; new_string had no backslashes."
)


def _halve_backslash_runs(text: str) -> str:
    return _BACKSLASH_RUN.sub(lambda m: "\\" * (len(m.group(0)) // 2), text)


def _all_backslash_runs_even(text: str) -> bool:
    return all(len(run) % 2 == 0 for run in _BACKSLASH_RUN.findall(text))


def _strip_spurious_quote_escapes(text: str) -> str:
    return text.replace("\\'", "'").replace('\\"', '"')


def _quote_escapes_all_spurious(*strings: str) -> bool:
    # The wire artifact escapes a touched quote kind at EVERY occurrence by exactly one
    # backslash; a bare quote or a longer run means an intended escape, indistinguishable.
    for quote in ("'", '"'):
        escaped = "\\" + quote
        if not any(escaped in s for s in strings):
            continue
        pattern = re.compile(r"(\\*)" + quote)
        if any(len(m.group(1)) != 1 for s in strings for m in pattern.finditer(s)):
            return False
    return True


def correct_escape_drift(content: str, old_string: str, new_string: str) -> tuple[str, str, str | None]:
    """Verified pre-match correction of JSON-double-escaping drift; raises PatchError when ambiguous.

    Corrections require the corrected old_string to appear verbatim in the file. New code's
    intended escaping cannot be proven, so a verified doubled anchor with replacement
    backslashes (or a quote kind only new_string escapes) is rejected, never guessed.
    """
    suspect = ("\\'" in old_string and "\\'" in new_string) or ('\\"' in old_string and '\\"' in new_string)
    if suspect and old_string not in content and _quote_escapes_all_spurious(old_string, new_string):
        stripped_old = _strip_spurious_quote_escapes(old_string)
        if stripped_old in content:
            unproven = [q for q in ("'", '"') if "\\" + q in new_string and "\\" + q not in old_string]
            if unproven:
                shown = " and ".join("\\" + q for q in unproven)
                raise PatchError(
                    "patch_escape_drift",
                    "Escape-drift detected: removing the spurious quote escapes in old_string matches "
                    f"the file, but new_string also contains {shown}, which old_string gives no evidence "
                    "for. Re-read the file and resend old_string/new_string with their intended escaping.",
                )
            return stripped_old, _strip_spurious_quote_escapes(new_string), _QUOTE_NOTE
    if "\\" in old_string and old_string not in content and _all_backslash_runs_even(old_string):
        halved_old = _halve_backslash_runs(old_string)
        if halved_old in content:
            if "\\" in new_string:
                raise PatchError(
                    "patch_escape_drift",
                    "Escape-drift detected: halving old_string's backslashes matches the file, "
                    "but correcting new_string could remove intentional backslashes. "
                    "Re-read the file and resend old_string/new_string with their intended escaping.",
                )
            return halved_old, new_string, _BACKSLASH_NOTE
    return old_string, new_string, None


def boundary_note(strategy: str) -> str | None:
    """Warning for spans located approximately; normalizing strategies match every line exactly."""
    if strategy not in SIMILARITY_STRATEGIES:
        return None
    return (
        f"Non-exact match (strategy: {strategy}). The edit span was located approximately, "
        "not matched to old_string's literal boundaries — review the change to confirm it landed "
        "exactly where intended (a shifted boundary can duplicate or drop a line just outside "
        "the edited region)."
    )


def _backslash_runs(text: str) -> list[int]:
    return [len(run) for run in _BACKSLASH_RUN.findall(text)]


def _matched_regions(content: str, spans: list[Span]) -> str:
    return "".join(content[start:end] for start, end in spans)


def _detect_newline_literal_drift(content: str, spans: list[Span], old_string: str, new_string: str) -> str | None:
    # Regions are compared one by one because joining them would multiply the file-side
    # count under replace_all; only a surplus of literal \n over the region is drift.
    if "\\n" not in new_string:
        return None
    old_literals = old_string.count("\\n")
    for start, end in spans:
        region = content[start:end]
        if "\n" in region and old_literals > region.count("\\n"):
            return (
                "Escape-drift detected: old_string contains more literal '\\n' sequences than the "
                "matched region of the file, which has real line breaks there instead. This is almost "
                "always a tool-call serialization artifact where a line break got escaped one extra "
                "time; new_string would write it as backslash + n. Re-read the file with files_read "
                "and pass old_string/new_string with actual line breaks, keeping only the '\\n' "
                "sequences that appear literally in the file."
            )
    return None


def _detect_backslash_doubling(regions: str, old_string: str, new_string: str) -> str | None:
    old_runs = _backslash_runs(old_string)
    file_runs = _backslash_runs(regions)
    if (
        not old_runs
        or not file_runs
        or len(old_runs) != len(file_runs)
        or old_runs == file_runs
        or any(o != f * 2 for o, f in zip(old_runs, file_runs))
        or not (any(f >= 2 for f in file_runs) or len(file_runs) >= 2)
        or _backslash_runs(new_string) == file_runs
    ):
        return None
    return (
        "Escape-drift detected: every backslash run in old_string is exactly twice as long as in "
        "the matched region of the file. The tool-call arguments were JSON-escaped one extra time; "
        "applying new_string verbatim would double every backslash in the file. Re-read the file "
        "with files_read and resend old_string/new_string with the backslash counts exactly as they "
        "appear in the file."
    )


def _detect_post_match_drift(content: str, spans: list[Span], old_string: str, new_string: str) -> str | None:
    """Reject replacement-side serialization artifacts a non-exact match would otherwise write."""
    has_quote_suspects = "\\'" in new_string or '\\"' in new_string
    if not has_quote_suspects and "\\" not in old_string:
        return None
    regions = _matched_regions(content, spans)
    if has_quote_suspects:
        for suspect in ("\\'", '\\"'):
            if suspect in new_string and suspect in old_string and suspect not in regions:
                plain = suspect[1]
                return (
                    f"Escape-drift detected: old_string and new_string contain the literal sequence "
                    f"{suspect!r} but the matched region of the file does not. This is almost always "
                    f"a tool-call serialization artifact where an apostrophe or quote got prefixed "
                    f"with a spurious backslash. Re-read the file with files_read and pass "
                    f"old_string/new_string without backslash-escaping {plain!r} characters."
                )
    newline_drift = _detect_newline_literal_drift(content, spans, old_string, new_string)
    if newline_drift:
        return newline_drift
    return _detect_backslash_doubling(regions, old_string, new_string)


def _maybe_unescape_new_string(new_string: str, content: str, spans: list[Span]) -> str:
    # \n is deliberately excluded: rewriting it would mangle source escape literals.
    if "\\t" not in new_string and "\\r" not in new_string:
        return new_string
    regions = _matched_regions(content, spans)
    for literal, control in (("\\t", "\t"), ("\\r", "\r")):
        if literal in new_string and control in regions:
            new_string = new_string.replace(literal, control)
    return new_string


def _leading_whitespace(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def _first_meaningful_line(text: str) -> str | None:
    return next((line for line in text.split("\n") if line.strip()), None)


def _match_starts_mid_line(content: str, start: int, end: int) -> bool:
    # Leading newlines/blank lines don't make an anchor partial-line: the first code line
    # can still be captured whole and need its base indent re-anchored.
    first_line_start = start
    for line in content[start:end].split("\n"):
        if line.strip():
            line_start = content.rfind("\n", 0, first_line_start) + 1
            return bool(content[line_start:first_line_start].strip(" \t\r"))
        first_line_start += len(line) + 1
    return False


def _reindent_replacement(file_region: str, old_string: str, new_string: str, *, match_starts_mid_line: bool) -> str:
    """Re-anchor new_string's base indent onto the file region's after a non-exact match.

    Swaps the LLM base prefix (first non-blank old_string line) for the file's, preserving relative
    nesting; shallower lines anchor to the file base. A partial-line match (non-whitespace text before
    its first non-blank line) has no reliable base indent, so the supplied indentation is kept.
    """
    if not new_string or match_starts_mid_line:
        return new_string
    old_first = _first_meaningful_line(old_string)
    file_first = _first_meaningful_line(file_region)
    if old_first is None or file_first is None:
        return new_string
    old_indent = _leading_whitespace(old_first)
    file_indent = _leading_whitespace(file_first)
    if old_indent == file_indent:
        return new_string
    out_lines: list[str] = []
    for line in new_string.split("\n"):
        if not line.strip():
            out_lines.append(line)
        elif _leading_whitespace(line).startswith(old_indent):
            out_lines.append(file_indent + line[len(old_indent) :])
        else:
            out_lines.append(file_indent + line.lstrip(" \t"))
    return "\n".join(out_lines)


_Opcode = tuple[str, int, int, int, int]
_UnicodePlan = tuple[str, Sequence[_Opcode]]


def _unicode_edit_plan(old_string: str, new_string: str) -> _UnicodePlan:
    """(normalized old, old->new opcodes), region-independent so replace_all computes it once."""
    norm_old = _unicode_normalize(old_string)
    return norm_old, SequenceMatcher(None, norm_old, new_string).get_opcodes()


def _preserve_unicode_in_replacement(file_region: str, new_string: str, plan: _UnicodePlan) -> str:
    """Apply only the old->new edits onto the region's original text so a unicode_normalized match
    keeps the file's em-dashes/smart quotes instead of flattening them to new_string's ASCII.
    """
    norm_old, opcodes = plan
    if norm_old != _unicode_normalize(file_region):
        return new_string
    file_orig_to_norm = _build_orig_to_norm_map(file_region)
    parts: list[str] = []
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            orig_start = bisect.bisect_right(file_orig_to_norm, i1) - 1
            orig_end = _norm_end_to_orig(file_orig_to_norm, orig_start, i2)
            parts.append(file_region[orig_start:orig_end])
        elif tag != "delete":
            parts.append(new_string[j1:j2])
    return "".join(parts)


def patch_replace(
    content: str, old_string: object, new_string: object, *, replace_all: bool = False
) -> tuple[str, int, str, str | None]:
    """Unique fuzzy search-replace via an ordered normalizing strategy chain.

    Returns (new_content, match_count, strategy, note). Verified escape-drift correction runs
    before matching; the note reports any correction and/or an approximate-boundary warning.
    Ambiguous matches are rejected unless replace_all; approximate similarity matches
    (block_anchor/context_aware) never allow replace_all. Blank/identical anchors, ambiguous
    escape drift and no-match are explicit errors, never silent edits.
    """
    if not isinstance(old_string, str) or not isinstance(new_string, str):
        raise PatchError("patch_invalid", "old_string and new_string must be strings")
    if not old_string or not old_string.strip():
        raise PatchError("patch_invalid", "old_string must be non-blank text to match")
    if len(old_string) > PATCH_STRING_CHARS or len(new_string) > PATCH_STRING_CHARS:
        raise PatchError("patch_invalid", "old_string/new_string exceed the bounded length")
    old_string, new_string, correction = correct_escape_drift(content, old_string, new_string)
    if old_string == new_string:
        raise PatchError("patch_identical", "old_string and new_string are identical")
    strategies: tuple[tuple[str, Callable[[str, str], list[Span]]], ...] = (
        ("exact", _exact_spans),
        ("line_trimmed", _trimmed_spans),
        ("line_whitespace", _whitespace_spans),
        ("escape_normalized", _escape_spans),
        ("unicode_normalized", _unicode_spans),
        ("block_anchor", _block_anchor_spans),
        ("context_aware", _context_aware_spans),
    )
    for name, find in strategies:
        spans = find(content, old_string)
        if not spans:
            continue
        if len(spans) > 1 and (not replace_all or name in SIMILARITY_STRATEGIES):
            hint = (
                "approximate matches cannot use replace_all; provide the precise text"
                if name in SIMILARITY_STRATEGIES
                else "provide more context or use replace_all"
            )
            raise PatchError("patch_ambiguous", f"Found {len(spans)} matches for old_string; {hint}")
        effective_new = new_string
        if name != "exact":
            drift = _detect_post_match_drift(content, spans, old_string, new_string)
            if drift:
                raise PatchError("patch_escape_drift", drift)
            effective_new = _maybe_unescape_new_string(new_string, content, spans)
        unicode_plan = _unicode_edit_plan(old_string, effective_new) if name == "unicode_normalized" else None
        patched = content
        for start, end in reversed(spans):
            adjusted = effective_new
            if name != "exact":
                region = content[start:end]
                if unicode_plan is not None:
                    adjusted = _preserve_unicode_in_replacement(region, adjusted, unicode_plan)
                adjusted = _reindent_replacement(
                    region,
                    old_string,
                    adjusted,
                    match_starts_mid_line=_match_starts_mid_line(content, start, end),
                )
            patched = patched[:start] + adjusted + patched[end:]
        note = "; ".join(part for part in (correction, boundary_note(name)) if part) or None
        return patched, len(spans), name, note
    if is_already_applied(content, old_string, new_string):
        raise PatchError("patch_already_applied", "The replacement is already present and old_string is gone")
    raise PatchError("patch_no_match", "Could not find a match for old_string in the file")
