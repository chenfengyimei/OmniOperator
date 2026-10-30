"""持久上下文与索引生命周期（D11）。

两条主线：

1. **索引只在需要时建**：以前每开一个连接就把 FTS 全库 rebuild 一遍（review 每轮
   一次、心跳每周期一次、工作台每个请求线程一个连接），数据一多就是启动风暴。
   现在由触发器维护增量，`meta` 记录状态，只有"首次 / 分词器升级 / 旧库无状态"
   才重建一次。
2. **摘要是持久事实**：摘要按消息 id 区间落库，重启后仍在；重复压缩同一段不会再
   花一次钱；压缩失败原样返回，绝不写半截摘要改写用户的历史。
"""
from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from omni.config import AppConfig
from omni.core.agent import Agent
from omni.core.compress import SUMMARY_HEADER, assemble_context
from omni.memory.store import MemoryStore
from omni.tools.registry import ToolRegistry


def _reply(text: str) -> Any:
    """与 Gateway.Reply 同形状：内核只依赖 text 与 tool_calls。"""
    from types import SimpleNamespace

    return SimpleNamespace(text=text, tool_calls=[], usage=None, cost_micro=None)


class _SummaryGateway:
    """压缩走一套回答，其余走另一套；记录每次调用的用途。"""

    def __init__(self, summary: str = "摘要：用户要求只用只读命令", *, fail: bool = False) -> None:
        self.summary = summary
        self.fail = fail
        self.calls: list[str] = []

    def resolve(self, _route: str) -> str:
        return "fake/model"

    def complete(self, messages, *, model=None, tools=None, purpose="chat", **kwargs):
        self.calls.append(purpose)
        if purpose == "compress":
            if self.fail:
                raise RuntimeError("摘要模型挂了")
            return _reply(self.summary)
        return _reply("好的")


def _seed(store: MemoryStore, sid: int, count: int, *, text: str = "内容") -> list[int]:
    ids = []
    for index in range(count):
        role = "user" if index % 2 == 0 else "assistant"
        store.append_message(sid, role, f"{text}{index}：" + "细节" * 40, auto_title=False)
        ids.append(store.recent_messages_ex(sid, 1)[-1][0])
    return ids


# ---------------------------------------------------------------- 索引生命周期


def test_fts_is_built_once_and_reused(tmp_path) -> None:
    path = tmp_path / "m.db"
    first = MemoryStore(path)
    try:
        assert first.fts_enabled is True
        assert first.fts_rebuilt is True and first.fts_reason == "fresh"
        assert first.fts_tokenizer in {"trigram", "unicode61"}
    finally:
        first.close()

    second = MemoryStore(path)
    try:
        # 第二次打开不重建：数据没变、分词器没变、状态记录还在
        assert second.fts_rebuilt is False
        second.append_message(2, "user", "新消息也要能搜到：蓝莓拿铁", auto_title=False)
        assert second.search_messages("蓝莓拿铁")
    finally:
        second.close()


def test_legacy_index_without_state_rebuilds_exactly_once(tmp_path) -> None:
    path = tmp_path / "m.db"
    store = MemoryStore(path)
    sid = store.new_session()
    store.append_message(sid, "user", "旧库里的历史：抹茶蛋糕", auto_title=False)
    # 模拟 D11 之前的库：索引在、状态记录没有
    store._conn.execute("DELETE FROM meta WHERE key = 'fts'")
    store._conn.commit()
    store.close()

    upgraded = MemoryStore(path)
    try:
        assert upgraded.fts_rebuilt is True and upgraded.fts_reason == "legacy"
        assert upgraded.search_messages("抹茶蛋糕")  # 旧数据被一次性补齐
    finally:
        upgraded.close()

    again = MemoryStore(path)
    try:
        assert again.fts_rebuilt is False  # 升级只发生一次
    finally:
        again.close()


def test_parallel_connections_do_not_each_rebuild(tmp_path) -> None:
    """review 每次新开连接、心跳每周期新开连接：只有第一个该重建。"""
    path = tmp_path / "m.db"
    first = MemoryStore(path)
    first.close()
    stores = [MemoryStore(path) for _ in range(3)]
    try:
        assert [s.fts_rebuilt for s in stores] == [False, False, False]
    finally:
        for store in stores:
            store.close()


