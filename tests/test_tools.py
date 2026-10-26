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
    def check(self, tool):
        return Decision(True)


class _Deny:
    def check(self, tool):
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
