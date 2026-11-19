"""Bounded workspace text search: pure rules, real-filesystem safety reuse, and tier/registration wiring."""

import asyncio
import os
from pathlib import Path

import pytest

from omni.adapters.workers.main import Worker
from omni.domain.contracts import Effect, Outcome
from omni.domain.files_search import (
    compile_search_regex,
    count_matches,
    match_lines,
    match_name,
    match_path_glob,
    normalize_context,
    normalize_file_glob,
    normalize_offset,
    normalize_order,
    normalize_output_mode,
    normalize_query,
    normalize_use_regex,
    regex_count_matches,
    regex_match_lines,
)
from omni.domain.routing import CHAT_TOOLS, RETRIEVAL_TOOLS, select_tools


def test_normalize_query_strips_and_rejects_empty_oversize_control_and_non_string():
    assert normalize_query("  needle  ") == "needle"
    assert normalize_query("x" * 200) == "x" * 200
    for bad in ("", "   ", "x" * 201, "a\x00b", "a\nb", "a\tb", 5, None, ["needle"]):
        with pytest.raises(ValueError):
            normalize_query(bad)


def test_match_lines_are_case_insensitive_bounded_and_capped_per_file():
    text = "Alpha\nbeta ALPHA\ngamma\n" + "alpha\n" * 10
    hits = match_lines(text, "ALPHA")
    assert [h["line"] for h in hits] == [1, 2, 4, 5, 6]
    assert all("alpha" in str(h["text"]).casefold() for h in hits)
    assert match_lines("abc", "zzz") == []
    long_line = "x" * 500 + "needle"
    snippet = match_lines(long_line, "needle")[0]["text"]
    assert isinstance(snippet, str) and len(snippet) <= 200


