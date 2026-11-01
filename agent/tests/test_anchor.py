"""沉淀锚定单测：原文锚定机制（P5-b）+ 人物条目的出处落库。

学自 dsh-bili-humanism（`lib/personality.mjs` 的 `PERSONALITY_REFLECTION_REQUIRED`）：
抄的是**机制**（确定性逐字校验 + 可回查的来源指针），不抄形态（我们不建
human-world.md，也不要求"整句逐字"——我们写的是概括，要求的是**摘录支撑**）。

纯逻辑部分完全不碰数据库，可离线跑；存储与工具部分用临时目录。
"""
from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from omni.memory.anchor import (
    MAX_EVIDENCE_CHARS,
    AnchorError,
    anchor_scope,
    current_anchor,
    describe_source,
    normalize_evidence,
    require_evidence,
    verify_evidence,
)
from omni.memory.guard import MemoryGuard
from omni.memory.owner import OwnerMemory
from omni.memory.people import PersonError, PersonStore
from omni.memory.state import StateStore
from omni.tools.personal.people import register_people_tools
from omni.tools.personal.profile import register_profile_tools
from omni.tools.personal.resident import register_resident_tools
from omni.tools.registry import ToolRegistry

TRANSCRIPT = (
    "[user] 我下周要去趟成都，帮我记一下\n"
    "[assistant] 好的，出行安排记下了：成都。"
)


# ---------------------------------------------------------------- 纯逻辑层


def test_normalize_evidence_collapses_whitespace():
    assert normalize_evidence("  出差 \n 去  成都 ") == "出差 去 成都"
    assert normalize_evidence("") == ""
    assert normalize_evidence("全角　空格") == "全角 空格"


def test_verify_evidence_accepts_a_verbatim_quote():
    assert verify_evidence("帮我记一下", TRANSCRIPT) == "帮我记一下"


def test_verify_evidence_tolerates_line_break_differences():
    """模型抄的换行/缩进常与来源不同：忽略空白后仍算命中。

    假阴性（合法摘录被拒）比略松的比对更糟，所以口径是"忽略全部空白"。
    """
    assert verify_evidence("我下周要去趟成都", "[user] 我下周\n要去趟成都，帮我记一下")


def test_verify_evidence_cannot_span_two_messages():
    """来源材料每条消息都带角色/时间前缀，跨消息拼接的摘录对不上。

    这比"忽略空白"更严的一道边界：拼接摘录不会蒙混过关。
    """
    with pytest.raises(AnchorError):
        verify_evidence("记一下好的", "[user] 帮我记一下\n[assistant] 好的")


def test_verify_evidence_rejects_a_paraphrase():
    with pytest.raises(AnchorError) as err:
        verify_evidence("用户要去成都出差", TRANSCRIPT)
    assert "找不到" in str(err.value)


def test_verify_evidence_rejects_empty_and_overlong():
    with pytest.raises(AnchorError):
        verify_evidence("   ", TRANSCRIPT)
    with pytest.raises(AnchorError) as err:
        verify_evidence("字" * (MAX_EVIDENCE_CHARS + 1), TRANSCRIPT)
    assert "超长" in str(err.value)


def test_anchor_scope_binds_and_always_restores():
    assert current_anchor() is None
    with anchor_scope(TRANSCRIPT, source="review", source_ref="session:7") as ctx:
        assert ctx.source == "review" and ctx.source_ref == "session:7"
        assert current_anchor() is not None
    assert current_anchor() is None

    with pytest.raises(RuntimeError):
        with anchor_scope(TRANSCRIPT, source="review", source_ref="session:7"):
            raise RuntimeError("boom")
    assert current_anchor() is None  # 异常路径也还原，不把上下文漏给下一个任务


def test_require_evidence_outside_anchor_context_stores_nothing():
    """对话路径：出处无从核对，一律不落库（存下没核对过的出处比不存更糟）。"""
    assert require_evidence("随便一段") == ("", "")


def test_require_evidence_inside_anchor_context_requires_a_quote():
    with anchor_scope(TRANSCRIPT, source="review", source_ref="session:7"):
        evidence, ref = require_evidence("帮我记一下")
        assert evidence == "帮我记一下" and ref == "session:7"
        with pytest.raises(AnchorError) as err:
            require_evidence("")
        assert "必须带原文摘录" in str(err.value)
        with pytest.raises(AnchorError):
            require_evidence("用户要去成都")


def test_describe_source_renders_readable_labels():
    assert describe_source("review", "session:42") == "对话 #42"
    assert describe_source("journal", "journal:daily:2026-11-01") == "日记 2026-11-01"
    assert describe_source("journal", "journal:weekly:2026-W44") == "周记 2026-W44"
    assert describe_source("journal", "journal:monthly:2026-11") == "月记 2026-11"
    assert describe_source("chat", "") == "chat"


