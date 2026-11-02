"""M1 对话全量记忆单元：记录、索引、精确读取、检索与整理取材。"""

from __future__ import annotations

import datetime as dt

import pytest

from omni.memory.dialogue import DialogueLog


def _log(tmp_path, name="memory.db", **kw) -> DialogueLog:
    return DialogueLog(tmp_path / name, **kw)


def test_append_and_paginated_read(tmp_path):
    log = _log(tmp_path)
    ids = [log.append(1, "user", f"第{i}句", channel="web") for i in range(5)]
    rows = log.entries(session_id=1, after=ids[2], limit=10)
    assert [r["id"] for r in rows] == ids[3:]
    assert all(r["role"] in ("user",) for r in rows)
    # 只按天过滤同样走游标
    day_rows = log.entries(day=rows[0]["day"], after=0, limit=100)
    assert len(day_rows) == 5
    log.close()


def test_role_validation(tmp_path):
    log = _log(tmp_path)
    with pytest.raises(ValueError, match="user/assistant"):
        log.append(1, "tool", "x")
    log.close()


def test_day_index_and_sessions_for_day(tmp_path):
    log = _log(tmp_path)
    log.append(7, "user", "帮我看看这个视频", channel="qq")
    log.append(7, "assistant", "好的，这就去看")
    log.append(8, "user", "另一个会话开头", channel="web")
    index = log.day_index()
    assert len(index) == 1
    assert index[0]["sessions"] == 2 and index[0]["entries"] == 3
    sessions = log.sessions_for_day(index[0]["day"])
    assert {s["session_id"] for s in sessions} == {7, 8}
    by_id = {s["session_id"]: s for s in sessions}
    assert by_id[7]["title"] == "帮我看看这个视频"
    assert by_id[8]["entries"] == 1
    log.close()


def test_entry_truncation_keeps_marker(tmp_path):
    log = _log(tmp_path, max_entry_chars=50)
    log.append(1, "user", "长" * 200)
    row = log.entries(session_id=1)[0]
    assert len(row["content"]) < 200
    assert "已按上限截断" in row["content"]
    log.close()


def test_search_fts_and_cjk_fallback(tmp_path):
    log = _log(tmp_path)
    log.append(1, "user", "今天讨论了 OmniOperator 的记忆系统设计", channel="web")
    log.append(2, "user", "晚饭吃火锅", channel="qq")
    # 长词走 FTS（trigram ≥3 字符）
    hit = log.search("记忆系统设计")
    assert hit and hit[0]["session_id"] == 1
    # 中文两字词 FTS 查不到 → LIKE 全词 → 2-gram 兜底链
    hit2 = log.search("火锅")
    assert hit2 and hit2[0]["session_id"] == 2
    assert log.search("不存在的词组xyz") == []
    log.close()


def test_max_id_and_material_for_day(tmp_path):
    log = _log(tmp_path)
    log.append(1, "user", "早", channel="web")
    log.append(1, "assistant", "早呀")
    log.append(1, "user", "晚", channel="web")
    day = log.days()[0]
    assert log.has_activity(day)
    material = log.material_for_day(day, max_chars=10000)
    assert "] user: 早" in material and "] assistant: 早呀" in material
    assert log.max_id_for_day(day) == log.entries(day=day)[-1]["id"]
    # 超限取材：保头留尾加标记（小上限退短标记，但必须有正文）
    tight = log.material_for_day(day, max_chars=35)
    assert "超长" in tight and "] user: 早" in tight
    assert "] assistant: 早呀" not in tight
    # 正常档位：长标记 + 头尾拼接
    big = _log(tmp_path, name="big.db")
    for i in range(12):
        big.append(2, "user", f"第{i}条比较长的消息内容举例")
    wide = big.material_for_day(big.days()[0], max_chars=140)
    assert "中段已省略" in wide and "第0条" in wide and "第11条" in wide
    assert "第5条" not in wide
    big.close()
    log.close()


def test_prune_respects_retention(tmp_path):
    log = _log(tmp_path)
    now = dt.datetime.now().astimezone()
    old = (now - dt.timedelta(days=10)).date().isoformat()
    log.append(1, "user", "旧记录", now=dt.datetime.fromisoformat(old + "T10:00:00").astimezone())
    log.append(1, "user", "新记录")
    removed = log.prune(7)
    assert removed == 1
    assert log.entries(session_id=1)[0]["content"] == "新记录"
    assert log.prune(0) == 0  # 0=永不清理
    log.close()


def test_reopen_is_idempotent(tmp_path):
    log = _log(tmp_path)
    log.append(1, "user", "重启前的记录", channel="web")
    log.close()
    again = _log(tmp_path)
    assert len(again.entries(session_id=1)) == 1
    assert again.fts_enabled  # 重建不报错、检索可用
    hit = again.search("重启前")
    assert hit and hit[0]["content"] == "重启前的记录"
    again.close()


def test_stats(tmp_path):
    log = _log(tmp_path)
    log.append(1, "user", "a", channel="web")
    log.append(1, "assistant", "b")
    stats = log.stats()
    assert stats["entries"] == 2 and stats["days"] == 1 and stats["sessions"] == 1
    assert stats["chars"] == 2
    log.close()
