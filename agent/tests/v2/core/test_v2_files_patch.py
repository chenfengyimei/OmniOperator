"""Bounded fuzzy search-replace patch: pure strategy rules, real-filesystem CAS safety, and write-tier wiring."""

import asyncio
import hashlib
from pathlib import Path

import pytest

from omni.adapters.workers.main import Worker
from omni.domain.contracts import Effect, Outcome
from omni.domain.files_patch import (
    PATCH_STRING_CHARS,
    PatchError,
    _first_meaningful_line,
    _leading_whitespace,
    _match_starts_mid_line,
    _preserve_unicode_in_replacement,
    _reindent_replacement,
    _unicode_edit_plan,
    boundary_note,
    is_already_applied,
    patch_replace,
)
from omni.domain.routing import CHAT_TOOLS, RETRIEVAL_TOOLS, select_tools


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def put(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))


def test_exact_unique_and_partial_line_replace():
    assert patch_replace("hello world", "world", "there") == ("hello there", 1, "exact", None)
    assert patch_replace("foo bar baz", "bar", "QUX") == ("foo QUX baz", 1, "exact", None)


def test_exact_replace_all_is_non_overlapping():
    assert patch_replace("a a a", "a", "b", replace_all=True) == ("b b b", 3, "exact", None)
    assert patch_replace("ababab", "ab", "X", replace_all=True) == ("XXX", 3, "exact", None)


def test_exact_ambiguous_rejected_without_replace_all():
    with pytest.raises(PatchError) as caught:
        patch_replace("a a", "a", "b")
    assert caught.value.code == "patch_ambiguous"


def test_line_trimmed_matches_indent_drift():
    content = "def f():\n\treturn 1\n"
    out = patch_replace(content, "def f():\n    return 1\n", "def f():\n    return 2\n")
    # First meaningful line "def f():" is top-level in both, so reindent is a no-op here;
    # the replacement keeps new_string verbatim (reindent only re-anchors the base indent).
    assert out == ("def f():\n    return 2\n", 1, "line_trimmed", None)


def test_line_whitespace_matches_internal_drift():
    out = patch_replace("foo   bar\n", "foo bar\n", "foo baz\n")
    assert out == ("foo baz\n", 1, "line_whitespace", None)


def test_line_trimmed_ambiguous_rejected():
    with pytest.raises(PatchError) as caught:
        patch_replace("\ta\n\tb\n\ta\n\tb\n", "a\nb\n", "X\n")
    assert caught.value.code == "patch_ambiguous"


def test_multiline_exact_window_replace():
    assert patch_replace("a\nb\nc\nd\n", "b\nc", "X\nY") == ("a\nX\nY\nd\n", 1, "exact", None)


def test_strategy_precedence_exact_wins():
    assert patch_replace("foo bar", "foo bar", "X")[2] == "exact"


def test_no_match_rejected():
    with pytest.raises(PatchError) as caught:
        patch_replace("hello", "xyz", "abc")
    assert caught.value.code == "patch_no_match"


def test_already_applied_rejected():
    with pytest.raises(PatchError) as caught:
        patch_replace("replacement text", "original text", "replacement text")
    assert caught.value.code == "patch_already_applied"


def test_identical_blank_nonstring_and_oversize_anchors_rejected():
    for old, new, code in [
        ("same", "same", "patch_identical"),
        ("", "x", "patch_invalid"),
        ("   ", "x", "patch_invalid"),
        (5, "x", "patch_invalid"),
        ("x", 5, "patch_invalid"),
        ("x" * (PATCH_STRING_CHARS + 1), "y", "patch_invalid"),
    ]:
        with pytest.raises(PatchError) as caught:
            patch_replace("content", old, new)
        assert caught.value.code == code


def test_is_already_applied_is_conservative():
    assert is_already_applied("replacement text", "original text", "replacement text") is True
    assert is_already_applied("short", "old", "short") is False
    assert is_already_applied("old text here", "old text here", "new text") is False


def test_worker_patch_applies_with_correct_fingerprint(tmp_path):
    put(tmp_path / "a.txt", "hello world\n")

    async def scenario():
        result = await Worker().invoke(
            "files_patch",
            {"path": "a.txt", "old_string": "world", "new_string": "there", "expected_sha256": digest("hello world\n")},
            {"workspace": str(tmp_path)},
        )
        assert result.status == Outcome.OK and result.effect == Effect.APPLIED
        assert (tmp_path / "a.txt").read_bytes() == b"hello there\n"
        assert result.data["replacements"] == 1 and result.data["strategy"] == "exact"
        assert result.verification == result.data["sha256"] == digest("hello there\n")
        assert not list(tmp_path.glob(".omni-write-*"))

    asyncio.run(scenario())


