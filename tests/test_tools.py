"""工具注册表单测：spec 格式 / dispatch / 审批闸门 / 错误回传。"""
import json

from omni.memory.store import MemoryStore
from omni.safety.gates import Decision, Risk
from omni.tools.builtin import register_core_tools
from omni.tools.registry import Tool, ToolRegistry


def _core_registry(store: MemoryStore) -> ToolRegistry:
    registry = ToolRegistry()
    register_core_tools(registry, store)
    return registry


def test_specs_openai_shape(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    registry = _core_registry(store)
    specs = registry.specs()
    assert all(s["type"] == "function" for s in specs)
    names = {s["function"]["name"] for s in specs}
    assert {"get_time", "remember", "recall"} <= names
    for s in specs:
        assert "description" in s["function"]
        assert "parameters" in s["function"]
    assert registry.toolsets() == ["core"]
    store.close()


def test_dispatch_with_json_string(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    registry = _core_registry(store)
    out = registry.dispatch("remember", json.dumps({"text": "用户在成都"}))
    assert "用户在成都" in out
    assert any("成都" in f for f in store.search_facts("成都"))
    assert "20" in registry.dispatch("get_time", "{}")
    assert registry.dispatch("nope", "{}").startswith("错误")
    store.close()


def test_dispatch_bad_json_and_empty_text(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    registry = _core_registry(store)
    assert "合法 JSON" in registry.dispatch("remember", "{oops")
    assert "不能为空" in registry.dispatch("remember", {"text": ""})
    store.close()


class _Allow:
    def check(self, tool, arguments=None):
        return Decision(True)


class _Deny:
    def check(self, tool, arguments=None):
        return Decision(False, "测试拒绝")


def test_dispatch_approval_gate(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    registry = _core_registry(store)
    registry.register(
        Tool(
            name="send_msg",
            description="发消息（测试用）",
            parameters={"type": "object", "properties": {}, "required": []},
            func=lambda _args: "已发送",
            toolset="test",
            risk=Risk.CONFIRM,
        )
    )
    # 拒绝：结果以字符串回传给 LLM，而不是抛异常
    denied = registry.dispatch("send_msg", "{}", approver=_Deny())
    assert "未获批准" in denied and "测试拒绝" in denied
    # 放行
    assert registry.dispatch("send_msg", "{}", approver=_Allow()) == "已发送"
    # 不给 approver：不设闸（调用方决定策略），工具直通
    assert registry.dispatch("send_msg", "{}") == "已发送"
    # 按 toolset 过滤
    assert "send_msg" not in registry.names(toolsets={"core"})
    assert "send_msg" in registry.names(toolsets={"core", "test"})
    store.close()


def test_daily_digest_dispatch(tmp_path):
    """daily_digest：空库如实说无事；有活动时给会话/记忆/档案额度摘要。"""
    from omni.tools.builtin import register_digest_tool

    store = MemoryStore(tmp_path / "m.db")
    registry = ToolRegistry()
    register_digest_tool(registry, store)
    assert registry.get("daily_digest").risk.value == "safe"

    out_empty = registry.dispatch("daily_digest", "{}")
    assert "无需整理" in out_empty

    sid = store.new_session()
    store.append_message(sid, "user", "聊聊猫粮怎么选")
    store.append_message(sid, "assistant", "好的", auto_title=False)
    store.save_fact("用户养了一只猫叫煤球")
    out = registry.dispatch("daily_digest", {"hours": 24})
    assert f"#{sid}" in out and "聊聊猫粮怎么选" in out
    assert "2 条消息" in out and "煤球" in out
    assert "1 条" in out  # 新增记忆计数

    # 非法参数以字符串回传；窗口收窄到 0 会话也如实呈现
    assert registry.dispatch("daily_digest", {"hours": "abc"}).startswith("错误")
    store.close()


# ---------- todo 内联工具（SessionRef 会话注入） ----------

def test_todo_dispatch_session_scoped(tmp_path):
    from omni.tools.builtin import register_todo_tools
    from omni.tools.registry import SessionRef

    store = MemoryStore(tmp_path / "m.db")
    sid_a = store.new_session()
    sid_b = store.new_session()
    ref = SessionRef()
    registry = ToolRegistry()
    register_todo_tools(registry, store, ref)

    # 无会话上下文 → 明确报错而非崩溃
    assert "会话上下文缺失" in registry.dispatch("todo", '{"operation": "view"}')

    ref.set(sid_a)
    assert "已加待办 #1" in registry.dispatch(
        "todo", json.dumps({"operation": "add", "content": "查字幕"})
    )
    ref.set(sid_b)
    # id 全表自增（跨会话唯一，view/操作都按全局编号）
    assert "已加待办 #2" in registry.dispatch(
        "todo", json.dumps({"operation": "add", "content": "发测试消息"})
    )
    # 会话隔离：A 看不到 B 的待办
    ref.set(sid_a)
    view_a = registry.dispatch("todo", '{"operation": "view"}')
    assert "查字幕" in view_a and "发测试消息" not in view_a
    # done / undo / delete / clear / 异常 id
    assert "已标记完成" in registry.dispatch("todo", '{"operation": "done", "id": 1}')
    assert "[x]" in registry.dispatch("todo", '{"operation": "view"}')
    assert "已撤销" in registry.dispatch("todo", '{"operation": "undo", "id": 1}')
    assert "已删除" in registry.dispatch("todo", '{"operation": "delete", "id": 1}')
    assert "不存在" in registry.dispatch("todo", '{"operation": "done", "id": 99}')
    assert "未知操作" in registry.dispatch("todo", '{"operation": "nope"}')
    assert "已清空 0 条" in registry.dispatch("todo", '{"operation": "clear"}')
    store.close()


def test_agent_chat_binds_session_ref(tmp_path):
    """Agent.chat 把当前会话写进 SessionRef：todo 落到正确的会话（含 QQ 式覆盖）。"""
    from types import SimpleNamespace

    from omni.config import AppConfig
    from omni.core.agent import Agent
    from omni.tools.builtin import register_todo_tools
    from omni.tools.registry import SessionRef

    store = MemoryStore(tmp_path / "m.db")
    sid = store.new_session()
    other = store.new_session()

    class TodoGateway:
        """每轮对话第一轮发起 todo add，第二轮给最终回答。"""

        def __init__(self):
            self.step = 0

        def resolve(self, route: str) -> str:
            return "fake/model"

        def complete(self, messages, *, model, tools=None):
            self.step += 1
            if self.step % 2 == 1:
                call = SimpleNamespace(
                    id=f"c{self.step}",
                    function=SimpleNamespace(
                        name="todo",
                        arguments=json.dumps(
                            {"operation": "add", "content": f"步骤{self.step}"}
                        ),
                    ),
                )
                return SimpleNamespace(text="", tool_calls=[call])
            return SimpleNamespace(text="清单已更新。", tool_calls=[])

    ref = SessionRef()
    registry = ToolRegistry()
    register_core_tools(registry, store)
    register_todo_tools(registry, store, ref)
    agent = Agent(AppConfig(), TodoGateway(), store, registry, sid, session_ref=ref)

    assert agent.chat("帮我拆解任务") == "清单已更新。"
    assert [t[1] for t in store.todo_list(sid)] == ["步骤1"]
    # QQ 式会话覆盖：显式 session_id 优先生效
    assert agent.chat("换会话继续", session_id=other) == "清单已更新。"
    assert [t[1] for t in store.todo_list(other)] == ["步骤3"]
    assert [t[1] for t in store.todo_list(sid)] == ["步骤1"]
    store.close()
