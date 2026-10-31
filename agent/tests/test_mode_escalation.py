"""同回合一次升档（D28）：request_mode 的状态机、权限边界与升档提示。

核心不变量：
- 升档只能向上、每回合最多一次；
- 升档只切换"注入哪一档工具规格"，**不扩大任何权限**：
  群聊升到 agent 也拿不到文件与设备工具；
- 升档后的写操作照旧走审批链，没人能借升档绕过人工确认；
- request_mode 是内核专用工具，不在 Registry 里，因此无法被派发或审批绕过；
- 档位不足时提示升档（提示协议在系统提示与拒绝文案里），场景不许可保持原文案。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from omni.config import AppConfig
from omni.core.agent import Agent
from omni.core.modes import REQUEST_MODE_TOOL, TurnMode
from omni.core.router import ModeRouter
from omni.memory.store import MemoryStore
from omni.safety.gates import DenyApprover, Risk
from omni.tools.registry import Tool, ToolRegistry
from omni.tools.result import ToolResult


def _call(name: str, **arguments):
    return SimpleNamespace(
        id=f"call-{name}",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments, ensure_ascii=False)),
    )


class _Script:
    """脚本化假网关：按脚本回放工具调用或文本。"""

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.seen_tools: list[list[str]] = []
        self.seen_messages: list[list[dict]] = []
        self.calls = 0

    def resolve(self, _route: str) -> str:
        return "fake/model"

    def complete(self, messages, *, model, tools=None, purpose="chat", **kwargs):
        self.calls += 1
        self.seen_tools.append([t["function"]["name"] for t in (tools or [])])
        # 拷贝快照：调用返回后 agent 还会往同一个列表里追加 tool 消息
        self.seen_messages.append([dict(m) for m in messages])
        step = self.script.pop(0) if self.script else "已完成。"
        if isinstance(step, str):
            return SimpleNamespace(text=step, tool_calls=[])
        return SimpleNamespace(text="", tool_calls=step)


def _tool_contents(script: _Script, call_index: int) -> str:
    """第 call_index 次模型调用看到的全部 tool 消息（升档/拒绝文案都在里面）。"""
    return "\n".join(
        str(m.get("content", ""))
        for m in script.seen_messages[call_index]
        if m.get("role") == "tool"
    )


def _agent(tmp_path, gateway, names=(), *, approver=None, router=True, mode="chat"):
    store = MemoryStore(tmp_path / "m.db")
    sid = store.new_session()
    registry = ToolRegistry()
    for name in names:
        registry.register(
            Tool(name, f"{name} 测试用", {"type": "object", "properties": {}},
                 lambda _args: f"{name} 已执行", toolset="test")
        )
    agent = Agent(
        AppConfig(), gateway, store, registry, sid,
        approver=approver,
        mode_router=ModeRouter(AppConfig()) if router else None,
    )
    return agent, store, sid


def test_escalation_switches_tool_set_for_the_next_model_call(tmp_path):
    """chat → agent 升档后，下一次模型调用拿到完整工具集。"""
    gateway = _Script([
        [_call(REQUEST_MODE_TOOL, mode="agent", reason="需要写文件")],
        "已经处理完。",
    ])
    agent, store, sid = _agent(
        tmp_path, gateway, names=("get_time", "files_write", "run_command")
    )
    try:
        agent.chat("帮我处理这件事", mode="chat")
        assert len(gateway.seen_tools) == 2
        first = set(gateway.seen_tools[0]) - {REQUEST_MODE_TOOL}
        assert first <= {"get_time"}          # chat 档位：只有只读记忆工具
        assert "files_write" not in first
        second = set(gateway.seen_tools[1])
        assert "files_write" in second        # 升档后拿到完整工具集
        assert "run_command" in second
        assert REQUEST_MODE_TOOL not in second  # 完整档位不再给升档工具
    finally:
        store.close()


def test_escalation_is_allowed_at_most_once_per_turn(tmp_path):
    """第二次升档被拒绝：模型不能反复试探。

    第一次 chat→retrieval 已用掉升档额度；第二次请求 agent 被拒，
    档位停在 retrieval——因此第三轮仍是 retrieval 的工具集，不会出现 agent 的工具。
    """
    gateway = _Script([
        [_call(REQUEST_MODE_TOOL, mode="retrieval", reason="要查资料")],
        [_call(REQUEST_MODE_TOOL, mode="agent", reason="再要更多")],
        "结束。",
    ])
    agent, store, sid = _agent(tmp_path, gateway, names=("get_time", "files_read"))
    try:
        agent.chat("看看这个", mode="chat")
        results = [set(names) for names in gateway.seen_tools]
        assert len(results) == 3
        # 第一轮：chat 档位（只读记忆工具 + 升档工具）
        assert "files_read" not in results[0]
        # 第二轮：升档到 retrieval，出现 retrieval 的只读工具
        assert "files_read" in results[1]
        # 第三轮：第二次升档被拒，档位仍是 retrieval（没有 agent 专属工具）
        assert "files_read" in results[2]
        assert "run_command" not in results[2]
    finally:
        store.close()


def test_escalation_cannot_go_downward(tmp_path):
    gateway = _Script([
        [_call(REQUEST_MODE_TOOL, mode="chat", reason="想退回聊天")],
        "结束。",
    ])
    agent, store, sid = _agent(tmp_path, gateway, names=("get_time", "files_read"))
    try:
        agent.chat("看看这个", mode="retrieval")
        # 降档被拒：档位仍是 retrieval（files_read 仍在，agent 专属工具没出现）
        after = set(gateway.seen_tools[1])
        assert "files_read" in after
        assert "run_command" not in after
    finally:
        store.close()


def test_escalation_target_must_be_a_known_mode(tmp_path):
    gateway = _Script([
        [_call(REQUEST_MODE_TOOL, mode="turbo", reason="瞎写的")],
        "结束。",
    ])
    agent, store, sid = _agent(tmp_path, gateway, names=("get_time", "files_read"))
    try:
        agent.chat("看看这个", mode="chat")
        # 非法目标被拒：档位不变
        assert "files_read" not in set(gateway.seen_tools[1])
    finally:
        store.close()


def test_escalation_can_be_disabled_by_config(tmp_path):
    config = AppConfig()
    config.mode_routing.allow_escalation = False
    gateway = _Script([
        [_call(REQUEST_MODE_TOOL, mode="agent", reason="要升档")],
        "结束。",
    ])
    store = MemoryStore(tmp_path / "m.db")
    try:
        sid = store.new_session()
        registry = ToolRegistry()
        for name in ("get_time", "files_read"):
            registry.register(Tool(name, name, {"type": "object", "properties": {}},
                                   lambda _a: "ok", toolset="test"))
        agent = Agent(config, gateway, store, registry, sid,
                      mode_router=ModeRouter(config))
        agent.chat("看看这个", mode="chat")
        # 关闭升档时连升档工具都不发给模型
        assert REQUEST_MODE_TOOL not in set(gateway.seen_tools[0])
        assert "files_read" not in set(gateway.seen_tools[1])
    finally:
        store.close()


def test_group_scope_cannot_escalate_into_owner_capabilities(tmp_path):
    """群聊升到 agent 也拿不到工作区文件：档位只缩小，作用域才是上限。"""
    gateway = _Script([
        [_call(REQUEST_MODE_TOOL, mode="agent", reason="要读文件")],
        "结束。",
    ])
    agent, store, sid = _agent(
        tmp_path, gateway, names=("get_time", "web_read", "files_read", "browser_click")
    )
    try:
        agent.run_turn(
            "把群里的东西整理一下", personal_context=False, channel="qq",
            context_scope="group:200", mode="chat",
        )
        # 升档后的工具集仍受群聊作用域限制
        after = set(gateway.seen_tools[1])
        assert "files_read" not in after
        assert "browser_click" not in after
        assert after <= {"get_time", "web_read"}
    finally:
        store.close()


def test_escalation_does_not_bypass_the_approver(tmp_path):
    """升档后写操作照旧要人工确认：升档不是授权。"""
    calls: list[str] = []
    registry_tool = Tool(
        "files_write", "写文件", {"type": "object", "properties": {}},
        lambda _a: calls.append("wrote") or "已写入", toolset="test", risk=Risk.CONFIRM,
    )
    gateway = _Script([
        [_call(REQUEST_MODE_TOOL, mode="agent", reason="要写文件")],
        [_call("files_write")],
        "结束。",
    ])
    store = MemoryStore(tmp_path / "m.db")
    try:
        sid = store.new_session()
        registry = ToolRegistry()
        registry.register(registry_tool)
        agent = Agent(
            AppConfig(), gateway, store, registry, sid,
            approver=DenyApprover(), mode_router=ModeRouter(AppConfig()),
        )
        agent.chat("帮我写点东西", mode="chat")
        # 无人值守审批器拒绝了写入：升档没有绕过审批链
        assert calls == []
    finally:
        store.close()


def test_request_mode_is_not_dispatchable_through_the_registry(tmp_path):
    """升档工具不在 Registry：无法被外部派发，也无法被审批绕过。"""
    store = MemoryStore(tmp_path / "m.db")
    try:
        registry = ToolRegistry()
        result = registry.execute(REQUEST_MODE_TOOL, {"mode": "agent"})
        assert result.status == "error"
        assert result.error_code == "tool_not_found"
    finally:
        store.close()


def test_mode_upgrade_event_is_recorded(tmp_path):
    """升档要留下事件：界面与排查都能看到这一回合换了档位。"""
    class _Recording:
        def __init__(self) -> None:
            self.events: list[tuple[str, dict]] = []

        def append_event(self, run_id, kind, data=None):
            self.events.append((kind, data or {}))
            return len(self.events)

        def create(self, **_kwargs):
            return None

        def touch(self, *_a, **_k):
            return True

        def finish(self, *_a, **_k):
            return True

        def get(self, *_a):
            return None

    gateway = _Script([
        [_call(REQUEST_MODE_TOOL, mode="agent", reason="要完整能力")],
        "结束。",
    ])
    store = MemoryStore(tmp_path / "m.db")
    try:
        sid = store.new_session()
        recorder = _Recording()
        agent = Agent(AppConfig(), gateway, store, ToolRegistry(), sid,
                      runs=recorder, mode_router=ModeRouter(AppConfig()))
        agent.chat("做点事", mode="chat")
        kinds = [kind for kind, _ in recorder.events]
        assert "mode_selected" in kinds
        assert "mode_upgraded" in kinds
        upgrade = next(data for kind, data in recorder.events if kind == "mode_upgraded")
        assert upgrade["selected"] == "agent"
        assert upgrade["previous"] == "chat"
    finally:
        store.close()


def test_escalation_keeps_message_invariants(tmp_path):
    """升档多出来的工具消息必须与 tool_call 严格配对。"""
    gateway = _Script([
        [_call(REQUEST_MODE_TOOL, mode="agent", reason="要升档")],
        "结束。",
    ])
    agent, store, sid = _agent(tmp_path, gateway, names=("get_time",))
    try:
        agent.chat("做点事", mode="chat")
        from omni.core.compress import assert_invariants
        # 重建含 system 的消息序列并校验不变量
        rows = store.recent_messages_ex(sid, 20)
        assert rows
        rebuilt = [{"role": "system", "content": "固定规则"}]
        for _id, role, content in rows:
            rebuilt.append({"role": role, "content": content})
        assert_invariants(rebuilt)
    finally:
        store.close()


def test_delegate_child_run_is_always_full_agent(tmp_path):
    """委派子任务固定完整档位：不再二次路由，也不降档。"""
    from omni.tools.delegation.tools import register_delegate_tools

    modes: list[str | None] = []

    class _DelegatingGateway:
        def resolve(self, _route: str) -> str:
            return "fake/model"

        def complete(self, messages, *, model, tools=None, purpose="chat", **kwargs):
            if purpose == "route":
                modes.append("route")
                return SimpleNamespace(text='{"mode":"chat","reason":"闲聊"}', tool_calls=[])
            text = messages[-1]["content"] if messages else ""
            if "主任务" in text:
                return SimpleNamespace(text="", tool_calls=[
                    _call("delegate_task", prompt="子任务", label="research")
                ])
            modes.append("child")
            return SimpleNamespace(text="子任务完成", tool_calls=[])

    store = MemoryStore(tmp_path / "m.db")
    try:
        sid = store.new_session()
        registry = ToolRegistry()
        holder: dict = {}
        register_delegate_tools(registry, store, holder)
        gateway = _DelegatingGateway()
        agent = Agent(
            AppConfig(), gateway, store, registry, sid,
            mode_router=ModeRouter(AppConfig()),
        )
        holder["agent"] = agent
        answer = agent.chat("主任务", mode="chat")
        assert "子任务完成" in answer
        # 子任务没有再触发路由（父回合也没有：显式 chat 不路由）
        assert "route" not in modes
        assert "child" in modes
    finally:
        store.close()


# ---------------------------------------------------------------- 升档提示（提示词与拒绝文案）


def test_system_prompt_carries_mode_escalation_protocol():
    """升档协议写在系统提示里：档位含义 + 能力不足先升档，别直接说做不到。"""
    from omni.core.context import system_prompt

    for text in (system_prompt("人设固定。", include_personal=True),
                 system_prompt("人设固定。", include_personal=False)):
        assert "能力档位协议" in text
        assert "request_mode" in text
        for name in ("chat", "retrieval", "agent"):
            assert name in text
        assert "拿不准目标就选 agent" in text
        assert "不要在能力不足时直接说做不到" in text
        # 升档不扩大场景许可：群聊同样看到这条边界
        assert "升档不改变场景许可" in text


def test_mode_denied_tool_hints_escalation(tmp_path):
    """档位白名单拒绝（工具在场景许可内）：拒绝文案提示先升档。"""
    gateway = _Script([
        [_call("web_read", url="https://example.com")],  # chat 档没有 web_read
        "已经读到了。",
    ])
    agent, store, sid = _agent(tmp_path, gateway, names=("get_time", "web_read"))
    try:
        agent.chat("看看这个网页", mode="chat")
        content = _tool_contents(gateway, 1)
        assert "当前回合未提供工具 web_read" in content
        assert "request_mode" in content
        assert "升档" in content
        # 场景许可内的工具才提示升档：这里没有触发"受限作用域"那套文案
        assert "受限作用域" not in content
    finally:
        store.close()


def test_scope_denied_tool_keeps_default_text(tmp_path):
    """场景不许可（群聊拿所有者资料）：保持原文案，不误导去升档。"""
    gateway = _Script([
        [_call("person_lookup", query="某人")],  # 档位允许但群聊作用域不允许
        "结束。",
    ])
    agent, store, sid = _agent(
        tmp_path, gateway, names=("get_time", "person_lookup")
    )
    try:
        agent.run_turn(
            "查一下这个人", personal_context=False, channel="qq",
            context_scope="group:200", mode="retrieval",
        )
        content = _tool_contents(gateway, 1)
        assert "当前场景不提供工具 person_lookup" in content
        assert "请让所有者本人来操作" in content
        assert "request_mode" not in content
    finally:
        store.close()


def test_agent_mode_forged_request_mode_gets_friendly_reply(tmp_path):
    """完整档位不发升档工具；模型凭系统提示伪造调用时给友好说明而非报错腔。"""
    gateway = _Script([
        [_call(REQUEST_MODE_TOOL, mode="agent", reason="试试看")],
        "结束。",
    ])
    agent, store, sid = _agent(tmp_path, gateway, names=("get_time",))
    try:
        agent.chat("做点事", mode="agent")
        content = _tool_contents(gateway, 1)
        assert "已是完整能力档位" in content
        assert "无需升档" in content
        # 完整档位的工具规格里本来就没有升档工具，档位也没有变化
        assert REQUEST_MODE_TOOL not in gateway.seen_tools[0]
        assert REQUEST_MODE_TOOL not in gateway.seen_tools[1]
    finally:
        store.close()


def test_retrieval_escalates_to_agent_and_finishes(tmp_path):
    """端到端：retrieval 档工具不足 → 拒绝文案提示升档 → request_mode → 完成。"""
    ran: list[str] = []

    def run_command(args):
        ran.append(str(args.get("command", "")))
        return "命令输出"

    gateway = _Script([
        [_call("run_command", command="dir")],   # retrieval 档没有 run_command
        [_call(REQUEST_MODE_TOOL, mode="agent", reason="需要执行命令")],
        [_call("run_command", command="dir")],
        "已经完成。",
    ])
    store = MemoryStore(tmp_path / "m.db")
    try:
        sid = store.new_session()
        registry = ToolRegistry()
        registry.register(
            Tool("get_time", "时间", {"type": "object", "properties": {}},
                 lambda _a: "12:00", toolset="test")
        )
        registry.register(
            Tool("run_command", "跑命令", {"type": "object", "properties": {}},
                 run_command, toolset="test")
        )
        agent = Agent(AppConfig(), gateway, store, registry, sid,
                      mode_router=ModeRouter(AppConfig()))
        answer = agent.chat("帮我跑个命令", mode="retrieval")
        assert answer == "已经完成。"
        # 第一次被拒（带升档提示），升档后同一工具真实执行且只执行了一次
        assert ran == ["dir"]
        denied_content = _tool_contents(gateway, 1)
        assert "升档" in denied_content
        upgraded_content = _tool_contents(gateway, 2)
        assert "已升档到 agent" in upgraded_content
        # 工具规格随升档变化：前两轮没有 run_command，第三轮出现
        assert "run_command" not in gateway.seen_tools[0]
        assert "run_command" not in gateway.seen_tools[1]
        assert "run_command" in gateway.seen_tools[2]
        assert REQUEST_MODE_TOOL not in gateway.seen_tools[2]
    finally:
        store.close()


def test_denied_note_callback_failure_falls_back_to_default_text():
    """拒绝说明回调抛异常：回退默认文案，拒绝语义不受影响。"""
    registry = ToolRegistry()
    registry.register(
        Tool("secret_read", "私人读", {"type": "object", "properties": {}},
             lambda _a: "秘密", toolset="test")
    )
    result = registry.execute(
        "secret_read", {},
        denied_tools=frozenset({"secret_read"}),
        denied_note=lambda _name: 1 / 0,
    )
    assert result.status == "error"
    assert result.error_code == "tool_not_available"
    assert "当前场景不提供工具 secret_read" in result.text
