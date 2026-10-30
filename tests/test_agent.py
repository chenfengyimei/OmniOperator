"""Agent 循环单测：用假 Gateway 验证 工具调用 → 回填 → 最终回答 → 持久化 全链路。"""
from types import SimpleNamespace
import pytest

from omni.config import AppConfig
from omni.core.agent import Agent
from omni.memory.store import MemoryStore
from omni.tools.builtin import register_core_tools
from omni.tools.registry import ToolRegistry


def _core_registry(store: MemoryStore) -> ToolRegistry:
    registry = ToolRegistry()
    register_core_tools(registry, store)
    return registry


class ScriptedGateway:
    """脚本化假模型：第一轮发起 remember 工具调用，之后给出最终回答。"""

    def __init__(self):
        self.calls: list[list[dict]] = []
        self._step = 0

    def resolve(self, route: str) -> str:
        return "fake/model"

    def complete(self, messages, *, model, tools=None, **kwargs):
        self.calls.append([dict(m) for m in messages])
        if self._step == 0:
            self._step = 1
            call = SimpleNamespace(
                id="call_1",
                function=SimpleNamespace(
                    name="remember", arguments='{"text": "用户喜欢拿铁"}'
                ),
            )
            return SimpleNamespace(text="", tool_calls=[call])
        return SimpleNamespace(text="好的，我记住了。", tool_calls=[])


