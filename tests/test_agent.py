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

    def complete(self, messages, *, model, tools=None):
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
    assert "第一句话" in contents and "第二句话" in contents
    store.close()


@pytest.mark.parametrize("max_steps", [1, 8])
def test_multiple_tool_calls_reach_normal_or_grace_answer(tmp_path, max_steps):
    from omni.tools.registry import Tool

    store = MemoryStore(tmp_path / "m.db")
    registry = ToolRegistry()
    calls = []
    registry.register(Tool("echo", "回显", {}, lambda args: calls.append(args["n"]) or str(args["n"])))

    class BatchGateway(ScriptedGateway):
        def complete(self, messages, *, model, tools=None):
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
        def complete(self, messages, *, model, tools=None):
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

        def complete(self, messages, *, model, tools=None):
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
        private_prompt = gateway.messages[0]["content"]
        assert "秘密花园" in private_prompt and "讨厌被叫全名" in private_prompt

        # 群聊：同一会话也不带私人上下文
        agent.chat("群里问一句", session_id=sid, personal_context=False)
        group_prompt = gateway.messages[0]["content"]
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