def test_worker_patch_line_trimmed_indent_drift(tmp_path):
    put(tmp_path / "a.py", "\treturn 1\n")

    async def scenario():
        result = await Worker().invoke(
            "files_patch",
            {
                "path": "a.py",
                "old_string": "    return 1\n",
                "new_string": "    return 2\n",
                "expected_sha256": digest("\treturn 1\n"),
            },
            {"workspace": str(tmp_path)},
        )
        assert result.status == Outcome.OK and result.data["strategy"] == "line_trimmed"
        assert (tmp_path / "a.py").read_bytes() == b"\treturn 2\n"

    asyncio.run(scenario())


def test_worker_patch_replace_all(tmp_path):
    put(tmp_path / "a.txt", "a a a\n")

    async def scenario():
        result = await Worker().invoke(
            "files_patch",
            {
                "path": "a.txt",
                "old_string": "a",
                "new_string": "b",
                "replace_all": True,
                "expected_sha256": digest("a a a\n"),
            },
            {"workspace": str(tmp_path)},
        )
        assert result.status == Outcome.OK and result.data["replacements"] == 3
        assert (tmp_path / "a.txt").read_bytes() == b"b b b\n"

    asyncio.run(scenario())


@pytest.mark.parametrize("expected", ["0" * 64, None])
def test_worker_patch_wrong_or_missing_fingerprint_rejected(tmp_path, expected):
    put(tmp_path / "a.txt", "hello world\n")
    args = {"path": "a.txt", "old_string": "world", "new_string": "there"}
    if expected is not None:
        args["expected_sha256"] = expected

    async def scenario():
        result = await Worker().invoke("files_patch", args, {"workspace": str(tmp_path)})
        assert result.status == Outcome.ERROR and result.effect == Effect.NONE
        assert (tmp_path / "a.txt").read_bytes() == b"hello world\n"

    asyncio.run(scenario())


def test_worker_patch_ambiguous_and_no_match_leave_file_unchanged(tmp_path):
    put(tmp_path / "a.txt", "a a\n")

    async def scenario():
        ambiguous = await Worker().invoke(
            "files_patch",
            {"path": "a.txt", "old_string": "a", "new_string": "b", "expected_sha256": digest("a a\n")},
            {"workspace": str(tmp_path)},
        )
        assert ambiguous.status == Outcome.ERROR and ambiguous.effect == Effect.NONE
        assert ambiguous.error_code == "patch_ambiguous"
        nomatch = await Worker().invoke(
            "files_patch",
            {"path": "a.txt", "old_string": "xyz", "new_string": "b", "expected_sha256": digest("a a\n")},
            {"workspace": str(tmp_path)},
        )
        assert nomatch.error_code == "patch_no_match" and nomatch.effect == Effect.NONE
        assert (tmp_path / "a.txt").read_bytes() == b"a a\n"

    asyncio.run(scenario())


def test_worker_patch_rejects_private_binary_oversize_missing_and_nonbool(tmp_path):
    put(tmp_path / ".env", "secret world\n")
    (tmp_path / "bin.dat").write_bytes(b"\xff\xfe world")
    put(tmp_path / "big.txt", "world " + "x" * 300000)
    put(tmp_path / "ok.txt", "world\n")

    async def scenario():
        worker = Worker()
        context = {"workspace": str(tmp_path)}
        private = await worker.invoke(
            "files_patch",
            {"path": ".env", "old_string": "world", "new_string": "x", "expected_sha256": digest("secret world\n")},
            context,
        )
        binary = await worker.invoke(
            "files_patch",
            {
                "path": "bin.dat",
                "old_string": "world",
                "new_string": "x",
                "expected_sha256": hashlib.sha256(b"\xff\xfe world").hexdigest(),
            },
            context,
        )
        oversize = await worker.invoke(
            "files_patch",
            {
                "path": "big.txt",
                "old_string": "world",
                "new_string": "x",
                "expected_sha256": digest("world " + "x" * 300000),
            },
            context,
        )
        missing = await worker.invoke(
            "files_patch",
            {"path": "gone.txt", "old_string": "world", "new_string": "x", "expected_sha256": digest("world\n")},
            context,
        )
        nonbool = await worker.invoke(
            "files_patch",
            {
                "path": "ok.txt",
                "old_string": "world",
                "new_string": "x",
                "replace_all": "yes",
                "expected_sha256": digest("world\n"),
            },
            context,
        )
        for result in (private, binary, oversize, missing, nonbool):
            assert result.status == Outcome.ERROR and result.effect == Effect.NONE
        assert (tmp_path / ".env").read_bytes() == b"secret world\n"
        assert (tmp_path / "ok.txt").read_bytes() == b"world\n"

    asyncio.run(scenario())


