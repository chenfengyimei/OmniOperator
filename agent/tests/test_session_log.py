"""M2 会话全量记忆单元：对话+工具过程的完整流水与排查读取。"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from omni.memory.session_log import SessionLog
from types import SimpleNamespace


def _log(tmp_path, **kw) -> SessionLog:
    return SessionLog(tmp_path / "memory.db", **kw)


def _call(name: str, arguments: str) -> SimpleNamespace:
    fn = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(function=fn)


def test_full_turn_records_all_kinds_in_order(tmp_path):
    log = _log(tmp_path)
    log.append_user(1, "run-1", "帮我查 BV1abc", channel="web")
    log.append_assistant(1, "run-1", "这就去查", tool_calls=[_call("video_info", '{"bvid": "BV1abc"}')])
    log.append_tool(1, "run-1", "video_info", '{"bvid": "BV1abc"}', "标题：示例视频", "ok")
    log.append_assistant(1, "run-1", "这个视频讲的是记忆系统")
    log.append_system(1, "run-1", "回合正常结束")
    rows = log.entries(session_id=1)
    assert [r["kind"] for r in rows] == ["user", "assistant", "tool", "assistant", "system"]
    tool = rows[2]
    assert tool["tool_name"] == "video_info" and tool["tool_status"] == "ok"
    assert json.loads(tool["tool_args"])["bvid"] == "BV1abc"   # 工具行存的是本次参数
    calls = json.loads(rows[1]["tool_args"])                     # 助手行存的是发起的调用清单
    assert calls[0]["name"] == "video_info"
    log.close()


def test_tool_result_and_args_capped(tmp_path):
    log = _log(tmp_path, max_args_chars=50, max_result_chars=60)
    log.append_tool(1, "run-1", "files_read", "x" * 300, "y" * 300, "ok")
    row = log.entries(session_id=1)[0]
    assert len(row["tool_args"]) <= 50 + len("…（参数超长，已按上限截断）")
    assert len(row["tool_result"]) <= 60 + len("…（结果超长，已按上限截断）")
    assert "参数超长" in row["tool_args"] and "结果超长" in row["tool_result"]
    log.close()


def test_run_id_filter_for_debugging(tmp_path):
    log = _log(tmp_path)
    log.append_user(1, "run-a", "第一场", channel="qq")
    log.append_tool(1, "run-a", "video_info", "{}", "done", "ok")
    log.append_user(1, "run-b", "第二场", channel="qq")
    rows = log.entries(session_id=1, run_id="run-a")
    assert len(rows) == 2
    after = log.entries(session_id=1, run_id="run-a", after=rows[0]["id"])
    assert len(after) == 1 and after[0]["kind"] == "tool"
    log.close()


def test_search_spans_tool_activity(tmp_path):
    log = _log(tmp_path)
    log.append_user(1, "run-1", "看下这个视频")
    log.append_tool(1, "run-1", "peek_command", '{"argv": ["git", "status"]}', "nothing to commit", "ok")
    # 命中工具名
    hits = log.search("peek_command")
    assert hits and hits[0]["kind"] == "tool"
    # 命中工具结果文本
    hits2 = log.search("nothing to commit")
    assert hits2 and hits2[0]["tool_result"].startswith("nothing")
    # 命中对话文本
    hits3 = log.search("看下这个视频")
    assert hits3 and hits3[0]["kind"] == "user"
    log.close()


def test_day_index_and_stats(tmp_path):
    log = _log(tmp_path)
    log.append_user(1, "run-1", "hi", channel="web")
    log.append_tool(1, "run-1", "get_time", "{}", "10:00", "ok")
    index = log.day_index()
    assert index[0]["entries"] == 2
    sessions = log.sessions_for_day(index[0]["day"])
    assert sessions[0]["tools"] == 1
    stats = log.stats()
    assert stats["entries"] == 2 and stats["tools"] == 1 and stats["runs"] == 1
    log.close()


def test_prune_and_reopen(tmp_path):
    log = _log(tmp_path)
    old_day = (dt.datetime.now().astimezone() - dt.timedelta(days=30)).date().isoformat()
    log.append_user(
        1, "run-old", "旧", now=dt.datetime.fromisoformat(old_day + "T10:00:00").astimezone()
    )
    log.append_user(1, "run-new", "新")
    assert log.prune(7) == 1
    log.close()
    again = _log(tmp_path)
    assert again.stats()["entries"] == 1
    assert again.search("旧") == []
    again.close()
