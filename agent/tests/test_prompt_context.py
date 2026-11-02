"""按真实模型请求验证稳定前缀、快照持久化与上下文边界，不调用外部模型。"""

from copy import deepcopy
import datetime as dt
import json
import sqlite3
from types import SimpleNamespace

import pytest

from omni.config import AppConfig
from omni.core.agent import Agent
from omni.core.compress import assemble_context, compress_if_needed, assert_invariants
from omni.core.context import CONTEXT_HEADER, CONTEXT_FOOTER, TurnContext, render_rows, system_prompt
from omni.llm.usage import Usage
from omni.memory.resident import ResidentMemory
from omni.memory.store import MemoryStore, DEFAULT_TITLE
from omni.tools.registry import Tool, ToolRegistry


class Capture:
    def __init__(self):
        self.calls = []

    def resolve(self, route):
        return "fake/model"

    def complete(self, messages, **kwargs):
        self.calls.append((deepcopy(messages), {**kwargs, "tools": deepcopy(kwargs.get("tools"))}))
        return SimpleNamespace(text="已回答", tool_calls=[])


@pytest.fixture
def fixture(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    sid = store.new_session()
    resident = ResidentMemory(tmp_path / "MEMORY.md")
    gateway = Capture()
    registry = ToolRegistry()
    config = AppConfig()
    config.compression.enabled = False
    stamps = iter([dt.datetime(2026, 10, 30, 12, i, tzinfo=dt.timezone.utc) for i in range(40)])
    agent = Agent(config, gateway, store, registry, sid, resident=resident, clock=lambda: next(stamps))
    try:
        yield agent, gateway, store, resident
    finally:
        store.close()


def context(message):
    text = message["content"]
    return json.loads(text.rsplit(CONTEXT_HEADER, 1)[1].removesuffix(CONTEXT_FOOTER))


def test_time_and_memory_changes_do_not_rewrite_previous_request_prefix(fixture):
    agent, gateway, store, resident = fixture
    resident.add("工作笔记甲")
    store.profile_add("档案甲")
    agent.chat("第一轮问题")
    first = gateway.calls[-1][0]
    resident.add("工作笔记乙")
    store.profile_add("档案乙")
    agent.chat("第二轮问题")
    second = gateway.calls[-1][0]
    assert second[: len(first)] == first
    assert "2026-10-30" not in first[0]["content"] and "档案甲" not in first[0]["content"]
    assert context(first[-1])["observed_at"].endswith("12:00:00+00:00")
    assert context(second[-1])["observed_at"].endswith("12:01:00+00:00")
    assert "档案乙" in context(second[-1])["personal_memory"]["profile"]
    assert second[-1]["content"].startswith("第二轮问题" + CONTEXT_HEADER)
    assert_invariants(second)


def test_unchanged_memory_is_not_repeated_but_history_stays_identical(fixture):
    agent, gateway, store, resident = fixture
    resident.add("同一份常驻记忆")
    agent.chat("第一轮")
    first = gateway.calls[-1][0]
    agent.chat("第二轮")
    second = gateway.calls[-1][0]
    agent.chat("第三轮")
    third = gateway.calls[-1][0]
    assert second[: len(first)] == first and third[: len(second)] == second
    assert "personal_memory" not in context(second[-1])
    assert "personal_memory" not in context(third[-1])
    assert json.dumps(third, ensure_ascii=False).count("同一份常驻记忆") == 1


def test_snapshots_survive_reopen_and_raw_chat_fts_titles_are_unchanged(tmp_path):
    file = tmp_path / "memory.db"
    store = MemoryStore(file)
    sid = store.new_session()
    gateway = Capture()
    config = AppConfig()
    config.compression.enabled = False
    agent = Agent(
        config, gateway, store, ToolRegistry(), sid, clock=lambda: dt.datetime(2026, 10, 30, tzinfo=dt.timezone.utc)
    )
    store.profile_add("唯一私密档案词")
    agent.chat("纯净的用户原文")
    first = gateway.calls[-1][0]
    assert store.recent_messages(sid, 10) == [("user", "纯净的用户原文"), ("assistant", "已回答")]
    assert store.list_sessions()[0][1] == "纯净的用户原文"
    assert not store.search_messages("唯一私密档案词")
    store.close()
    store = MemoryStore(file)
    try:
        agent = Agent(
            config, gateway, store, ToolRegistry(), sid, clock=lambda: dt.datetime(2027, 1, 1, tzinfo=dt.timezone.utc)
        )
        agent.chat("重启后提问")
        assert gateway.calls[-1][0][: len(first)] == first
        other = store.new_session()
        ids = [r[0] for r in store.recent_messages_ex(sid, 10)]
        assert not store.prompt_contexts(other, ids)
    finally:
        store.close()


def test_group_rendering_strips_personal_data_from_all_old_snapshots(fixture, monkeypatch):
    agent, gateway, store, resident = fixture
    resident.add("私人常驻标识")
    store.profile_add("私人档案标识")
    agent.chat("私聊问题")

    def forbidden():
        raise AssertionError("群聊不得读取私人资料")

    monkeypatch.setattr(resident, "block", forbidden)
    monkeypatch.setattr(store, "profile_block", forbidden)
    agent.chat("群聊问题", personal_context=False)
    outgoing = json.dumps(gateway.calls[-1][0], ensure_ascii=False)
    assert "私人常驻标识" not in outgoing and "私人档案标识" not in outgoing
    assert all(
        "personal_memory" not in context(message) for message in gateway.calls[-1][0] if message["role"] == "user"
    )
    assert "群聊场景" in outgoing


def test_stable_prompt_routes_reminders_to_cron_not_resident_memory():
    prompt = system_prompt("身份固定。", include_personal=True)
    assert "一次性用 cron_once，周期性用 cron_add" in prompt
    assert "不要再把同一任务写进 resident_memory、person_event 或 remember" in prompt
    assert "服务在线状态、一次查询结果" in prompt
    assert "临时事件只是背景记录，不会触发提醒" in prompt


def test_old_now_placeholder_remains_a_stable_reference(fixture):
    agent, gateway, store, resident = fixture
    agent.config.persona.system_prompt = "身份固定。当前时间：{now}，规则固定。"
    agent.chat("甲")
    first = gateway.calls[-1][0][0]
    agent.chat("乙")
    second = gateway.calls[-1][0][0]
    assert first == second and "{now}" not in first["content"]
    assert "规则固定" in first["content"] and "observed_at" in first["content"]


def test_tool_loop_freezes_time_memory_and_tool_schema_until_next_turn(fixture):
    agent, gateway, store, resident = fixture
    resident.add("旧笔记")

    def mutate(args):
        resident.add("本轮才写入的新笔记")
        agent.registry.register(Tool("new_tool", "下一轮生效", {}, lambda args: "ok"))
        return "已更新"

    agent.registry.register(Tool("mutate", "更改笔记", {}, mutate))

    def complete(messages, **kwargs):
        gateway.calls.append((deepcopy(messages), {**kwargs, "tools": deepcopy(kwargs.get("tools"))}))
        if len(gateway.calls) == 1:
            return SimpleNamespace(
                text="",
                tool_calls=[SimpleNamespace(id="call1", function=SimpleNamespace(name="mutate", arguments="{}"))],
            )
        return SimpleNamespace(text="完成", tool_calls=[])

    gateway.complete = complete
    agent.chat("更新笔记")
    first, second = [call[0] for call in gateway.calls]
    assert second[: len(first)] == first
    assert gateway.calls[0][1]["tools"] == gateway.calls[1][1]["tools"]
    assert "本轮才写入的新笔记" not in json.dumps(second, ensure_ascii=False)
    agent.chat("下一轮")
    assert "本轮才写入的新笔记" in json.dumps(gateway.calls[-1][0], ensure_ascii=False)
    assert "new_tool" in [t["function"]["name"] for t in gateway.calls[-1][1]["tools"]]


def test_history_window_reanchors_latest_memory_and_clears_removed_profile(fixture):
    agent, gateway, store, resident = fixture
    resident.add("保留的常驻笔记")
    store.profile_add("待删除档案")
    agent.chat("第一轮")
    first = gateway.calls[-1][0]
    agent.config.limits.max_history_messages = 2
    agent.chat("滚动窗口")
    assert "保留的常驻笔记" in context(gateway.calls[-1][0][-1])["personal_memory"]["resident"]
    store._conn.execute("DELETE FROM profile")
    store._conn.commit()
    agent.chat("档案已清空")
    assert context(gateway.calls[-1][0][-1])["personal_memory"]["profile"] == ""
    assert "待删除档案" in context(first[-1])["personal_memory"]["profile"]


def test_legacy_database_upgrades_without_rewriting_history(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        db.executescript(
            "CREATE TABLE sessions(id INTEGER PRIMARY KEY,created_at TEXT,title TEXT);"
            "CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id INTEGER,role TEXT,content TEXT,created_at TEXT);"
            "INSERT INTO sessions VALUES(1,'old','老会话');"
            "INSERT INTO messages VALUES(1,1,'user','旧消息原文','old');"
        )
    store = MemoryStore(path)
    try:
        assert store.recent_messages(1, 10) == [("user", "旧消息原文")]
        assert store.prompt_contexts(1, [1]) == {}
        assert not store.fts_rebuilt or store.fts_reason == "fresh"
    finally:
        store.close()


def test_user_and_context_insert_is_atomic_and_cleanup_follows_message(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    sid = store.new_session()
    snapshot = TurnContext("2026-10-30", True).payload()
    try:
        store._conn.execute(
            "CREATE TRIGGER reject_context BEFORE INSERT ON message_contexts BEGIN SELECT RAISE(ABORT,'test'); END"
        )
        with pytest.raises(sqlite3.IntegrityError):
            store.append_message(sid, "user", "不可留下半条消息", prompt_context=snapshot)
        assert store.message_count(sid) == 0 and store.list_sessions()[0][1] == DEFAULT_TITLE
        store._conn.execute("DROP TRIGGER reject_context")
        store.append_message(sid, "user", "原文", prompt_context=snapshot)
        mid = store.recent_messages_ex(sid, 10)[0][0]
        assert store.prompt_contexts(sid, [mid]) == {mid: snapshot}
        store._conn.execute("DELETE FROM messages WHERE id=?", (mid,))
        store._conn.commit()
        assert store._conn.execute("SELECT COUNT(*) FROM message_contexts").fetchone()[0] == 0
    finally:
        store.close()


@pytest.mark.parametrize("bad", [{"large": "x" * 65537}, {"invalid": float("nan")}])
def test_invalid_snapshot_cannot_leave_user_message(tmp_path, bad):
    store = MemoryStore(tmp_path / "memory.db")
    sid = store.new_session()
    try:
        with pytest.raises(ValueError):
            store.append_message(sid, "user", "不可落盘", prompt_context=bad)
        assert store.message_count(sid) == 0
    finally:
        store.close()


def test_persistent_compression_keeps_current_turn_and_reanchors_context():
    rows = [(i, "user" if i % 2 else "assistant", "原文" + str(i) * 30) for i in range(1, 10)]
    contexts = {i: TurnContext("time-" + str(i), True, "常驻内容").payload() for i, role, _ in rows if role == "user"}
    gateway = Capture()
    saved = []
    body, info = assemble_context(
        rows,
        None,
        gateway=gateway,
        model="fake/model",
        trigger_chars=1,
        min_messages=1,
        keep_recent=0,
        render=lambda selected: render_rows(selected, contexts, include_personal=True),
        save=lambda **fields: saved.append(fields) or (1, 1, True),
    )
    assert saved[0]["to_msg_id"] == 8 and "time-9" in body[-1]["content"]
    assert context(body[-1])["personal_memory"]["resident"] == "常驻内容"
    assert_invariants([{"role": "system", "content": "稳定规则"}, *body])
    assert "常驻内容" not in gateway.calls[0][0][-1]["content"]


def test_in_turn_compression_cannot_drop_current_user_snapshot():
    snapshot = render_rows(
        [(9, "user", "当前请求")], {9: TurnContext("本轮时间", True, "最新笔记").payload()}, include_personal=True
    )[0]
    system = {"role": "system", "content": "固定规则"}
    old = [{"role": "user", "content": "旧输入" * 100}, {"role": "assistant", "content": "旧回复" * 100}]
    tail = []
    for i in range(8):
        tail.extend(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": str(i), "function": {"name": "read", "arguments": "{}"}}],
                },
                {"role": "tool", "tool_call_id": str(i), "content": "工具输出" * 100},
            ]
        )
    result = compress_if_needed(
        [system, *old, snapshot, *tail],
        gateway=Capture(),
        model="fake/model",
        trigger_chars=1,
        min_messages=1,
        keep_recent=2,
    )
    assert result[0] == system and "当前请求" in result[1]["content"] and "最新笔记" in result[1]["content"]
    assert result[2:] == tail
    assert_invariants(result)


def test_in_turn_compression_reanchors_unchanged_memory_from_removed_history(fixture):
    agent, gateway, store, resident = fixture
    snapshot = TurnContext("本轮时间", True, "必须保留的完整笔记", "档案")
    rendered = render_rows(
        [(1, "user", "较早的长消息" * 100), (2, "assistant", "旧回答"), (3, "user", "当前问题")],
        {1: snapshot.payload(), 3: snapshot.payload()},
        include_personal=True,
    )
    assert "personal_memory" not in context(rendered[-1])
    agent.config.compression.enabled = True
    agent.config.compression.trigger_chars = 1
    agent.config.compression.min_messages = 1
    agent.config.compression.keep_recent = 1
    result = agent._maybe_compress([{"role": "system", "content": "固定规则"}, *rendered], current_context=snapshot)
    assert context(result[-1])["personal_memory"]["resident"] == "必须保留的完整笔记"
    assert "必须保留的完整笔记" not in gateway.calls[0][0][-1]["content"]
    assert_invariants(result)


def test_equivalent_registry_order_produces_same_specs_without_shared_mutation():
    first, second = ToolRegistry(), ToolRegistry()
    for registry, names in [(first, ["z", "a"]), (second, ["a", "z"])]:
        parameters = {"type": "object", "properties": {}} if registry is first else {"properties": {}, "type": "object"}
        for name in names:
            registry.register(Tool(name, name, parameters, lambda args: "ok"))
    assert first.specs() == second.specs()
    assert json.dumps(first.specs()) == json.dumps(second.specs())
    view = first.specs()
    view[0]["function"]["parameters"]["properties"]["injected"] = {}
    assert first.specs() == second.specs()


@pytest.mark.parametrize(
    "usage,expected",
    [
        (
            {
                "prompt_tokens": 100,
                "completion_tokens": 2,
                "prompt_cache_hit_tokens": 80,
                "prompt_cache_miss_tokens": 20,
            },
            (80, None, 20),
        ),
        (
            {"prompt_tokens": 100, "completion_tokens": 2, "prompt_tokens_details": {"cached_tokens": 70}},
            (70, None, None),
        ),
        (
            {
                "prompt_tokens": 100,
                "completion_tokens": 2,
                "cache_read_input_tokens": 60,
                "cache_creation_input_tokens": 40,
            },
            (60, 40, None),
        ),
        (
            {
                "prompt_tokens": 100,
                "completion_tokens": 2,
                "prompt_tokens_details": {"cached_tokens": 60, "cache_creation_tokens": 40},
            },
            (60, 40, None),
        ),
        ({"prompt_tokens": 100, "completion_tokens": 2}, (None, None, None)),
        (
            {
                "prompt_cache_hit_tokens": True,
                "cache_creation_input_tokens": -1,
                "prompt_cache_miss_tokens": float("nan"),
            },
            (None, None, None),
        ),
    ],
)
def test_cache_usage_is_only_taken_from_provider_reporting(usage, expected):
    normalized = Usage.from_response({"usage": usage})
    assert (normalized.cached_prompt_tokens, normalized.cache_creation_tokens, normalized.cache_miss_tokens) == expected
    assert normalized.brief()["cached_prompt_tokens"] == expected[0]


# ---------------------------------------------------------------- 人物卡注入（M6）

_PERSON_CARD = {
    "id": 7,
    "name": "小张",
    "importance": "important",
    "resident": "在准备考研",
    "archive": "喜欢骑车",
    "events": "",
}


def test_person_card_injected_for_non_owner_dm_and_deduplicated(fixture):
    agent, gateway, store, resident = fixture
    card_calls = []
    agent._person_card = lambda channel, value: card_calls.append((channel, value)) or dict(_PERSON_CARD)
    agent.chat("你好呀", personal_context=False, channel="qq", principal="qq:12345", context_scope="private")
    first = gateway.calls[-1][0]
    data = context(first[-1])
    assert data["person_memory"]["name"] == "小张" and "personal_memory" not in data
    assert card_calls == [("qq", "12345")]
    assert "群聊场景" not in first[0]["content"]  # 私聊不是群：不加群提示词
    # 同一张卡在同一保留窗口只发一次；请求前缀稳定
    agent.chat("还在吗", personal_context=False, channel="qq", principal="qq:12345", context_scope="private")
    second = gateway.calls[-1][0]
    assert second[: len(first)] == first
    assert "person_memory" not in context(second[-1])
    assert json.dumps(second, ensure_ascii=False).count("在准备考研") == 1


def test_person_card_changes_resend_full_snapshot(fixture):
    agent, gateway, store, resident = fixture
    state = {"card": dict(_PERSON_CARD)}
    agent._person_card = lambda channel, value: state["card"]
    agent.chat("第一轮", personal_context=False, channel="qq", principal="qq:12345", context_scope="private")
    state["card"] = {**_PERSON_CARD, "resident": "考研结束了，在准备面试"}
    agent.chat("第二轮", personal_context=False, channel="qq", principal="qq:12345", context_scope="private")
    second = gateway.calls[-1][0]
    assert context(second[-1])["person_memory"]["resident"] == "考研结束了，在准备面试"


def test_person_card_failure_and_unknown_account_do_not_break_turn(fixture):
    agent, gateway, store, resident = fixture

    def boom(channel, value):
        raise RuntimeError("人物库暂时打不开")

    agent._person_card = boom
    agent.chat("在吗", personal_context=False, channel="qq", principal="qq:12345", context_scope="private")
    assert "person_memory" not in context(gateway.calls[-1][0][-1])
    agent._person_card = lambda channel, value: None  # 查无此人
    agent.chat("还在吗", personal_context=False, channel="qq", principal="qq:12345", context_scope="private")
    assert "person_memory" not in context(gateway.calls[-1][0][-1])


def test_group_and_owner_never_get_person_card(fixture):
    agent, gateway, store, resident = fixture
    agent._person_card = lambda channel, value: dict(_PERSON_CARD)
    # 群聊：scope 非 private，不注入（系统提示词里提及 person_memory 协议文字不算）
    agent.chat("群里说话", personal_context=False, channel="qq", principal="qq:12345", context_scope="group:200")
    user_msg = gateway.calls[-1][0][-1]["content"]
    assert "person_memory" not in user_msg and "小张" not in user_msg
    assert "群聊场景" in gateway.calls[-1][0][0]["content"]
    # 所有者：personal=True 走既有档案/常驻通道，不双份注入人物卡
    agent.chat("主人说话", personal_context=True, channel="qq", principal="qq:12345", context_scope="private")
    assert "person_memory" not in context(gateway.calls[-1][0][-1])


def test_turn_context_payload_person_back_compat():
    legacy = {"version": 1, "observed_at": "t", "personal": False, "resident": "", "profile": ""}
    assert TurnContext.read(legacy).person is None
    with_person = {**legacy, "person": dict(_PERSON_CARD)}
    assert TurnContext.read(with_person).person["name"] == "小张"
    broken = {**legacy, "person": {"name": 7, "resident": "", "archive": "", "events": ""}}
    with pytest.raises(ValueError):
        TurnContext.read(broken)
    # 群聊渲染把人物卡连同私人资料一起剔除
    rendered = render_rows([(1, "user", "hi")], {1: with_person}, include_personal=False)
    assert "person_memory" not in rendered[0]["content"] and "小张" not in rendered[0]["content"]