def test_worker_search_returns_relative_paths_and_line_numbers(tmp_path):
    (tmp_path / "a.txt").write_text("first\nNeedle here\nlast", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("needle again", encoding="utf-8")

    async def scenario():
        result = await Worker().invoke("files_search", {"query": "needle"}, {"workspace": str(tmp_path)})
        assert result.status == Outcome.OK and result.effect == Effect.NONE
        matches = result.data["matches"]
        by_path = {Path(m["path"]).as_posix(): m for m in matches}
        assert set(by_path) == {"a.txt", "sub/b.txt"}
        assert by_path["a.txt"]["line"] == 2 and by_path["a.txt"]["text"] == "Needle here"
        assert by_path["sub/b.txt"]["line"] == 1
        assert result.data["truncated"] is False

    asyncio.run(scenario())


def test_worker_search_skips_private_and_hardlinked_files(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "good.txt").write_text("needle good", encoding="utf-8")
    (workspace / ".env").write_text("needle secret", encoding="utf-8")
    (workspace / ".git").mkdir()
    (workspace / ".git" / "config").write_text("needle secret", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("needle outside", encoding="utf-8")
    os.link(outside, workspace / "alias.txt")

    async def scenario():
        result = await Worker().invoke("files_search", {"query": "needle"}, {"workspace": str(workspace)})
        assert result.status == Outcome.OK and result.effect == Effect.NONE
        paths = {Path(m["path"]).as_posix() for m in result.data["matches"]}
        assert paths == {"good.txt"}
        assert "needle secret" not in result.text and "needle outside" not in result.text
        assert outside.read_text(encoding="utf-8") == "needle outside"

    asyncio.run(scenario())


def test_worker_search_skips_binary_and_oversize_files(tmp_path):
    (tmp_path / "ok.txt").write_text("needle", encoding="utf-8")
    (tmp_path / "bin.dat").write_bytes(b"\xff\xfe needle \x00")
    (tmp_path / "big.txt").write_text("needle " + "x" * 300000, encoding="utf-8")

    async def scenario():
        result = await Worker().invoke("files_search", {"query": "needle"}, {"workspace": str(tmp_path)})
        assert result.status == Outcome.OK
        paths = {Path(m["path"]).as_posix() for m in result.data["matches"]}
        assert paths == {"ok.txt"}

    asyncio.run(scenario())


@pytest.mark.parametrize("query", ["", "   ", "x" * 201, "a\x00b", 5])
def test_worker_search_rejects_invalid_query_before_dispatch(tmp_path, query):
    (tmp_path / "a.txt").write_text("needle", encoding="utf-8")

    async def scenario():
        result = await Worker().invoke("files_search", {"query": query}, {"workspace": str(tmp_path)})
        assert result.status == Outcome.ERROR and result.effect == Effect.NONE
        assert result.error_code == "invalid_capability_request"
        assert "needle" not in result.text

    asyncio.run(scenario())


def test_worker_search_subdir_root_keeps_workspace_relative_paths(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "x.txt").write_text("needle", encoding="utf-8")
    (tmp_path / "top.txt").write_text("needle", encoding="utf-8")

    async def scenario():
        result = await Worker().invoke("files_search", {"query": "needle", "path": "sub"}, {"workspace": str(tmp_path)})
        assert result.status == Outcome.OK
        paths = {Path(m["path"]).as_posix() for m in result.data["matches"]}
        assert paths == {"sub/x.txt"}

    asyncio.run(scenario())


def test_worker_search_caps_total_results_and_reports_truncation(tmp_path):
    for index in range(60):
        (tmp_path / f"f{index:03d}.txt").write_text("needle", encoding="utf-8")

    async def scenario():
        result = await Worker().invoke("files_search", {"query": "needle"}, {"workspace": str(tmp_path)})
        assert result.status == Outcome.OK
        assert len(result.data["matches"]) == 50
        assert result.data["truncated"] is True and result.data["scanned"] <= 2000

    asyncio.run(scenario())


def test_worker_search_caps_matches_per_file(tmp_path):
    (tmp_path / "many.txt").write_text("needle\n" * 10, encoding="utf-8")

    async def scenario():
        result = await Worker().invoke("files_search", {"query": "needle"}, {"workspace": str(tmp_path)})
        assert result.status == Outcome.OK
        assert len(result.data["matches"]) == 5
        assert result.data["truncated"] is False

    asyncio.run(scenario())


def test_worker_search_no_match_is_empty_and_untruncated(tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")

    async def scenario():
        result = await Worker().invoke("files_search", {"query": "zzz"}, {"workspace": str(tmp_path)})
        assert result.status == Outcome.OK and result.effect == Effect.NONE
        assert result.data["matches"] == [] and result.data["truncated"] is False

    asyncio.run(scenario())


def test_files_search_is_registered_readonly_and_tier_filtered(tmp_path):
    from omni_test_support.v2.documents import Clock, DocumentService, opened

    from omni.composition.capabilities import builtin_capabilities

    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, objects, _agent):
            specs = tuple(c.spec for c in builtin_capabilities(db, DocumentService(db, objects)))
            spec = next(s for s in specs if s.name == "files_search")
            assert spec.writes is False and spec.resource == "workspace"
            assert spec.parameters["properties"]["target"]["enum"] == ["content", "files"]
            assert "files_search" in RETRIEVAL_TOOLS and "files_search" not in CHAT_TOOLS
            retrieval = [t.name for t in select_tools("retrieval", specs, escalate=False)]
            chat = [t.name for t in select_tools("chat", specs, escalate=False)]
            assert "files_search" in retrieval and "files_search" not in chat

    asyncio.run(scenario())


def test_match_name_glob_is_basename_case_sensitive_and_strips_dir_prefix():
    assert match_name("a.py", "*.py") is True
    assert match_name("a.txt", "*.py") is False
    assert match_name("a.py", "src/*.py") is True
    assert match_name("a.py", "**/*.py") is True
    assert match_name("a.py", "*.PY") is False
    assert match_name("myconfig.json", "*config*") is True


def test_worker_files_mode_finds_by_glob_with_relative_paths(tmp_path):
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    (tmp_path / "b.txt").write_text("x", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.py").write_text("x", encoding="utf-8")

    async def scenario():
        result = await Worker().invoke(
            "files_search", {"query": "*.py", "target": "files"}, {"workspace": str(tmp_path)}
        )
        assert result.status == Outcome.OK and result.effect == Effect.NONE
        assert result.data["target"] == "files"
        paths = {Path(m["path"]).as_posix() for m in result.data["matches"]}
        assert paths == {"a.py", "sub/c.py"}
        assert all("line" not in m and "text" not in m for m in result.data["matches"])

    asyncio.run(scenario())


def test_worker_files_mode_skips_private_and_hardlinked(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "good.py").write_text("x", encoding="utf-8")
    (workspace / ".env").write_text("secret", encoding="utf-8")
    (workspace / ".git").mkdir()
    (workspace / ".git" / "config.py").write_text("x", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("x", encoding="utf-8")
    os.link(outside, workspace / "alias.py")

    async def scenario():
        result = await Worker().invoke(
            "files_search", {"query": "*.py", "target": "files"}, {"workspace": str(workspace)}
        )
        assert result.status == Outcome.OK
        paths = {Path(m["path"]).as_posix() for m in result.data["matches"]}
        assert paths == {"good.py"}

    asyncio.run(scenario())


def test_worker_files_mode_includes_oversize_while_content_skips(tmp_path):
    (tmp_path / "big.py").write_text("needle " + "x" * 300000, encoding="utf-8")

    async def scenario():
        files = await Worker().invoke(
            "files_search", {"query": "*.py", "target": "files"}, {"workspace": str(tmp_path)}
        )
        assert {Path(m["path"]).as_posix() for m in files.data["matches"]} == {"big.py"}
        content = await Worker().invoke("files_search", {"query": "needle"}, {"workspace": str(tmp_path)})
        assert content.data["matches"] == []

    asyncio.run(scenario())


def test_worker_files_mode_caps_results_and_truncates(tmp_path):
    for index in range(60):
        (tmp_path / f"f{index:03d}.py").write_text("x", encoding="utf-8")

    async def scenario():
        result = await Worker().invoke(
            "files_search", {"query": "*.py", "target": "files"}, {"workspace": str(tmp_path)}
        )
        assert result.status == Outcome.OK
        assert len(result.data["matches"]) == 50 and result.data["truncated"] is True

    asyncio.run(scenario())


def test_worker_search_rejects_invalid_target(tmp_path):
    (tmp_path / "a.py").write_text("needle", encoding="utf-8")

    async def scenario():
        result = await Worker().invoke(
            "files_search", {"query": "needle", "target": "bogus"}, {"workspace": str(tmp_path)}
        )
        assert result.status == Outcome.ERROR and result.effect == Effect.NONE
        assert result.error_code == "invalid_capability_request"

    asyncio.run(scenario())


def test_normalize_output_mode_defaults_and_rejects_unknown():
    assert normalize_output_mode(None) == "content"
    for mode in ("content", "files_with_matches", "count"):
        assert normalize_output_mode(mode) == mode
    for bad in ("bogus", "", "CONTENT", 5, True, ["content"]):
        with pytest.raises(ValueError):
            normalize_output_mode(bad)


def test_count_matches_counts_all_lines_beyond_per_file_hit_cap():
    text = "alpha\n" * 12 + "beta\n"
    assert count_matches(text, "ALPHA") == 12
    assert count_matches(text, "zzz") == 0
    assert count_matches("one alpha two\nalpha again", "alpha") == 2


def test_worker_output_mode_files_with_matches_returns_distinct_paths(tmp_path):
    (tmp_path / "a.txt").write_text("needle\nneedle again\nneedle third", encoding="utf-8")
    (tmp_path / "b.txt").write_text("nothing here", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.txt").write_text("a needle", encoding="utf-8")

    async def scenario():
        result = await Worker().invoke(
            "files_search", {"query": "needle", "output_mode": "files_with_matches"}, {"workspace": str(tmp_path)}
        )
        assert result.status == Outcome.OK and result.effect == Effect.NONE
        assert result.data["output_mode"] == "files_with_matches"
        matches = result.data["matches"]
        assert {Path(m["path"]).as_posix() for m in matches} == {"a.txt", "sub/c.txt"}
        assert all(set(m) == {"path"} for m in matches)

    asyncio.run(scenario())


def test_worker_output_mode_count_returns_full_per_file_totals(tmp_path):
    (tmp_path / "a.txt").write_text("alpha\n" * 12 + "beta\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("no hit", encoding="utf-8")

    async def scenario():
        result = await Worker().invoke(
            "files_search", {"query": "alpha", "output_mode": "count"}, {"workspace": str(tmp_path)}
        )
        assert result.status == Outcome.OK
        assert result.data["output_mode"] == "count"
        matches = result.data["matches"]
        assert matches == [{"path": "a.txt", "count": 12}]

    asyncio.run(scenario())


def test_worker_output_mode_content_default_shape_unchanged(tmp_path):
    (tmp_path / "a.txt").write_text("first\nNeedle here\nlast", encoding="utf-8")

    async def scenario():
        result = await Worker().invoke("files_search", {"query": "needle"}, {"workspace": str(tmp_path)})
        assert result.data["output_mode"] == "content"
        assert result.data["matches"] == [{"path": "a.txt", "line": 2, "text": "Needle here"}]

    asyncio.run(scenario())


def test_worker_output_mode_count_caps_files_and_truncates(tmp_path):
    for index in range(60):
        (tmp_path / f"f{index:03d}.txt").write_text("needle", encoding="utf-8")

    async def scenario():
        result = await Worker().invoke(
            "files_search", {"query": "needle", "output_mode": "count"}, {"workspace": str(tmp_path)}
        )
        assert result.status == Outcome.OK
        assert len(result.data["matches"]) == 50 and result.data["truncated"] is True
        assert all(m["count"] == 1 for m in result.data["matches"])

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["bogus", "", 5, True])
def test_worker_search_rejects_invalid_output_mode_before_dispatch(tmp_path, mode):
    (tmp_path / "a.py").write_text("needle", encoding="utf-8")

    async def scenario():
        result = await Worker().invoke(
            "files_search", {"query": "needle", "output_mode": mode}, {"workspace": str(tmp_path)}
        )
        assert result.status == Outcome.ERROR and result.effect == Effect.NONE
        assert result.error_code == "invalid_capability_request"

    asyncio.run(scenario())


def test_files_search_spec_exposes_output_mode_enum(tmp_path):
    from omni_test_support.v2.documents import Clock, DocumentService, opened

    from omni.composition.capabilities import builtin_capabilities

    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, objects, _agent):
            specs = tuple(c.spec for c in builtin_capabilities(db, DocumentService(db, objects)))
            parameters = next(s for s in specs if s.name == "files_search").parameters
            assert parameters["properties"]["output_mode"]["enum"] == ["content", "files_with_matches", "count"]
            assert parameters["properties"]["context"] == {"type": "integer", "minimum": 0, "maximum": 3}
            assert parameters["properties"]["offset"] == {"type": "integer", "minimum": 0, "maximum": 1000}
            assert parameters["required"] == ["query"]

    asyncio.run(scenario())


def test_normalize_context_defaults_and_rejects_out_of_range_or_non_int():
    assert normalize_context(None) == 0
    for value in (0, 1, 2, 3):
        assert normalize_context(value) == value
    for bad in (-1, 4, True, False, 1.5, "2", [1]):
        with pytest.raises(ValueError):
            normalize_context(bad)


def test_match_lines_context_attaches_bounded_neighbors_and_clamps_edges():
    text = "l1\nl2\nneedle\nl4\nl5\nl6"
    hits = match_lines(text, "needle", 2)
    assert hits[0]["line"] == 3 and hits[0]["text"] == "needle"
    assert hits[0]["before"] == [{"line": 1, "text": "l1"}, {"line": 2, "text": "l2"}]
    assert hits[0]["after"] == [{"line": 4, "text": "l4"}, {"line": 5, "text": "l5"}]
    first = match_lines("needle\nb\nc", "needle", 3)[0]
    assert first["before"] == [] and first["after"] == [{"line": 2, "text": "b"}, {"line": 3, "text": "c"}]
    assert match_lines("a\nneedle", "needle", 0) == [{"line": 2, "text": "needle"}]


def test_worker_content_mode_context_returns_neighbors(tmp_path):
    (tmp_path / "a.txt").write_text("alpha\nbeta\nNeedle here\ngamma\ndelta", encoding="utf-8")

    async def scenario():
        result = await Worker().invoke("files_search", {"query": "needle", "context": 1}, {"workspace": str(tmp_path)})
        assert result.status == Outcome.OK and result.effect == Effect.NONE
        assert result.data["context"] == 1
        hit = result.data["matches"][0]
        assert hit["line"] == 3 and hit["text"] == "Needle here"
        assert hit["before"] == [{"line": 2, "text": "beta"}]
        assert hit["after"] == [{"line": 4, "text": "gamma"}]

    asyncio.run(scenario())


def test_worker_context_default_zero_shape_unchanged(tmp_path):
    (tmp_path / "a.txt").write_text("alpha\nNeedle here\ngamma", encoding="utf-8")

    async def scenario():
        result = await Worker().invoke("files_search", {"query": "needle"}, {"workspace": str(tmp_path)})
        assert result.data["context"] == 0
        assert result.data["matches"] == [{"path": "a.txt", "line": 2, "text": "Needle here"}]

    asyncio.run(scenario())


def test_worker_context_ignored_for_count_mode(tmp_path):
    (tmp_path / "a.txt").write_text("alpha\nneedle\nneedle", encoding="utf-8")

    async def scenario():
        result = await Worker().invoke(
            "files_search", {"query": "needle", "output_mode": "count", "context": 3}, {"workspace": str(tmp_path)}
        )
        assert result.status == Outcome.OK
        assert result.data["matches"] == [{"path": "a.txt", "count": 2}]

    asyncio.run(scenario())


@pytest.mark.parametrize("value", [-1, 4, True, "2", 1.5])
def test_worker_search_rejects_invalid_context_before_dispatch(tmp_path, value):
    (tmp_path / "a.py").write_text("needle", encoding="utf-8")

    async def scenario():
        result = await Worker().invoke(
            "files_search", {"query": "needle", "context": value}, {"workspace": str(tmp_path)}
        )
        assert result.status == Outcome.ERROR and result.effect == Effect.NONE
        assert result.error_code == "invalid_capability_request"

    asyncio.run(scenario())


def test_normalize_offset_defaults_and_rejects_out_of_range_or_non_int():
    assert normalize_offset(None) == 0
    for value in (0, 1, 1000):
        assert normalize_offset(value) == value
    for bad in (-1, 1001, True, False, 1.5, "2", [1]):
        with pytest.raises(ValueError):
            normalize_offset(bad)


def test_worker_offset_skips_first_matches(tmp_path):
    (tmp_path / "a.txt").write_text("needle\nneedle\nneedle\nneedle", encoding="utf-8")

    async def scenario():
        result = await Worker().invoke("files_search", {"query": "needle", "offset": 2}, {"workspace": str(tmp_path)})
        assert result.status == Outcome.OK and result.effect == Effect.NONE
        assert result.data["offset"] == 2 and result.data["truncated"] is False
        assert [m["line"] for m in result.data["matches"]] == [3, 4]

    asyncio.run(scenario())


def test_worker_offset_beyond_total_is_empty_untruncated(tmp_path):
    for index in range(3):
        (tmp_path / f"f{index}.txt").write_text("needle", encoding="utf-8")

    async def scenario():
        result = await Worker().invoke("files_search", {"query": "needle", "offset": 10}, {"workspace": str(tmp_path)})
        assert result.status == Outcome.OK
        assert result.data["matches"] == [] and result.data["truncated"] is False

    asyncio.run(scenario())


def test_worker_offset_pages_with_truncation(tmp_path):
    for index in range(60):
        (tmp_path / f"f{index:03d}.txt").write_text("needle", encoding="utf-8")

    async def scenario():
        result = await Worker().invoke("files_search", {"query": "needle", "offset": 5}, {"workspace": str(tmp_path)})
        assert result.status == Outcome.OK
        matches = result.data["matches"]
        assert len(matches) == 50 and result.data["truncated"] is True
        assert matches[0]["path"] == "f005.txt" and matches[-1]["path"] == "f054.txt"

    asyncio.run(scenario())


def test_worker_offset_default_zero_shape_unchanged(tmp_path):
    (tmp_path / "a.txt").write_text("alpha\nNeedle here\ngamma", encoding="utf-8")

    async def scenario():
        result = await Worker().invoke("files_search", {"query": "needle"}, {"workspace": str(tmp_path)})
        assert result.data["offset"] == 0
        assert result.data["matches"] == [{"path": "a.txt", "line": 2, "text": "Needle here"}]

    asyncio.run(scenario())


@pytest.mark.parametrize("value", [-1, 1001, True, "2", 1.5])
def test_worker_search_rejects_invalid_offset_before_dispatch(tmp_path, value):
    (tmp_path / "a.py").write_text("needle", encoding="utf-8")

    async def scenario():
        result = await Worker().invoke(
            "files_search", {"query": "needle", "offset": value}, {"workspace": str(tmp_path)}
        )
        assert result.status == Outcome.ERROR and result.effect == Effect.NONE
        assert result.error_code == "invalid_capability_request"

    asyncio.run(scenario())


def test_normalize_order_defaults_and_rejects_unknown():
    assert normalize_order(None) == "discovery"
    for value in ("discovery", "modified"):
        assert normalize_order(value) == value
    for bad in ("bogus", "", "MODIFIED", 5, True, ["discovery"]):
        with pytest.raises(ValueError):
            normalize_order(bad)


def test_worker_order_modified_sorts_newest_first(tmp_path):
    for name, mtime in (("old.txt", 1000), ("mid.txt", 2000), ("new.txt", 3000)):
        path = tmp_path / name
        path.write_text("needle", encoding="utf-8")
        os.utime(path, (mtime, mtime))

    async def scenario():
        result = await Worker().invoke(
            "files_search", {"query": "needle", "order": "modified"}, {"workspace": str(tmp_path)}
        )
        assert result.status == Outcome.OK and result.effect == Effect.NONE
        assert result.data["order"] == "modified"
        assert [Path(m["path"]).as_posix() for m in result.data["matches"]] == ["new.txt", "mid.txt", "old.txt"]

    asyncio.run(scenario())


def test_worker_order_discovery_default_keeps_traversal_order(tmp_path):
    older = tmp_path / "a.txt"
    older.write_text("needle", encoding="utf-8")
    os.utime(older, (1000, 1000))
    newer = tmp_path / "b.txt"
    newer.write_text("needle", encoding="utf-8")
    os.utime(newer, (9000, 9000))

    async def scenario():
        result = await Worker().invoke("files_search", {"query": "needle"}, {"workspace": str(tmp_path)})
        assert result.data["order"] == "discovery"
        assert [Path(m["path"]).as_posix() for m in result.data["matches"]] == ["a.txt", "b.txt"]

    asyncio.run(scenario())


def test_worker_order_modified_stable_ties_keep_discovery_order(tmp_path):
    for name in ("b.txt", "a.txt", "c.txt"):
        path = tmp_path / name
        path.write_text("needle", encoding="utf-8")
        os.utime(path, (5000, 5000))

    async def scenario():
        result = await Worker().invoke(
            "files_search", {"query": "needle", "order": "modified"}, {"workspace": str(tmp_path)}
        )
        assert [Path(m["path"]).as_posix() for m in result.data["matches"]] == ["a.txt", "b.txt", "c.txt"]

    asyncio.run(scenario())


def test_worker_order_modified_global_sort_across_scan_and_pages(tmp_path):
    for index in range(60):
        path = tmp_path / f"f{index:03d}.txt"
        path.write_text("needle", encoding="utf-8")
        os.utime(path, (1000 + index, 1000 + index))

    async def scenario():
        result = await Worker().invoke(
            "files_search", {"query": "needle", "order": "modified"}, {"workspace": str(tmp_path)}
        )
        assert result.status == Outcome.OK
        matches = result.data["matches"]
        assert len(matches) == 50 and result.data["truncated"] is True
        assert matches[0]["path"] == "f059.txt" and matches[-1]["path"] == "f010.txt"
        page2 = await Worker().invoke(
            "files_search", {"query": "needle", "order": "modified", "offset": 50}, {"workspace": str(tmp_path)}
        )
        assert [Path(m["path"]).as_posix() for m in page2.data["matches"]] == [
            f"f{i:03d}.txt" for i in range(9, -1, -1)
        ]
        assert page2.data["truncated"] is False

    asyncio.run(scenario())


def test_worker_order_modified_files_mode(tmp_path):
    for name, mtime in (("a.py", 1000), ("b.py", 3000), ("c.py", 2000)):
        path = tmp_path / name
        path.write_text("x", encoding="utf-8")
        os.utime(path, (mtime, mtime))

    async def scenario():
        result = await Worker().invoke(
            "files_search", {"query": "*.py", "target": "files", "order": "modified"}, {"workspace": str(tmp_path)}
        )
        assert result.data["target"] == "files" and result.data["order"] == "modified"
        assert [Path(m["path"]).as_posix() for m in result.data["matches"]] == ["b.py", "c.py", "a.py"]
        assert all(set(m) == {"path"} for m in result.data["matches"])

    asyncio.run(scenario())


@pytest.mark.parametrize("value", ["bogus", "", "MODIFIED", 5, True])
def test_worker_search_rejects_invalid_order_before_dispatch(tmp_path, value):
    (tmp_path / "a.py").write_text("needle", encoding="utf-8")

    async def scenario():
        result = await Worker().invoke(
            "files_search", {"query": "needle", "order": value}, {"workspace": str(tmp_path)}
        )
        assert result.status == Outcome.ERROR and result.effect == Effect.NONE
        assert result.error_code == "invalid_capability_request"

    asyncio.run(scenario())


def test_files_search_spec_exposes_order_enum(tmp_path):
    from omni_test_support.v2.documents import Clock, DocumentService, opened

    from omni.composition.capabilities import builtin_capabilities

    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, objects, _agent):
            specs = tuple(c.spec for c in builtin_capabilities(db, DocumentService(db, objects)))
            parameters = next(s for s in specs if s.name == "files_search").parameters
            assert parameters["properties"]["order"]["enum"] == ["discovery", "modified"]
            assert parameters["required"] == ["query"]

    asyncio.run(scenario())


def test_normalize_file_glob_defaults_and_rejects_invalid():
    assert normalize_file_glob(None) is None
    assert normalize_file_glob("  *.py  ") == "*.py"
    assert normalize_file_glob("x" * 200) == "x" * 200
    for bad in ("", "   ", "x" * 201, "a\nb", "a\x00b", 5, True, ["*.py"]):
        with pytest.raises(ValueError):
            normalize_file_glob(bad)


def test_match_path_glob_is_case_sensitive_and_path_aware():
    assert match_path_glob("a.py", "*.py") is True
    assert match_path_glob("sub/a.py", "*.py") is True
    assert match_path_glob("sub/a.py", "src/*.py") is False
    assert match_path_glob("src/a.py", "src/*.py") is True
    assert match_path_glob("a.py", "*.PY") is False
    assert match_path_glob("a.py.bak", "*.py") is False


def test_worker_file_glob_filters_content_to_matching_files(tmp_path):
    (tmp_path / "a.py").write_text("needle", encoding="utf-8")
    (tmp_path / "b.txt").write_text("needle", encoding="utf-8")

    async def scenario():
        result = await Worker().invoke(
            "files_search", {"query": "needle", "file_glob": "*.py"}, {"workspace": str(tmp_path)}
        )
        assert result.status == Outcome.OK and result.effect == Effect.NONE
        assert {Path(m["path"]).as_posix() for m in result.data["matches"]} == {"a.py"}

    asyncio.run(scenario())


def test_worker_file_glob_path_anchored(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "x.py").write_text("needle", encoding="utf-8")
    (tmp_path / "top.py").write_text("needle", encoding="utf-8")

    async def scenario():
        result = await Worker().invoke(
            "files_search", {"query": "needle", "file_glob": "sub/*.py"}, {"workspace": str(tmp_path)}
        )
        assert {Path(m["path"]).as_posix() for m in result.data["matches"]} == {"sub/x.py"}

    asyncio.run(scenario())


def test_worker_file_glob_none_default_searches_all(tmp_path):
    (tmp_path / "a.py").write_text("needle", encoding="utf-8")
    (tmp_path / "b.txt").write_text("needle", encoding="utf-8")

    async def scenario():
        result = await Worker().invoke("files_search", {"query": "needle"}, {"workspace": str(tmp_path)})
        assert {Path(m["path"]).as_posix() for m in result.data["matches"]} == {"a.py", "b.txt"}

    asyncio.run(scenario())


def test_worker_file_glob_applies_to_files_with_matches_and_count(tmp_path):
    (tmp_path / "a.py").write_text("needle\nneedle", encoding="utf-8")
    (tmp_path / "b.txt").write_text("needle", encoding="utf-8")

    async def scenario():
        fwm = await Worker().invoke(
            "files_search",
            {"query": "needle", "output_mode": "files_with_matches", "file_glob": "*.py"},
            {"workspace": str(tmp_path)},
        )
        assert {Path(m["path"]).as_posix() for m in fwm.data["matches"]} == {"a.py"}
        cnt = await Worker().invoke(
            "files_search",
            {"query": "needle", "output_mode": "count", "file_glob": "*.py"},
            {"workspace": str(tmp_path)},
        )
        assert cnt.data["matches"] == [{"path": "a.py", "count": 2}]

    asyncio.run(scenario())


def test_worker_file_glob_ignored_for_files_target(tmp_path):
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    (tmp_path / "b.txt").write_text("x", encoding="utf-8")

    async def scenario():
        result = await Worker().invoke(
            "files_search", {"query": "*.py", "target": "files", "file_glob": "*.txt"}, {"workspace": str(tmp_path)}
        )
        assert {Path(m["path"]).as_posix() for m in result.data["matches"]} == {"a.py"}

    asyncio.run(scenario())


@pytest.mark.parametrize("value", ["", "   ", "x" * 201, "a\nb", 5])
def test_worker_search_rejects_invalid_file_glob_before_dispatch(tmp_path, value):
    (tmp_path / "a.py").write_text("needle", encoding="utf-8")

    async def scenario():
        result = await Worker().invoke(
            "files_search", {"query": "needle", "file_glob": value}, {"workspace": str(tmp_path)}
        )
        assert result.status == Outcome.ERROR and result.effect == Effect.NONE
        assert result.error_code == "invalid_capability_request"

    asyncio.run(scenario())


def test_files_search_spec_exposes_file_glob(tmp_path):
    from omni_test_support.v2.documents import Clock, DocumentService, opened

    from omni.composition.capabilities import builtin_capabilities

    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, objects, _agent):
            specs = tuple(c.spec for c in builtin_capabilities(db, DocumentService(db, objects)))
            parameters = next(s for s in specs if s.name == "files_search").parameters
            assert parameters["properties"]["file_glob"] == {"type": "string", "minLength": 1, "maxLength": 200}
            assert parameters["required"] == ["query"]

    asyncio.run(scenario())


def test_normalize_use_regex_defaults_and_rejects_non_bool():
    assert normalize_use_regex(None) is False
    assert normalize_use_regex(True) is True
    assert normalize_use_regex(False) is False
    for bad in (1, 0, "true", "", ["regex"], 1.0):
        with pytest.raises(ValueError):
            normalize_use_regex(bad)


def test_compile_search_regex_accepts_near_linear_patterns():
    for pattern in (
        r"needle",
        r"^def \w+\(",
        r"[a-z]+\d{2,4}",
        r"a|b",
        r"(a|b)+",
        r"(ab)c",
        r"(?=ab)cd",
        r"a+?",
        r"a++",
        r"a{1024}",
        r".*",
    ):
        assert compile_search_regex(pattern) is not None


def test_compile_search_regex_rejects_invalid_syntax():
    for pattern in ("[", "(unclosed", "a{2,1}", "*bad"):
        with pytest.raises(ValueError):
            compile_search_regex(pattern)


@pytest.mark.parametrize(
    "pattern",
    [
        r"(a+)+",
        r"(a*)*",
        r"(?:a+)+",
        r"(a|bc)+",
        r"(a|ab)*",
        r"(?=a+)b",
        r"(?!x+)y",
        r"(?<=a+)b",
        r"(a)\1",
        r"a{1025}",
        r"(a{2}){3}",
    ],
)
def test_compile_search_regex_rejects_super_linear_constructs(pattern):
    with pytest.raises(ValueError):
        compile_search_regex(pattern)


def test_regex_match_lines_are_case_sensitive_bounded_and_capped():
    text = "Alpha\nbeta ALPHA\ngamma\n" + "alpha\n" * 10
    hits = regex_match_lines(text, compile_search_regex("alpha"))
    assert [h["line"] for h in hits] == [4, 5, 6, 7, 8]
    assert regex_match_lines("abc", compile_search_regex("zzz")) == []
    long_line = "x" * 500 + "needle"
    snippet = regex_match_lines(long_line, compile_search_regex("needle"))[0]["text"]
    assert isinstance(snippet, str) and len(snippet) <= 200
    anchored = regex_match_lines("a1\nb22\nc333", compile_search_regex(r"^[a-z]\d{2}$"))
    assert [h["line"] for h in anchored] == [2]


def test_regex_match_lines_context_matches_substring_shape():
    text = "l1\nl2\nneedle\nl4\nl5\nl6"
    hits = regex_match_lines(text, compile_search_regex("needle"), 2)
    assert hits[0]["line"] == 3 and hits[0]["text"] == "needle"
    assert hits[0]["before"] == [{"line": 1, "text": "l1"}, {"line": 2, "text": "l2"}]
    assert hits[0]["after"] == [{"line": 4, "text": "l4"}, {"line": 5, "text": "l5"}]
    assert regex_match_lines("a\nneedle", compile_search_regex("needle"), 0) == [{"line": 2, "text": "needle"}]


def test_regex_count_matches_counts_all_lines_beyond_hit_cap():
    text = "alpha\n" * 12 + "beta\n"
    assert regex_count_matches(text, compile_search_regex("alpha")) == 12
    assert regex_count_matches(text, compile_search_regex("ALPHA")) == 0
    assert regex_count_matches("one alpha two\nalpha again", compile_search_regex(r"alpha")) == 2


def test_worker_regex_content_search_is_case_sensitive(tmp_path):
    (tmp_path / "a.txt").write_text("first\nNeedle here\nneedle again\nlast", encoding="utf-8")

    async def scenario():
        result = await Worker().invoke(
            "files_search", {"query": r"needle\b", "use_regex": True}, {"workspace": str(tmp_path)}
        )
        assert result.status == Outcome.OK and result.effect == Effect.NONE
        assert result.data["use_regex"] is True
        assert result.data["matches"] == [{"path": "a.txt", "line": 3, "text": "needle again"}]
        insensitive = await Worker().invoke("files_search", {"query": "needle"}, {"workspace": str(tmp_path)})
        assert [m["line"] for m in insensitive.data["matches"]] == [2, 3]

    asyncio.run(scenario())


def test_worker_regex_default_false_shape_unchanged(tmp_path):
    (tmp_path / "a.txt").write_text("alpha\nNeedle here\ngamma", encoding="utf-8")

    async def scenario():
        result = await Worker().invoke("files_search", {"query": "needle"}, {"workspace": str(tmp_path)})
        assert result.data["use_regex"] is False
        assert result.data["matches"] == [{"path": "a.txt", "line": 2, "text": "Needle here"}]

    asyncio.run(scenario())


def test_worker_regex_output_modes_and_context(tmp_path):
    (tmp_path / "a.txt").write_text("v1\nv2\nv3\nnone\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("nothing here", encoding="utf-8")

    async def scenario():
        fwm = await Worker().invoke(
            "files_search",
            {"query": r"^v\d+$", "use_regex": True, "output_mode": "files_with_matches"},
            {"workspace": str(tmp_path)},
        )
        assert {Path(m["path"]).as_posix() for m in fwm.data["matches"]} == {"a.txt"}
        cnt = await Worker().invoke(
            "files_search",
            {"query": r"^v\d+$", "use_regex": True, "output_mode": "count"},
            {"workspace": str(tmp_path)},
        )
        assert cnt.data["matches"] == [{"path": "a.txt", "count": 3}]
        ctx = await Worker().invoke(
            "files_search", {"query": r"^v2$", "use_regex": True, "context": 1}, {"workspace": str(tmp_path)}
        )
        hit = ctx.data["matches"][0]
        assert (
            hit["line"] == 2
            and hit["before"] == [{"line": 1, "text": "v1"}]
            and hit["after"] == [{"line": 3, "text": "v3"}]
        )

    asyncio.run(scenario())


def test_worker_regex_combines_with_file_glob_order_and_offset(tmp_path):
    for name, mtime in (("a.py", 1000), ("b.py", 3000), ("c.txt", 2000)):
        path = tmp_path / name
        path.write_text("item1\nitem2\n", encoding="utf-8")
        os.utime(path, (mtime, mtime))

    async def scenario():
        result = await Worker().invoke(
            "files_search",
            {"query": r"item\d", "use_regex": True, "file_glob": "*.py", "order": "modified", "offset": 1},
            {"workspace": str(tmp_path)},
        )
        assert result.status == Outcome.OK
        matches = result.data["matches"]
        assert [(Path(m["path"]).as_posix(), m["line"]) for m in matches] == [("b.py", 2), ("a.py", 1), ("a.py", 2)]
        assert result.data["truncated"] is False

    asyncio.run(scenario())


def test_worker_regex_caps_matches_per_file(tmp_path):
    (tmp_path / "many.txt").write_text("item1\n" * 10, encoding="utf-8")

    async def scenario():
        result = await Worker().invoke(
            "files_search", {"query": r"item\d", "use_regex": True}, {"workspace": str(tmp_path)}
        )
        assert result.status == Outcome.OK
        assert len(result.data["matches"]) == 5 and result.data["truncated"] is False

    asyncio.run(scenario())


@pytest.mark.parametrize("pattern", ["[", "(a+)+", r"(a)\1", "a{1025}", r"(?=a+)b"])
def test_worker_regex_rejects_invalid_or_unsafe_before_dispatch(tmp_path, pattern):
    (tmp_path / "a.txt").write_text("needle", encoding="utf-8")

    async def scenario():
        result = await Worker().invoke(
            "files_search", {"query": pattern, "use_regex": True}, {"workspace": str(tmp_path)}
        )
        assert result.status == Outcome.ERROR and result.effect == Effect.NONE
        assert result.error_code == "invalid_capability_request"
        assert "needle" not in result.text

    asyncio.run(scenario())


@pytest.mark.parametrize("value", [1, "true", "", ["regex"]])
def test_worker_regex_rejects_non_bool_use_regex_before_dispatch(tmp_path, value):
    (tmp_path / "a.txt").write_text("needle", encoding="utf-8")

    async def scenario():
        result = await Worker().invoke(
            "files_search", {"query": "needle", "use_regex": value}, {"workspace": str(tmp_path)}
        )
        assert result.status == Outcome.ERROR and result.effect == Effect.NONE
        assert result.error_code == "invalid_capability_request"

    asyncio.run(scenario())


def test_worker_regex_ignored_for_files_target(tmp_path):
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    (tmp_path / "b.txt").write_text("x", encoding="utf-8")

    async def scenario():
        result = await Worker().invoke(
            "files_search", {"query": "*.py", "target": "files", "use_regex": True}, {"workspace": str(tmp_path)}
        )
        assert result.status == Outcome.OK
        assert result.data["use_regex"] is True
        assert {Path(m["path"]).as_posix() for m in result.data["matches"]} == {"a.py"}

    asyncio.run(scenario())


def test_files_search_spec_exposes_use_regex(tmp_path):
    from omni_test_support.v2.documents import Clock, DocumentService, opened

    from omni.composition.capabilities import builtin_capabilities

    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, objects, _agent):
            specs = tuple(c.spec for c in builtin_capabilities(db, DocumentService(db, objects)))
            parameters = next(s for s in specs if s.name == "files_search").parameters
            assert parameters["properties"]["use_regex"] == {"type": "boolean"}
            assert parameters["required"] == ["query"]

    asyncio.run(scenario())