def test_files_patch_is_registered_write_and_agent_tier_only(tmp_path):
    from omni_test_support.v2.documents import Clock, DocumentService, opened

    from omni.composition.capabilities import builtin_capabilities

    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, objects, _agent):
            specs = tuple(c.spec for c in builtin_capabilities(db, DocumentService(db, objects)))
            spec = next(s for s in specs if s.name == "files_patch")
            assert spec.writes is True and spec.resource == "workspace"
            assert "files_patch" not in RETRIEVAL_TOOLS and "files_patch" not in CHAT_TOOLS
            assert "files_patch" in [t.name for t in select_tools("agent", specs, escalate=False)]
            assert "files_patch" not in [t.name for t in select_tools("retrieval", specs, escalate=False)]
            assert "files_patch" not in [t.name for t in select_tools("chat", specs, escalate=False)]

    asyncio.run(scenario())


def test_escape_normalized_matches_literal_newline_and_tab():
    assert patch_replace("a\nb\n", "a\\nb", "X") == ("X\n", 1, "escape_normalized", None)
    assert patch_replace("a\tb\n", "a\\tb", "X") == ("X\n", 1, "escape_normalized", None)


def test_escape_normalized_replace_all_non_overlapping():
    assert patch_replace("a\nb a\nb", "a\\nb", "X", replace_all=True) == ("X X", 2, "escape_normalized", None)


def test_escape_normalized_only_fires_for_literal_escapes():
    assert patch_replace("a\nb\n", "a\nb", "X") == ("X\n", 1, "exact", None)


def test_worker_patch_escape_normalized(tmp_path):
    put(tmp_path / "a.txt", "a\nb\n")

    async def scenario():
        result = await Worker().invoke(
            "files_patch",
            {"path": "a.txt", "old_string": "a\\nb", "new_string": "X", "expected_sha256": digest("a\nb\n")},
            {"workspace": str(tmp_path)},
        )
        assert result.status == Outcome.OK and result.effect == Effect.APPLIED
        assert result.data["strategy"] == "escape_normalized"
        assert (tmp_path / "a.txt").read_bytes() == b"X\n"

    asyncio.run(scenario())


def test_unicode_normalized_smart_quotes():
    assert patch_replace("\u201cfoo\u201d\n", '"foo"', "bar") == ("bar\n", 1, "unicode_normalized", None)


def test_unicode_normalized_em_dash_expansion_span():
    assert patch_replace("a\u2014b\n", "a--b", "X") == ("X\n", 1, "unicode_normalized", None)


def test_unicode_normalized_ellipsis_expansion_span():
    assert patch_replace("a\u2026b\n", "a...b", "X") == ("X\n", 1, "unicode_normalized", None)


def test_unicode_normalized_line_trimmed_fallback():
    assert patch_replace("\u201cfoo\u201d  \n", '"foo"\n', "bar\n") == ("bar\n", 1, "unicode_normalized", None)


def test_unicode_normalized_ambiguous_rejected():
    with pytest.raises(PatchError) as caught:
        patch_replace("\u201cfoo\u201d \u201cfoo\u201d", '"foo"', "bar")
    assert caught.value.code == "patch_ambiguous"


def test_unicode_normalized_ascii_exact_wins():
    assert patch_replace('"foo"\n', '"foo"', "bar")[2] == "exact"