# ---------------------------------------------------------------- 存储层


def _store(tmp_path):
    return PersonStore(tmp_path / "m.db")


def test_anchored_write_without_evidence_is_rejected(tmp_path):
    store = _store(tmp_path)
    owner = store.ensure_owner("所有者")
    with anchor_scope(TRANSCRIPT, source="review", source_ref="session:7"):
        with pytest.raises(PersonError) as err:
            store.add_entry(owner, "resident", "用户下周要去成都")
    assert "必须带原文摘录" in str(err.value)
    assert store.entries(owner) == []


def test_anchored_write_with_a_quote_outside_the_material_is_rejected(tmp_path):
    store = _store(tmp_path)
    owner = store.ensure_owner("所有者")
    with anchor_scope(TRANSCRIPT, source="review", source_ref="session:7"):
        with pytest.raises(PersonError) as err:
            store.add_entry(owner, "resident", "用户下周要去成都", evidence="用户下周要去成都")
    assert "找不到" in str(err.value)
    assert store.entries(owner) == []


def test_anchored_write_stamps_source_ref_and_evidence(tmp_path):
    store = _store(tmp_path)
    owner = store.ensure_owner("所有者")
    with anchor_scope(TRANSCRIPT, source="review", source_ref="session:7"):
        entry_id = store.add_entry(
            owner, "resident", "用户下周要去成都", evidence="我下周要去趟成都"
        )
    entry = store.entries(owner)[0]
    assert entry["id"] == entry_id
    assert entry["source"] == "review"
    assert entry["source_ref"] == "session:7"
    assert entry["evidence"] == "我下周要去趟成都"


def test_chat_write_keeps_no_unverified_evidence(tmp_path):
    """对话路径给了摘录也不落库：没有可核的来源，留着就是"看着有依据"。"""
    store = _store(tmp_path)
    owner = store.ensure_owner("所有者")
    store.add_entry(owner, "resident", "用户下周要去成都", source="chat", evidence="我下周要去趟成都")
    entry = store.entries(owner)[0]
    assert entry["evidence"] == "" and entry["source_ref"] == ""
    assert entry["source"] == "chat"


def test_anchored_replace_requires_a_fresh_quote(tmp_path):
    store = _store(tmp_path)
    owner = store.ensure_owner("所有者")
    with anchor_scope(TRANSCRIPT, source="review", source_ref="session:7"):
        entry_id = store.add_entry(
            owner, "resident", "用户下周要去成都", evidence="我下周要去趟成都"
        )
        with pytest.raises(PersonError) as err:
            store.edit_entry(entry_id, "用户下周要去成都，顺便订酒店")
        assert "必须带原文摘录" in str(err.value)
        assert store.edit_entry(entry_id, "用户下周要去成都", evidence="我下周要去趟成都")


def test_chat_replace_clears_the_stale_quote_when_content_changes(tmp_path):
    """出处与内容绑定：内容改了，旧摘录未必还支撑新内容——清掉比留错更诚实。"""
    store = _store(tmp_path)
    owner = store.ensure_owner("所有者")
    with anchor_scope(TRANSCRIPT, source="review", source_ref="session:7"):
        entry_id = store.add_entry(
            owner, "resident", "用户下周要去成都", evidence="我下周要去趟成都"
        )
    # 内容没变 → 保留旧出处
    assert store.edit_entry(entry_id, "用户下周要去成都")
    assert store.entries(owner)[0]["evidence"] == "我下周要去趟成都"
    # 内容变了 → 出处清空
    assert store.edit_entry(entry_id, "用户下周要去成都，顺便订酒店")
    assert store.entries(owner)[0]["evidence"] == ""


def test_old_database_gets_source_ref_and_evidence_columns(tmp_path):
    """旧库补列：存量条目没有出处是事实，不猜、不回填。"""
    db = tmp_path / "m.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE people (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            importance TEXT NOT NULL DEFAULT 'known',
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT
        );
        CREATE TABLE person_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
            entry_kind TEXT NOT NULL,
            content TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'chat',
            created_at TEXT NOT NULL,
            updated_at TEXT,
            expires_at TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO people(name, importance, created_at)"
        " VALUES ('小张', 'known', '2026-01-01T00:00:00+08:00')"
    )
    conn.execute(
        "INSERT INTO person_entries(person_id, entry_kind, content, source, created_at)"
        " VALUES (1, 'resident', '在准备考研', 'chat', '2026-01-01T00:00:00+08:00')"
    )
    conn.commit()
    conn.close()

    store = PersonStore(db)
    entry = store.entries(1)[0]
    assert entry["content"] == "在准备考研"
    assert entry["source_ref"] == "" and entry["evidence"] == ""
    store.close()

    conn = sqlite3.connect(db)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(person_entries)")}
    conn.close()
    assert {"source_ref", "evidence"} <= columns


# ---------------------------------------------------------------- 工具层


