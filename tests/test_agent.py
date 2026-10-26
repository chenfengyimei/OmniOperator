"""Agent 循环单测：用假 Gateway 验证 工具调用 → 回填 → 最终回答 → 持久化 全链路。"""
from types import SimpleNamespace

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