def test_worker_patch_unicode_normalized(tmp_path):
    put(tmp_path / "a.txt", "\u201cfoo\u201d\n")

    async def scenario():
        result = await Worker().invoke(
            "files_patch",
            {
                "path": "a.txt",
                "old_string": '"foo"',
                "new_string": "bar",
                "expected_sha256": digest("\u201cfoo\u201d\n"),
            },
            {"workspace": str(tmp_path)},
        )
        assert result.status == Outcome.OK and result.data["strategy"] == "unicode_normalized"
        assert (tmp_path / "a.txt").read_bytes() == b"bar\n"

    asyncio.run(scenario())


def test_indentation_drift_subsumed_by_line_trimmed():
    # Reference indentation_flexible (lstrip) is provably subsumed by line_trimmed (strip-all).
    # Reindent anchors the unindented replacement onto the file's tab base.
    assert patch_replace("\tfoo\n", "    foo\n", "bar\n") == ("\tbar\n", 1, "line_trimmed", None)


def test_block_anchor_unique_middle_drift():
    out = patch_replace("head\nmiddle one\nfoot\n", "head\nmiddle two\nfoot\n", "head\nREPLACED\nfoot\n")
    assert out == ("head\nREPLACED\nfoot\n", 1, "block_anchor", boundary_note("block_anchor"))


def test_block_anchor_rejects_low_similarity_middle():
    with pytest.raises(PatchError) as caught:
        patch_replace("head\nxxxxxxxxxxxxxxxxxxxx\nfoot\n", "head\nmiddle two\nfoot\n", "X\n")
    assert caught.value.code == "patch_no_match"


def test_block_anchor_multiple_anchors_ambiguous_even_replace_all():
    content = "head\nmiddle one\nfoot\nhead\nmiddle one\nfoot\n"
    for replace_all in (False, True):
        with pytest.raises(PatchError) as caught:
            patch_replace(content, "head\nmiddle two\nfoot\n", "X\n", replace_all=replace_all)
        assert caught.value.code == "patch_ambiguous"


def test_context_aware_approximate_boundary_lines():
    out = patch_replace("head one\nmiddle\nfoot one\n", "head onX\nmiddle\nfoot onX\n", "REPLACED\n")
    assert out == ("REPLACED\n", 1, "context_aware", boundary_note("context_aware"))


def test_context_aware_rejects_low_similarity_middle_line():
    with pytest.raises(PatchError) as caught:
        patch_replace("head\nxxxxxxxxxxxxxxxxxxxx\nfoot\n", "head\nmiddle\nfoot\n", "X\n")
    assert caught.value.code == "patch_no_match"


def test_context_aware_single_line_last_resort():
    assert patch_replace("hello world\n", "hello worlds\n", "X\n") == (
        "X\n",
        1,
        "context_aware",
        boundary_note("context_aware"),
    )


def test_precise_strategy_wins_over_similarity():
    assert patch_replace("head\nmiddle one\nfoot\n", "head\nmiddle one\nfoot\n", "X\n")[2] == "exact"


def test_worker_patch_block_anchor(tmp_path):
    put(tmp_path / "a.txt", "head\nmiddle one\nfoot\n")

    async def scenario():
        result = await Worker().invoke(
            "files_patch",
            {
                "path": "a.txt",
                "old_string": "head\nmiddle two\nfoot\n",
                "new_string": "head\nREPLACED\nfoot\n",
                "expected_sha256": digest("head\nmiddle one\nfoot\n"),
            },
            {"workspace": str(tmp_path)},
        )
        assert result.status == Outcome.OK and result.effect == Effect.APPLIED
        assert result.data["strategy"] == "block_anchor"
        assert (tmp_path / "a.txt").read_bytes() == b"head\nREPLACED\nfoot\n"

    asyncio.run(scenario())


def test_worker_patch_similarity_ambiguous_leaves_file_unchanged(tmp_path):
    content = "head\nmiddle one\nfoot\nhead\nmiddle one\nfoot\n"
    put(tmp_path / "a.txt", content)

    async def scenario():
        result = await Worker().invoke(
            "files_patch",
            {
                "path": "a.txt",
                "old_string": "head\nmiddle two\nfoot\n",
                "new_string": "X\n",
                "replace_all": True,
                "expected_sha256": digest(content),
            },
            {"workspace": str(tmp_path)},
        )
        assert result.status == Outcome.ERROR and result.effect == Effect.NONE
        assert result.error_code == "patch_ambiguous"
        assert (tmp_path / "a.txt").read_bytes() == content.encode("utf-8")

    asyncio.run(scenario())