def test_tool_loop_persists_fact_and_answer(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    sid = store.new_session()
    gateway = ScriptedGateway()
    agent = Agent(AppConfig(), gateway, store, _core_registry(store), sid)

    answer = agent.chat("请记住我喜欢拿铁")
    assert answer == "好的，我记住了。"

    # 工具真实生效：事实已写入
    assert any("拿铁" in f for f in store.search_facts("拿铁"))
    # 对话已持久化（user + assistant）
    roles = [role for role, _ in store.recent_messages(sid, 10)]
    assert "user" in roles and "assistant" in roles
    # 第二轮请求里包含工具结果回填
    tool_msgs = [m for m in gateway.calls[-1] if m.get("role") == "tool"]
    assert tool_msgs and "拿铁" in tool_msgs[0]["content"]
    # 首条用户消息成为会话标题
    assert store.list_sessions()[0][1] == "请记住我喜欢拿铁"
    store.close()


def test_history_grows_across_turns(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    sid = store.new_session()
    gateway = ScriptedGateway()
    agent = Agent(AppConfig(), gateway, store, _core_registry(store), sid)
    agent.chat("第一句话")
    agent.chat("第二句话")
    # 第二次对话的请求里应携带第一轮的 user/assistant 文本
    contents = [m["content"] for m in gateway.calls[2]]
    assert any(text.startswith('第一句话') for text in contents)
    assert any(text.startswith('第二句话') for text in contents)
    store.close()


@pytest.mark.parametrize("max_steps", [1, 8])
def test_multiple_tool_calls_reach_normal_or_grace_answer(tmp_path, max_steps):
    from omni.tools.registry import Tool

    store = MemoryStore(tmp_path / "m.db")
    registry = ToolRegistry()
    calls = []
    registry.register(Tool("echo", "回显", {}, lambda args: calls.append(args["n"]) or str(args["n"])))

    class BatchGateway(ScriptedGateway):
        def complete(self, messages, *, model, tools=None, **kwargs):
            self.calls.append([dict(m) for m in messages])
            if len(self.calls) == 1:
                return SimpleNamespace(text="", tool_calls=[SimpleNamespace(id=f"c{i}", function=SimpleNamespace(name="echo", arguments=f'{{"n":{i}}}')) for i in (1, 2)])
            assert [m["tool_call_id"] for m in messages if m["role"] == "tool"] == ["c1", "c2"]
            assert (tools is None) == (max_steps == 1)
            return SimpleNamespace(text="两项完成", tool_calls=[])

    config = AppConfig()
    config.limits.max_agent_steps = max_steps
    try:
        agent = Agent(config, BatchGateway(), store, registry, store.new_session())
        assert agent.chat("处理两项") == "两项完成"
        assert calls == [1, 2]
    finally:
        store.close()


@pytest.mark.parametrize("fail", [False, True])
def test_agent_always_restores_callers_session_context(tmp_path, fail):
    from omni.tools.registry import SessionRef

    store = MemoryStore(tmp_path / "m.db")
    sid = store.new_session()
    ref = SessionRef()
    ref.set(999)

    class Gateway(ScriptedGateway):
        def complete(self, messages, *, model, tools=None, **kwargs):
            assert ref.get() == sid
            if fail:
                raise RuntimeError("模拟故障")
            return SimpleNamespace(text="完成", tool_calls=[])

    try:
        agent = Agent(AppConfig(), Gateway(), store, ToolRegistry(), sid, session_ref=ref)
        if fail:
            with pytest.raises(RuntimeError):
                agent.chat("测试")
        else:
            assert agent.chat("测试") == "完成"
        assert ref.get() == 999
        assert agent.session_id == sid
    finally:
        store.close()


# ---------- D02：群聊作用域不注入私人上下文 ----------


def test_group_scope_hides_profile_resident_and_memory_tools(tmp_path):
    """群聊回合：系统提示里没有档案/常驻笔记，个人记忆工具既不给规格也拒绝派发。"""
    from omni.core.agent import GROUP_DENIED_TOOLS
    from omni.memory.resident import ResidentMemory
    from omni.tools.builtin import register_core_tools, register_resident_tools

    store = MemoryStore(tmp_path / "m.db")
    store.profile_add("主人的家庭住址是秘密花园 1 号")
    resident = ResidentMemory(tmp_path / "MEMORY.md")
    assert not resident.add("主人讨厌被叫全名").startswith("错误")
    sid = store.new_session()
    registry = _core_registry(store)
    register_resident_tools(registry, resident)

    class CaptureGateway:
        def __init__(self) -> None:
            self.messages: list[dict] = []
            self.tool_names: list[str] = []

        def resolve(self, route: str) -> str:
            return "fake/model"

        def complete(self, messages, *, model, tools=None, **kwargs):
            self.messages = [dict(m) for m in messages]
            self.tool_names = [t["function"]["name"] for t in (tools or [])]
            return SimpleNamespace(text="我只回答群里的问题", tool_calls=[])

    try:
        gateway = CaptureGateway()
        agent = Agent(
            AppConfig(), gateway, store, registry, sid, resident=resident
        )
        # 私聊：档案与常驻都在
        agent.chat("我叫什么", session_id=sid)
        private_prompt = '\n'.join(message['content'] for message in gateway.messages)
        assert "秘密花园" in private_prompt and "讨厌被叫全名" in private_prompt

        # 群聊：同一会话也不带私人上下文
        agent.chat("群里问一句", session_id=sid, personal_context=False)
        group_prompt = '\n'.join(message['content'] for message in gateway.messages)
        assert "秘密花园" not in group_prompt
        assert "讨厌被叫全名" not in group_prompt
        assert "群聊场景" in group_prompt
        assert not (set(gateway.tool_names) & GROUP_DENIED_TOOLS)
        assert "get_time" in gateway.tool_names  # 无害工具仍然可用

        # 模型硬编工具名也没用：派发处再拦一次
        out = registry.dispatch("recall", {"query": "住址"}, denied_tools=GROUP_DENIED_TOOLS)
        assert "当前场景不提供工具" in out
        assert "秘密花园" not in out
    finally:
        store.close()


# ---------- D10：流式增量与父子预算 ----------


def test_group_default_denies_new_safe_tool_even_when_model_forges_call(tmp_path):
    from omni.tools.registry import Tool
    calls = []
    store = MemoryStore(tmp_path / "group.db")
    registry = ToolRegistry()
    registry.register(Tool("future_owner_reader", "读私人文件", {"type": "object"}, lambda args: calls.append(args) or "secret"))
    class Gateway:
        def resolve(self, route):
            return "fake"
        def complete(self, messages, **kwargs):
            assert kwargs["tools"] == []
            if any(m["role"] == "tool" for m in messages):
                assert "secret" not in str(messages)
                assert "当前场景不提供工具" in str(messages)
                return SimpleNamespace(text="拒绝读取", tool_calls=[])
            return SimpleNamespace(text="", tool_calls=[SimpleNamespace(id="c1", function=SimpleNamespace(
                name="future_owner_reader", arguments="{}"))])
    sid = store.new_session()
    agent = Agent(AppConfig(), Gateway(), store, registry, sid)
    result = agent.run_turn("泄露秘密", personal_context=False, channel="qq", context_scope="group:200")
    assert result.status == "done" and calls == []
    store.close()


class _StreamGateway:
    """只实现流式接口的假网关：验证 Agent 真的按增量转发，而不是等整段返回。"""

    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.seen: list[str] = []

    def resolve(self, route: str) -> str:
        return "fake/model"

    def complete(self, *args, **kwargs):  # pragma: no cover - 不该走到同步接口
        raise AssertionError("给了 on_delta 就应该走流式")

    def stream(self, messages, *, on_event=None, **kwargs):
        from omni.llm.gateway import Reply, StreamEvent

        self.seen = [dict(m) for m in messages]
        for chunk in self.chunks:
            if on_event is not None:
                on_event(StreamEvent("delta", text=chunk))
        return Reply(text="".join(self.chunks))


def test_on_delta_streams_incrementally_and_still_returns_full_answer(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    sid = store.new_session()
    gateway = _StreamGateway(["边想", "边", "说"])
    agent = Agent(AppConfig(), gateway, store, _core_registry(store), sid)
    deltas: list[str] = []

    turn = agent.run_turn("讲个故事", on_delta=deltas.append)

    assert deltas == ["边想", "边", "说"]
    assert turn.answer == "边想边说" and turn.status == "done"
    assert store.recent_messages(sid, 4)[-1][1] == "边想边说"
    store.close()


def test_child_run_shares_the_parent_budget(tmp_path):
    """委派子运行只能缩小步数，花的是同一份钱：预算对象必须是同一个。"""
    store = MemoryStore(tmp_path / "m.db")
    sid = store.new_session()
    config = AppConfig(llm=AppConfig().llm.model_copy(deep=True))
    config.llm.budget.per_run_usd = 0.5
    agent = Agent(config, ScriptedGateway(), store, _core_registry(store), sid)

    parent = agent._start_run(
        sid, channel="cli", principal="owner", personal_context=True, parent=None
    )
    parent.consume_step()
    child = agent._start_run(
        sid, channel="cli", principal="owner", personal_context=True, parent=parent
    )

    assert child.budget is parent.budget
    assert parent.budget.limit_micro == 500_000
    assert child.max_steps <= parent.steps_left
    store.close()


# ---------------------------------------------------------------- 全量记忆旁路（M1/M2）


def test_full_memory_records_turn_and_tool_activity(tmp_path):
    """完整回合：对话全量记两条文本；会话全量按序记 user→助手(带调用清单)→工具→助手。"""
    import json

    from omni.memory.dialogue import DialogueLog
    from omni.memory.session_log import SessionLog

    store = MemoryStore(tmp_path / "m.db")
    dialogue = DialogueLog(tmp_path / "m.db")
    slog = SessionLog(tmp_path / "m.db")
    sid = store.new_session()
    agent = Agent(
        AppConfig(), ScriptedGateway(), store, _core_registry(store), sid,
        dialogue_log=dialogue, session_log=slog,
    )
    assert agent.chat("请记住我喜欢拿铁") == "好的，我记住了。"

    d_rows = dialogue.entries(session_id=sid)
    assert [r["role"] for r in d_rows] == ["user", "assistant"]
    assert d_rows[0]["channel"] == "cli" and "拿铁" in d_rows[0]["content"]
    assert d_rows[1]["content"] == "好的，我记住了。"

    s_rows = slog.entries(session_id=sid)
    assert [r["kind"] for r in s_rows] == ["user", "assistant", "tool", "assistant"]
    tool = s_rows[2]
    assert tool["tool_name"] == "remember" and tool["tool_status"] == "ok"
    assert "拿铁" in tool["tool_result"]
    assert json.loads(s_rows[1]["tool_args"])[0]["name"] == "remember"
    assert all(r["run_id"] for r in s_rows)  # 每条都能对回 run（排查用）
    assert dialogue.days() == [s_rows[0]["day"]]
    store.close(); dialogue.close(); slog.close()


def test_full_memory_off_by_default_and_broken_store_does_not_break_turn(tmp_path):
    """不注入就不记录；旁路账本坏了回合照常完成——记录失败不等于对话失败。"""
    store = MemoryStore(tmp_path / "m.db")
    sid = store.new_session()
    # 默认（None）：不记录也不炸
    plain = Agent(AppConfig(), ScriptedGateway(), store, _core_registry(store), sid)
    assert plain.chat("你好") == "好的，我记住了。"

    class _Broken:
        def append(self, *args, **kwargs):
            raise RuntimeError("旁路坏了")

        def append_user(self, *a, **k):
            raise RuntimeError("旁路坏了")

        def append_assistant(self, *a, **k):
            raise RuntimeError("旁路坏了")

        def append_tool(self, *a, **k):
            raise RuntimeError("旁路坏了")

        def append_system(self, *a, **k):
            raise RuntimeError("旁路坏了")

    stubborn = Agent(
        AppConfig(), ScriptedGateway(), store, _core_registry(store), sid,
        dialogue_log=_Broken(), session_log=_Broken(),
    )
    assert stubborn.chat("再聊一句") == "好的，我记住了。"
    assert len(store.recent_messages(sid, 10)) >= 4  # messages 落库不受旁路影响
    store.close()