def test_triggers_keep_index_in_sync(tmp_path) -> None:
    store = MemoryStore(tmp_path / "m.db")
    try:
        sid = store.new_session()
        store.append_message(sid, "user", "触发器同步：黑糖珍珠", auto_title=False)
        store.save_fact("用户喜欢黑糖珍珠")
        assert store.search_messages("黑糖珍珠")
        assert any("黑糖珍珠" in fact for fact in store.search_facts("黑糖珍珠"))
        assert store._fts_triggers is True
    finally:
        store.close()


def test_two_char_query_still_hits(tmp_path) -> None:
    """trigram 需要 ≥3 字，2 字查询靠后面的降级链，不能被 D11 弄丢。"""
    store = MemoryStore(tmp_path / "m.db")
    try:
        store.save_fact("用户喜欢喝拿铁")
        assert store.search_facts("拿铁"), "2 字中文查询退化"
        assert store.search_facts("喜欢"), "2 字片段（整句查询的兜底路径）退化"
        # 不在原文里的 2 字组合本来就不该命中：不为了"看起来能搜"去伪造结果
        assert store.search_facts("喝什么") == []
    finally:
        store.close()


# ---------------------------------------------------------------- 摘要持久化


def _rows(store: MemoryStore, sid: int, limit: int = 100):
    return store.recent_messages_ex(sid, limit)


def test_assemble_persists_summary_with_message_range(tmp_path) -> None:
    store = MemoryStore(tmp_path / "m.db")
    sid = store.new_session()
    ids = _seed(store, sid, 12)
    gateway = _SummaryGateway()

    body, info = assemble_context(
        _rows(store, sid),
        store.active_summary(sid),
        gateway=gateway,
        model="fake/model",
        trigger_chars=200,
        keep_recent=4,
        min_messages=4,
        save=lambda **fields: store.save_summary(sid, **fields),
    )

    assert gateway.calls == ["compress"]
    assert info["summarized"] is True
    record = store.active_summary(sid)
    assert record is not None
    _id, from_id, to_id, content, version = record
    assert version == 1
    assert from_id == ids[0] and to_id == ids[-5]  # 覆盖的是被压掉的那一段
    assert "只读命令" in content
    assert body[0]["role"] == "user" and SUMMARY_HEADER in body[0]["content"]
    # 摘要并进保留窗的第一条 user（维持严格角色交替），所以是 1+3 条
    assert len(body) == 4 and body[1]["role"] == "assistant"
    store.close()


def test_repeat_compression_does_not_spend_again(tmp_path) -> None:
    store = MemoryStore(tmp_path / "m.db")
    sid = store.new_session()
    _seed(store, sid, 12)
    gateway = _SummaryGateway()
    kwargs = dict(
        gateway=gateway,
        model="fake/model",
        trigger_chars=200,
        keep_recent=4,
        min_messages=4,
        save=lambda **fields: store.save_summary(sid, **fields),
    )
    assemble_context(_rows(store, sid), None, **kwargs)
    first = store.active_summary(sid)
    assert first is not None

    # 同一段历史再组装一次：摘要已覆盖，不再调用摘要模型
    body, info = assemble_context(_rows(store, sid), store.active_summary(sid), **kwargs)
    assert gateway.calls == ["compress"]
    assert info["summarized"] is False
    assert store.active_summary(sid) == first
    assert SUMMARY_HEADER in body[0]["content"]
    store.close()


def test_summary_extends_with_new_version_and_supersedes(tmp_path) -> None:
    store = MemoryStore(tmp_path / "m.db")
    sid = store.new_session()
    _seed(store, sid, 12)
    kwargs = dict(
        gateway=_SummaryGateway("第一段摘要"),
        model="fake/model",
        trigger_chars=200,
        keep_recent=4,
        min_messages=4,
        save=lambda **fields: store.save_summary(sid, **fields),
    )
    assemble_context(_rows(store, sid), None, **kwargs)
    first = store.active_summary(sid)
    assert first is not None

    _seed(store, sid, 12, text="后续")
    gateway = _SummaryGateway("第二段摘要")
    kwargs["gateway"] = gateway
    body, info = assemble_context(_rows(store, sid), first, **kwargs)

    second = store.active_summary(sid)
    assert second is not None and second[0] != first[0]
    assert second[4] == 2  # 版本递增
    assert "第一段摘要" in second[3] and "第二段摘要" in second[3]  # 旧摘要不被丢弃
    assert second[1] == first[1]  # 覆盖区间从旧摘要的起点继续
    assert info["version"] == 2
    history = store.summary_history(sid)
    superseded = [row for row in history if row["id"] == first[0]][0]
    assert superseded["superseded_by"] == second[0]
    assert SUMMARY_HEADER in body[0]["content"]
    store.close()