def test_quote_drift_corrected_when_verified_against_file():
    out = patch_replace("say 'hi'\n", "say \\'hi\\'", "say \\'yo\\'")
    assert out == ("say 'yo'\n", 1, "exact", out[3])
    assert isinstance(out[3], str) and "escape-drift auto-corrected" in out[3] and "quote" in out[3]


def test_quote_drift_skipped_when_anchor_already_exact():
    assert patch_replace("say \\'hi\\'\n", "say \\'hi\\'", "X") == ("X\n", 1, "exact", None)


def test_quote_drift_skipped_when_bare_quote_mixes_with_escaped():
    with pytest.raises(PatchError) as caught:
        patch_replace("it's a 'test' plus more words here\n", "it\\'s a 'test'", "X")
    assert caught.value.code == "patch_no_match"


def test_quote_drift_skipped_for_longer_backslash_run():
    out = patch_replace("say 'hi'\n", "say \\\\'hi\\\\'", "X")
    assert out[2] == "context_aware"
    assert "escape-drift" not in (out[3] or "")


def test_quote_drift_unproven_replacement_kind_rejected():
    with pytest.raises(PatchError) as caught:
        patch_replace("say 'hi'\n", "say \\'hi\\'", "say \\'yo\\' " + '\\"end\\"')
    assert caught.value.code == "patch_escape_drift"


def test_backslash_doubling_halved_when_verified():
    out = patch_replace("path C:\\temp\n", "path C:\\\\temp", "path D:/tmp")
    assert out == ("path D:/tmp\n", 1, "exact", out[3])
    assert isinstance(out[3], str) and "halved doubled backslash" in out[3]


def test_backslash_doubling_with_replacement_backslashes_rejected():
    with pytest.raises(PatchError) as caught:
        patch_replace("path C:\\temp\n", "path C:\\\\temp", "path D:\\tmp")
    assert caught.value.code == "patch_escape_drift"


def test_backslash_odd_runs_not_corrected():
    out = patch_replace("x\\\\y\n", "x\\y", "Z")
    assert out[2] == "context_aware"
    assert "escape-drift" not in (out[3] or "")


def test_backslash_halved_anchor_absent_not_corrected():
    with pytest.raises(PatchError) as caught:
        patch_replace("zzz\n", "a\\\\b", "X")
    assert caught.value.code == "patch_no_match"


def test_correction_then_identical_rejected():
    with pytest.raises(PatchError) as caught:
        patch_replace("say 'hi'\n", "say \\'hi\\'", "say \\'hi\\'")
    assert caught.value.code == "patch_identical"


def test_boundary_note_only_for_similarity_strategies():
    assert boundary_note("exact") is None
    assert boundary_note("line_trimmed") is None
    assert boundary_note("unicode_normalized") is None
    for name in ("block_anchor", "context_aware"):
        note = boundary_note(name)
        assert isinstance(note, str) and name in note and "approximately" in note


def test_worker_patch_quote_drift_applied_with_note(tmp_path):
    put(tmp_path / "a.txt", "say 'hi'\n")

    async def scenario():
        result = await Worker().invoke(
            "files_patch",
            {
                "path": "a.txt",
                "old_string": "say \\'hi\\'",
                "new_string": "say \\'yo\\'",
                "expected_sha256": digest("say 'hi'\n"),
            },
            {"workspace": str(tmp_path)},
        )
        assert result.status == Outcome.OK and result.effect == Effect.APPLIED
        assert (tmp_path / "a.txt").read_bytes() == b"say 'yo'\n"
        assert isinstance(result.data["note"], str) and "escape-drift auto-corrected" in result.data["note"]
        assert result.data["note"] in result.text
        assert result.verification == digest("say 'yo'\n")

    asyncio.run(scenario())


def test_worker_patch_ambiguous_drift_rejected_before_write(tmp_path):
    put(tmp_path / "a.txt", "path C:\\temp\n")

    async def scenario():
        result = await Worker().invoke(
            "files_patch",
            {
                "path": "a.txt",
                "old_string": "path C:\\\\temp",
                "new_string": "path D:\\tmp",
                "expected_sha256": digest("path C:\\temp\n"),
            },
            {"workspace": str(tmp_path)},
        )
        assert result.status == Outcome.ERROR and result.effect == Effect.NONE
        assert result.error_code == "patch_escape_drift"
        assert (tmp_path / "a.txt").read_bytes() == b"path C:\\temp\n"
        assert not list(tmp_path.glob(".omni-write-*"))

    asyncio.run(scenario())


