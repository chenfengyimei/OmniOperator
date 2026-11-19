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
        assert (tmp_path / "a.py").read_bytes() == b"    return 2\n"

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
    assert patch_replace("\tfoo\n", "    foo\n", "bar\n") == ("bar\n", 1, "line_trimmed", None)


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
    assert out == ("    z\\\\w\n", 1, "context_aware", boundary_note("context_aware"))


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