def test_summary_failure_degrades_without_writing(tmp_path) -> None:
    store = MemoryStore(tmp_path / "m.db")
    sid = store.new_session()
    _seed(store, sid, 12)
    gateway = _SummaryGateway(fail=True)

    body, info = assemble_context(
        _rows(store, sid),
        None,
        gateway=gateway,
        model="fake/model",
        trigger_chars=200,
        keep_recent=4,
        min_messages=4,
        save=lambda **fields: store.save_summary(sid, **fields),
    )

    assert info["summarized"] is False and info["reason"] == "summary_failed"
    assert store.active_summary(sid) is None  # 不留半截摘要
    assert len(body) == 12 and SUMMARY_HEADER not in body[0]["content"]  # 原始历史原样保留
    store.close()


def test_constraint_survives_restart(tmp_path) -> None:
    path = tmp_path / "m.db"
    store = MemoryStore(path)
    sid = store.new_session()
    store.append_message(sid, "user", "硬约束：只允许 SELECT，不要 DELETE", auto_title=False)
    _seed(store, sid, 11)
    assemble_context(
        _rows(store, sid),
        store.active_summary(sid),
        gateway=_SummaryGateway("摘要：硬约束只允许 SELECT"),
        model="fake/model",
        trigger_chars=200,
        keep_recent=4,
        min_messages=4,
        save=lambda **fields: store.save_summary(sid, **fields),
    )
    store.close()

    # 进程重启：新连接、新内存，摘要仍在，且不再重新压缩
    reopened = MemoryStore(path)
    try:
        record = reopened.active_summary(sid)
        assert record is not None and "SELECT" in record[3]
        gateway = _SummaryGateway("不该被调用")
        body, info = assemble_context(
            _rows(reopened, sid),
            record,
            gateway=gateway,
            model="fake/model",
            trigger_chars=200,
            keep_recent=4,
            min_messages=4,
            save=lambda **fields: reopened.save_summary(sid, **fields),
        )
        assert gateway.calls == []
        assert SUMMARY_HEADER in body[0]["content"] and "SELECT" in body[0]["content"]
    finally:
        reopened.close()


def test_save_summary_is_idempotent_per_range(tmp_path) -> None:
    store = MemoryStore(tmp_path / "m.db")
    sid = store.new_session()
    first = store.save_summary(sid, from_msg_id=1, to_msg_id=10, content="A")
    second = store.save_summary(sid, from_msg_id=1, to_msg_id=10, content="B")
    assert first[0] == second[0] and second[2] is False
    assert store.active_summary(sid)[3] == "A"  # 重复写入不覆盖已有内容
    store.close()


def test_compression_disabled_keeps_existing_summary(tmp_path) -> None:
    """关掉压缩只是不再产生新摘要；已有摘要仍然进上下文，否则等于静默丢历史。"""
    path = tmp_path / "m.db"
    store = MemoryStore(path)
    sid = store.new_session()
    store.save_summary(sid, from_msg_id=1, to_msg_id=1, content="旧摘要")
    _seed(store, sid, 6)
    config = AppConfig()
    config.compression.enabled = False
    agent = Agent(config, _SummaryGateway(), store, ToolRegistry(), sid)

    body, info = agent._assemble_body(sid)

    assert info["summarized"] is False
    assert SUMMARY_HEADER in body[0]["content"] and "旧摘要" in body[0]["content"]
    store.close()


def test_agent_summarizes_long_history_and_records_event(tmp_path) -> None:
    store = MemoryStore(tmp_path / "m.db")
    sid = store.new_session()
    _seed(store, sid, 24)
    config = AppConfig()
    config.compression.trigger_chars = 300
    config.compression.min_messages = 4
    config.compression.keep_recent = 4
    gateway = _SummaryGateway("摘要：用户只要结论")
    agent = Agent(config, gateway, store, ToolRegistry(), sid)

    agent.run_turn("继续")

    assert "compress" in gateway.calls
    assert store.active_summary(sid) is not None