def test_worker_patch_similarity_note_surfaces(tmp_path):
    put(tmp_path / "a.txt", "head\nmiddle one\nfoot\n")

    async def scenario():
        result = await Worker().invoke(
            "files_patch",
            {
                "path": "a.txt",
                "old_string": "head\nmiddle two\nfoot\n",
                "new_string": "head\nREPLACED\nfoot\n",
                "expected_sha256": digest("head\nmiddle one\nfoot\n"),
            },
            {"workspace": str(tmp_path)},
        )
        assert result.status == Outcome.OK and result.data["strategy"] == "block_anchor"
        assert isinstance(result.data["note"], str) and "Non-exact match" in result.data["note"]
        assert result.data["note"] in result.text

    asyncio.run(scenario())


def test_worker_patch_exact_note_none(tmp_path):
    put(tmp_path / "a.txt", "hello world\n")

    async def scenario():
        result = await Worker().invoke(
            "files_patch",
            {"path": "a.txt", "old_string": "world", "new_string": "there", "expected_sha256": digest("hello world\n")},
            {"workspace": str(tmp_path)},
        )
        assert result.status == Outcome.OK
        assert result.data["note"] is None and result.text == "File patched"

    asyncio.run(scenario())


def test_post_match_newline_literal_drift_rejected():
    with pytest.raises(PatchError) as caught:
        patch_replace("a\nb\n", "a\\nb", "c\\nd")
    assert caught.value.code == "patch_escape_drift"
    assert "literal '\\n'" in str(caught.value)


def test_post_match_equal_literal_newline_counts_preserved():
    out = patch_replace('sep = "\\n"  \n', 'sep = "\\n"\n', 'sep = "\\t"\n')
    assert out == ('sep = "\\t"\n', 1, "line_trimmed", None)


def test_post_match_quote_suspect_absent_from_region_rejected():
    with pytest.raises(PatchError) as caught:
        patch_replace("\tsay 'hi'\n", "    say \\'hi\\'\n", "    say \\'yo\\'\n")
    assert caught.value.code == "patch_escape_drift"


def test_post_match_backslash_doubling_rejected():
    with pytest.raises(PatchError) as caught:
        patch_replace("\tx\\y\\z\n", "    x\\\\y\\\\z\n", "    w\\\\q\\\\r\n")
    assert caught.value.code == "patch_escape_drift"
    assert "twice as long" in str(caught.value)


def test_post_match_trivial_single_run_doubling_not_rejected():
    out = patch_replace("\tx\\y\n", "    x\\\\y\n", "    z\\\\w\n")
    # Trivial single-run doubling is not drift; reindent swaps the 4-space base for the file's tab.
    assert out == ("\tz\\\\w\n", 1, "context_aware", boundary_note("context_aware"))


def test_new_string_literal_tab_unescaped_when_region_has_real_tab():
    assert patch_replace("a\tb\n", "a\\tb", "c\\td") == ("c\td\n", 1, "escape_normalized", None)


def test_new_string_literal_cr_unescaped_when_region_has_real_cr():
    assert patch_replace("a\rb\n", "a\\rb", "c\\rd") == ("c\rd\n", 1, "escape_normalized", None)


def test_new_string_literals_preserved_when_region_lacks_control_char():
    out = patch_replace('sep = "\\t"  \n', 'sep = "\\t"\n', 'sep = "\\r"\n')
    assert out == ('sep = "\\r"\n', 1, "line_trimmed", None)


def test_new_string_literal_newline_never_unescaped():
    assert patch_replace("a\tb\n", "a\\tb", "c\\td\\ne") == ("c\td\\ne\n", 1, "escape_normalized", None)


def test_exact_strategy_keeps_replacement_literals_verbatim():
    assert patch_replace("a\\nb\n", "a\\nb", "c\\nd") == ("c\\nd\n", 1, "exact", None)


def test_worker_patch_post_match_drift_rejected_before_write(tmp_path):
    put(tmp_path / "a.txt", "a\nb\n")

    async def scenario():
        result = await Worker().invoke(
            "files_patch",
            {"path": "a.txt", "old_string": "a\\nb", "new_string": "c\\nd", "expected_sha256": digest("a\nb\n")},
            {"workspace": str(tmp_path)},
        )
        assert result.status == Outcome.ERROR and result.effect == Effect.NONE
        assert result.error_code == "patch_escape_drift"
        assert (tmp_path / "a.txt").read_bytes() == b"a\nb\n"
        assert not list(tmp_path.glob(".omni-write-*"))

    asyncio.run(scenario())


