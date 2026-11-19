"""Bounded workspace text search: pure rules, real-filesystem safety reuse, and tier/registration wiring."""

import asyncio
import os
from pathlib import Path

import pytest

from omni.adapters.workers.main import Worker
from omni.domain.contracts import Effect, Outcome
from omni.domain.files_search import count_matches, match_lines, match_name, normalize_output_mode, normalize_query
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
            assert parameters["required"] == ["query"]

    asyncio.run(scenario())