def _make(tmp_path):
    people = PersonStore(tmp_path / "m.db")
    state = StateStore(tmp_path / "state.db")
    guard = MemoryGuard(state, max_writes_per_day=200)
    owner = OwnerMemory(people)
    registry = ToolRegistry()
    register_people_tools(registry, people, guard=guard)
    register_resident_tools(registry, owner)
    register_profile_tools(registry, owner)
    return SimpleNamespace(registry=registry, people=people, owner=owner)


def _dispatch(fixture, name: str, args: dict) -> str:
    return fixture.registry.dispatch(name, json.dumps(args))


def test_tools_reject_anchored_writes_without_a_quote(tmp_path):
    fixture = _make(tmp_path)
    with anchor_scope(TRANSCRIPT, source="review", source_ref="session:7"):
        resident = _dispatch(fixture, "resident_memory", {"operation": "add", "content": "用户下周要去成都"})
        profile = _dispatch(fixture, "profile_manage", {"operation": "add", "content": "用户常驻成都"})
        person = _dispatch(fixture, "person_remember", {"person": "小张", "content": "在成都工作"})
    for result in (resident, profile, person):
        assert "必须带原文摘录" in result, result
    assert fixture.owner.entries() == []


def test_tools_accept_anchored_writes_with_a_quote(tmp_path):
    fixture = _make(tmp_path)
    with anchor_scope(TRANSCRIPT, source="review", source_ref="session:7"):
        resident = _dispatch(fixture, "resident_memory", {
            "operation": "add", "content": "用户下周要去成都", "evidence": "我下周要去趟成都",
        })
        profile = _dispatch(fixture, "profile_manage", {
            "operation": "add", "content": "用户常驻成都", "evidence": "我下周要去趟成都",
        })
        person = _dispatch(fixture, "person_remember", {
            "person": "小张", "content": "在成都工作", "evidence": "我下周要去趟成都",
        })
    assert "已写入常驻记忆" in resident
    assert "已加入档案" in profile
    assert "已记入" in person

    owner_id = fixture.people.ensure_owner("所有者")
    assert fixture.people.entries(owner_id)[0]["source_ref"] == "session:7"
    zhang = fixture.people.find_by_name("小张")[0]
    assert fixture.people.entries(zhang["id"])[0]["source_ref"] == "session:7"


def test_tools_accept_chat_writes_without_a_quote(tmp_path):
    """对话路径不受影响：正常记忆写入不该被出处要求挡住。"""
    fixture = _make(tmp_path)
    result = _dispatch(fixture, "resident_memory", {"operation": "add", "content": "约定：只改测试"})
    assert "已写入常驻记忆" in result


def test_view_shows_the_provenance_section(tmp_path):
    fixture = _make(tmp_path)
    with anchor_scope(TRANSCRIPT, source="review", source_ref="session:7"):
        _dispatch(fixture, "resident_memory", {
            "operation": "add", "content": "用户下周要去成都", "evidence": "我下周要去趟成都",
        })
    view = _dispatch(fixture, "resident_memory", {"operation": "view"})
    assert "出处" in view
    assert "对话 #7" in view
    assert "我下周要去趟成都" in view


def test_person_lookup_shows_the_provenance_section(tmp_path):
    fixture = _make(tmp_path)
    with anchor_scope(TRANSCRIPT, source="journal", source_ref="journal:daily:2026-11-01"):
        _dispatch(fixture, "person_remember", {
            "person": "小张", "content": "在成都工作", "evidence": "我下周要去趟成都",
        })
    out = _dispatch(fixture, "person_lookup", {"query": "小张"})
    assert "出处" in out
    assert "日记 2026-11-01" in out
    assert "我下周要去趟成都" in out


def test_injection_blocks_do_not_carry_the_evidence(tmp_path):
    """体积纪律：常驻 2200 / 档案 2000 是硬上限，出处不进注入块，只在核对面可见。

    这条是刻意取舍（参考实现把来源写进每轮注入的人格文件），得有守卫，
    免得日后有人"顺手"把出处塞回提示词，把注入体积顶穿。
    """
    fixture = _make(tmp_path)
    with anchor_scope(TRANSCRIPT, source="review", source_ref="session:7"):
        _dispatch(fixture, "resident_memory", {
            "operation": "add", "content": "用户下周要去成都", "evidence": "我下周要去趟成都",
        })
        _dispatch(fixture, "profile_manage", {
            "operation": "add", "content": "用户常驻成都", "evidence": "我下周要去趟成都",
        })
    resident_block = fixture.owner.block()
    profile_block = fixture.owner.profile_block()
    assert "用户下周要去成都" in resident_block
    assert "我下周要去趟成都" not in resident_block
    assert "对话 #7" not in resident_block
    assert "用户常驻成都" in profile_block
    assert "我下周要去趟成都" not in profile_block