def test_worker_patch_unescapes_new_string_with_region_evidence(tmp_path):
    put(tmp_path / "a.txt", "a\tb\n")

    async def scenario():
        result = await Worker().invoke(
            "files_patch",
            {"path": "a.txt", "old_string": "a\\tb", "new_string": "c\\td", "expected_sha256": digest("a\tb\n")},
            {"workspace": str(tmp_path)},
        )
        assert result.status == Outcome.OK and result.effect == Effect.APPLIED
        assert (tmp_path / "a.txt").read_bytes() == b"c\td\n"
        assert result.verification == digest("c\td\n")

    asyncio.run(scenario())


def test_leading_whitespace_and_first_meaningful_line():
    assert _leading_whitespace("    foo") == "    "
    assert _leading_whitespace("\tfoo") == "\t"
    assert _leading_whitespace("foo") == ""
    assert _leading_whitespace("  \t x") == "  \t "
    assert _leading_whitespace("") == ""
    assert _first_meaningful_line("\n\n    foo\nbar") == "    foo"
    assert _first_meaningful_line("foo\nbar") == "foo"
    assert _first_meaningful_line("\n  \n") is None
    assert _first_meaningful_line("") is None


def test_match_starts_mid_line_whole_line_vs_partial():
    assert _match_starts_mid_line("def f():\n\treturn 1\n", 9, 19) is False
    assert _match_starts_mid_line("\n\n    foo\n", 0, 10) is False
    assert _match_starts_mid_line("hello world", 6, 11) is True
    assert _match_starts_mid_line("hello\nworld", 6, 11) is False


def test_reindent_replacement_swaps_base_preserves_nesting():
    region = "\tfoo\n\t    bar\n"
    old = "    foo\n        bar\n"
    new = "    baz\n        qux\n"
    assert _reindent_replacement(region, old, new, match_starts_mid_line=False) == "\tbaz\n\t    qux\n"


def test_reindent_replacement_shallow_line_anchors_to_file_base():
    region = "        foo\n"
    old = "    foo\n"
    new = "    bar\n  baz\n"
    assert _reindent_replacement(region, old, new, match_starts_mid_line=False) == "        bar\n        baz\n"


def test_reindent_replacement_noop_guards():
    assert _reindent_replacement("\tfoo\n", "    foo\n", "", match_starts_mid_line=False) == ""
    assert _reindent_replacement("\tfoo\n", "    foo\n", "bar\n", match_starts_mid_line=True) == "bar\n"
    assert _reindent_replacement("    foo\n", "    foo\n", "bar\n", match_starts_mid_line=False) == "bar\n"
    assert _reindent_replacement("\n\n", "\n\n", "bar\n", match_starts_mid_line=False) == "bar\n"
    assert (
        _reindent_replacement("\tfoo\n", "    foo\n", "bar\n\nbaz\n", match_starts_mid_line=False) == "\tbar\n\n\tbaz\n"
    )


def test_patch_reindent_swaps_base_keeps_relative_nesting():
    content = "\tdef m(self):\n\t\treturn 1\n"
    old = "    def m(self):\n        return 1\n"
    new = "    def m(self):\n        return 2\n"
    # Base 4-space -> file tab; the +4-space relative nesting from old_string is preserved verbatim.
    assert patch_replace(content, old, new) == ("\tdef m(self):\n\t    return 2\n", 1, "line_trimmed", None)


def test_patch_reindent_replace_all_per_region():
    content = "\tfoo\n\t\tfoo\n"
    out = patch_replace(content, "  foo\n", "  bar\n", replace_all=True)
    # Each region re-anchors onto its own file base (tab vs double-tab); old base is 2 spaces.
    assert out == ("\tbar\n\t\tbar\n", 2, "line_trimmed", None)


def test_patch_reindent_exact_stays_verbatim():
    assert patch_replace("\tfoo\n", "\tfoo\n", "    bar\n") == ("    bar\n", 1, "exact", None)


def test_patch_reindent_mid_line_escape_keeps_supplied_indent():
    out = patch_replace("\tx = a\tb\n", "a\\tb", "    c\\td")
    assert out == ("\tx = " + "    c\td\n", 1, "escape_normalized", None)


def test_patch_reindent_top_level_noop_zero_regression():
    assert patch_replace("foo bar\n", "foo  bar\n", "baz\n") == ("baz\n", 1, "line_whitespace", None)


def test_worker_patch_reindent_preserves_file_indent(tmp_path):
    put(tmp_path / "a.py", "\tdef m():\n\t\treturn 1\n")

    async def scenario():
        result = await Worker().invoke(
            "files_patch",
            {
                "path": "a.py",
                "old_string": "    def m():\n        return 1\n",
                "new_string": "    def m():\n        return 2\n",
                "expected_sha256": digest("\tdef m():\n\t\treturn 1\n"),
            },
            {"workspace": str(tmp_path)},
        )
        assert result.status == Outcome.OK and result.effect == Effect.APPLIED
        assert result.data["strategy"] == "line_trimmed"
        assert (tmp_path / "a.py").read_bytes() == b"\tdef m():\n\t    return 2\n"
        assert result.verification == digest("\tdef m():\n\t    return 2\n")

    asyncio.run(scenario())


def test_unicode_edit_plan_and_preserve_fallback():
    norm_old, opcodes = _unicode_edit_plan("a--b", "a--c")
    assert norm_old == "a--b"
    assert opcodes == [("equal", 0, 3, 0, 3), ("replace", 3, 4, 3, 4)]
    # A region whose normalization differs from norm_old falls back to verbatim new_string.
    assert _preserve_unicode_in_replacement("a--b  ", "a--c", (norm_old, opcodes)) == "a--c"


def test_preserve_unicode_keeps_region_typography():
    plan = _unicode_edit_plan("a--b", "a--c")
    assert _preserve_unicode_in_replacement("a\u2014b", "a--c", plan) == "a\u2014c"


def test_patch_unicode_preserve_em_dash_shared_prefix():
    assert patch_replace("a\u2014b\n", "a--b", "a--c") == ("a\u2014c\n", 1, "unicode_normalized", None)


def test_patch_unicode_preserve_smart_quotes_shared_affixes():
    assert patch_replace("\u201cfoo\u201d\n", '"foo"', '"bar"') == (
        "\u201cbar\u201d\n",
        1,
        "unicode_normalized",
        None,
    )


def test_patch_unicode_preserve_ellipsis():
    assert patch_replace("a\u2026b\n", "a...b", "a...c") == ("a\u2026c\n", 1, "unicode_normalized", None)


def test_patch_unicode_preserve_delete_opcode():
    assert patch_replace("\u2014foo\n", "--foo", "--") == ("\u2014\n", 1, "unicode_normalized", None)


def test_patch_unicode_preserve_fallback_on_region_norm_mismatch():
    # Trailing-space region normalizes unequal to norm_old -> verbatim new_string (typography flattened).
    assert patch_replace("a\u2014b  \n", "a--b\n", "a--c\n") == ("a--c\n", 1, "unicode_normalized", None)


def test_patch_unicode_preserve_replace_all_per_region_typography():
    out = patch_replace("\u201cx\u201d\n\u201dx\u201c\n", '"x"', '"z"', replace_all=True)
    assert out == ("\u201cz\u201d\n\u201dz\u201c\n", 2, "unicode_normalized", None)


def test_patch_unicode_preserve_with_matching_indent():
    assert patch_replace("\ta\u2014b\n", "\ta--b\n", "\ta--c\n") == (
        "\ta\u2014c\n",
        1,
        "unicode_normalized",
        None,
    )


def test_worker_patch_unicode_preserve_typography(tmp_path):
    put(tmp_path / "a.txt", "a\u2014b\n")

    async def scenario():
        result = await Worker().invoke(
            "files_patch",
            {
                "path": "a.txt",
                "old_string": "a--b",
                "new_string": "a--c",
                "expected_sha256": digest("a\u2014b\n"),
            },
            {"workspace": str(tmp_path)},
        )
        assert result.status == Outcome.OK and result.effect == Effect.APPLIED
        assert result.data["strategy"] == "unicode_normalized"
        assert (tmp_path / "a.txt").read_bytes() == "a\u2014c\n".encode("utf-8")
        assert result.verification == digest("a\u2014c\n")

    asyncio.run(scenario())
