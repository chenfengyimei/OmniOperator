"""能力档位路由（D28）：模式声明、场景交集、工具规格过滤与升档状态机。

这些测试刻意不调用真实模型：路由要么走确定性规则，要么用假网关回放。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from omni.config import AppConfig
from omni.core.agent import Agent
from omni.core.modes import (
    CHAT_TOOLS,
    DM_ALLOWED_TOOLS,
    GROUP_ALLOWED_TOOLS,
    REQUEST_MODE_TOOL,
    RETRIEVAL_TOOLS,
    TurnMode,
    TurnModeState,
    can_escalate,
    denied_names,
    filter_specs,
    mode_allowed_names,
    request_mode_spec,
    scope_allowed_names,
)
from omni.core.router import ROUTER_SYSTEM_PROMPT, ModeRouter
from omni.memory.store import MemoryStore
from omni.tools.registry import Tool, ToolRegistry


def _registry(names: tuple[str, ...] = ()) -> ToolRegistry:
    registry = ToolRegistry()
    for name in names:
        registry.register(
            Tool(name, f"{name} 测试用", {"type": "object", "properties": {}}, lambda _args: "ok", toolset="test")
        )
    return registry


def _call(name: str, **arguments):
    return SimpleNamespace(
        id=f"call-{name}",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments, ensure_ascii=False)),
    )


# ---------------------------------------------------------------- 模式声明


def test_mode_allowlists_only_contain_registered_tool_names():
    """白名单里的名字必须是真实工具名（拼错会让该能力静默消失）。"""
    from omni.tools.personal import clock, digest, facts, journal, people, transcript

    known = {
        "get_time",
        "recall",
        "search_history",
        "journal_view",
        "person_lookup",
        "daily_digest",
        "dialogue_search",
        "dialogue_read",
        "session_log_search",
        "session_log_read",
        "files_list",
        "files_search",
        "files_read",
        "files_diff",
        "files_history",
        "peek_command",
        "web_read",
        "system_status",
        "bilibili_video_info",
        "bilibili_video_comments",
        "bilibili_video_subtitle",
        "bilibili_video_transcript",
    }
    assert set(CHAT_TOOLS) <= known
    assert set(RETRIEVAL_TOOLS) <= known
    assert CHAT_TOOLS < RETRIEVAL_TOOLS


def test_chat_mode_has_no_write_capability():
    """chat 档位只给只读记忆工具：不许写事实、不许改文件、不许执行命令。"""
    for name in (
        "remember",
        "forget",
        "profile_manage",
        "resident_memory",
        "todo",
        "skill_create",
        "skill_patch",
        "skill_rewrite",
        "cron_add",
        "cron_once",
        "files_write",
        "files_restore",
        "run_command",
        "browser_click",
        "android_tap_element",
        "qq_send_message",
        "delegate_task",
    ):
        assert name not in CHAT_TOOLS
        assert name not in RETRIEVAL_TOOLS


def test_plugin_and_mcp_tools_are_never_in_light_modes():
    """外部扩展不进轻量档位：避免轻量回合拉起外部服务或被插件顶名。"""
    for name in ("mcp_list_tools", "mcp_read_tool", "mcp_call_tool", "plugin_list"):
        assert name not in CHAT_TOOLS
        assert name not in RETRIEVAL_TOOLS


def test_scope_intersection_never_expands_permissions():
    """场景白名单与模式白名单求交：群聊即使升到 agent 也拿不到文件与设备工具。"""
    names = frozenset({"get_time", "web_read", "todo", "files_read", "browser_click"})
    group = scope_allowed_names(names, personal_context=False, scope="group:1")
    assert group == GROUP_ALLOWED_TOOLS & names
    # 群聊升到 agent 也只能拿到群场景允许的那几个
    assert mode_allowed_names(TurnMode.AGENT, group) == group
    assert "files_read" not in mode_allowed_names(TurnMode.AGENT, group)
    # 非所有者私聊同理
    private = scope_allowed_names(names, personal_context=False, scope="private")
    assert private == DM_ALLOWED_TOOLS & names
    # 所有者私聊才有全部
    owner = scope_allowed_names(names, personal_context=True, scope="private")
    assert owner == names


def test_denied_names_covers_everything_outside_the_intersection():
    names = frozenset({"get_time", "recall", "files_read", "run_command"})
    owner = scope_allowed_names(names, personal_context=True, scope="private")
    denied = denied_names(TurnMode.CHAT, owner, names)
    assert "run_command" in denied and "files_read" in denied
    assert "recall" not in denied and "get_time" not in denied


def test_filter_specs_keeps_order_and_only_keeps_allowed():
    specs = [
        {"type": "function", "function": {"name": "b", "description": "", "parameters": {}}},
        {"type": "function", "function": {"name": "a", "description": "", "parameters": {}}},
    ]
    kept = filter_specs(specs, frozenset({"a"}))
    assert [s["function"]["name"] for s in kept] == ["a"]


def test_escalation_only_goes_upward():
    assert can_escalate(TurnMode.CHAT, TurnMode.RETRIEVAL)
    assert can_escalate(TurnMode.CHAT, TurnMode.AGENT)
    assert can_escalate(TurnMode.RETRIEVAL, TurnMode.AGENT)
    assert not can_escalate(TurnMode.AGENT, TurnMode.CHAT)
    assert not can_escalate(TurnMode.RETRIEVAL, TurnMode.CHAT)
    assert not can_escalate(TurnMode.CHAT, TurnMode.CHAT)


def test_request_mode_spec_is_not_registered_in_the_registry():
    """升档工具是内核专用的：不在 Registry 里，因此无法被派发或被审批绕过。"""
    spec = request_mode_spec()
    assert spec["function"]["name"] == REQUEST_MODE_TOOL
    assert spec["function"]["parameters"]["properties"]["mode"]["enum"] == ["retrieval", "agent"]
    registry = _registry()
    assert registry.get(REQUEST_MODE_TOOL) is None


# ---------------------------------------------------------------- 路由判定


def test_rule_route_recognises_action_read_and_greeting():
    router = ModeRouter(AppConfig())
    assert router.rule_decision("帮我把文件删掉", requested="auto").mode is TurnMode.AGENT
    assert router.rule_decision("查一下项目里的配置在哪", requested="auto").mode is TurnMode.RETRIEVAL
    assert router.rule_decision("你好呀", requested="auto").mode is TurnMode.CHAT


def test_rule_route_returns_none_for_ambiguous_input():
    router = ModeRouter(AppConfig())
    assert router.rule_decision("关于上个月那件事你怎么看", requested="auto") is None


def test_model_reply_parsing_is_strict():
    router = ModeRouter(AppConfig())
    assert router.parse_model_reply('{"mode":"chat","reason":"只是闲聊"}') == (
        TurnMode.CHAT,
        "只是闲聊",
    )
    # 多一个键 / 不是 JSON / 非法模式 / 理由过长，一律不认
    assert router.parse_model_reply('{"mode":"chat","reason":"好","extra":1}') is None
    assert router.parse_model_reply("chat") is None
    assert router.parse_model_reply('{"mode":"auto","reason":"好"}') is None
    assert router.parse_model_reply('{"mode":"chat","reason":"' + "长" * 200 + '"}') is None


def test_side_effect_guard_cannot_be_downgraded_by_the_model():
    router = ModeRouter(AppConfig())
    assert router.guard(TurnMode.CHAT, "帮我删掉那个文件") is TurnMode.AGENT
    assert router.guard(TurnMode.RETRIEVAL, "帮我删掉那个文件") is TurnMode.AGENT
    assert router.guard(TurnMode.RETRIEVAL, "查一下配置文件") is TurnMode.RETRIEVAL


def test_router_prompt_asks_for_one_json_object_only():
    assert "JSON" in ROUTER_SYSTEM_PROMPT
    assert "chat" in ROUTER_SYSTEM_PROMPT and "retrieval" in ROUTER_SYSTEM_PROMPT


# ---------------------------------------------------------------- Agent 集成


class _RouterGateway:
    """先回放路由判定，再回放主模型回答。"""

    def __init__(self, route_reply: str | None = None) -> None:
        self.route_reply = route_reply
        self.calls: list[dict] = []
        self.seen_tools: list[list[str]] = []

    def resolve(self, _route: str) -> str:
        return "fake/model"

    def complete(self, messages, *, model, tools=None, purpose="chat", **kwargs):
        self.calls.append({"purpose": purpose, "model": model})
        self.seen_tools.append([t["function"]["name"] for t in (tools or [])])
        if purpose == "route":
            return SimpleNamespace(text=self.route_reply or "", tool_calls=[])
        return SimpleNamespace(text="已经回答。", tool_calls=[])


def _agent(tmp_path, gateway, names=(), *, mode_router=None):
    store = MemoryStore(tmp_path / "m.db")
    sid = store.new_session()
    agent = Agent(AppConfig(), gateway, store, _registry(names), sid, mode_router=mode_router)
    return agent, store, sid


def test_auto_route_uses_the_router_model_and_light_tool_set(tmp_path):
    gateway = _RouterGateway('{"mode":"retrieval","reason":"需要查资料"}')
    router = ModeRouter(AppConfig())
    agent, store, sid = _agent(
        tmp_path,
        gateway,
        names=("get_time", "recall", "files_read", "run_command"),
        mode_router=router,
    )
    try:
        agent.chat("关于上个月那件事你怎么看", mode="auto")
        # 路由确实发生了一次模型调用，且用的是独立用途
        assert [c["purpose"] for c in gateway.calls] == ["route", "chat"]
        # 主调用的工具规格只有 retrieval 档位允许的那些（外加内核升档工具）
        sent = set(gateway.seen_tools[1]) - {REQUEST_MODE_TOOL}
        assert sent <= set(RETRIEVAL_TOOLS) & {"get_time", "recall", "files_read"}
        assert "run_command" not in gateway.seen_tools[1]
    finally:
        store.close()


def test_manual_mode_skips_the_router_model_entirely(tmp_path):
    gateway = _RouterGateway()
    router = ModeRouter(AppConfig())
    agent, store, sid = _agent(tmp_path, gateway, names=("get_time", "recall"), mode_router=router)
    try:
        agent.chat("你好", mode="chat")
        assert [c["purpose"] for c in gateway.calls] == ["chat"]
        sent = set(gateway.seen_tools[0]) - {REQUEST_MODE_TOOL}
        assert sent <= set(CHAT_TOOLS)
    finally:
        store.close()


def test_legacy_agent_without_router_keeps_full_tool_set(tmp_path):
    """未注入路由的 Agent（老用法）保持完整能力，行为不变。"""
    gateway = _RouterGateway()
    agent, store, sid = _agent(tmp_path, gateway, names=("get_time", "run_command"))
    try:
        agent.chat("帮我删掉那个文件")
        assert [c["purpose"] for c in gateway.calls] == ["chat"]
        assert "run_command" in gateway.seen_tools[0]
    finally:
        store.close()


def test_router_failure_falls_back_to_a_working_capability(tmp_path):
    class Boom(_RouterGateway):
        def complete(self, messages, *, model, tools=None, purpose="chat", **kwargs):
            if purpose == "route":
                raise RuntimeError("路由模型不可用")
            return SimpleNamespace(text="已经回答。", tool_calls=[])

    router = ModeRouter(AppConfig())
    agent, store, sid = _agent(tmp_path, Boom(), names=("get_time",), mode_router=router)
    try:
        answer = agent.chat("关于上个月那件事你怎么看", mode="auto")
        assert "已经回答" in answer
    finally:
        store.close()


def test_router_output_that_is_not_json_falls_back_to_full_capability(tmp_path):
    """路由输出不合规时回退完整能力：宁可多带规格，也不让用户的事做不成。"""
    gateway = _RouterGateway("我觉得应该用完整模式")
    router = ModeRouter(AppConfig())
    agent, store, sid = _agent(tmp_path, gateway, names=("get_time", "files_read"), mode_router=router)
    try:
        agent.chat("关于上个月那件事你怎么看", mode="auto")
        # 回退到完整能力：全部已注册工具都在（完整档位不附带升档工具）
        assert set(gateway.seen_tools[1]) == {"get_time", "files_read"}
    finally:
        store.close()


def test_mode_is_recorded_in_the_turn_snapshot(tmp_path):
    gateway = _RouterGateway()
    router = ModeRouter(AppConfig())
    agent, store, sid = _agent(tmp_path, gateway, names=("get_time",), mode_router=router)
    try:
        agent.chat("你好", mode="chat")
        snapshot = store.prompt_contexts(sid, [row[0] for row in store.recent_messages_ex(sid, 10)])
        modes = [payload.get("mode") for payload in snapshot.values()]
        assert "chat" in modes
    finally:
        store.close()


def test_unknown_mode_is_rejected(tmp_path):
    gateway = _RouterGateway()
    agent, store, sid = _agent(tmp_path, gateway)
    try:
        with pytest.raises(ValueError):
            agent.chat("你好", mode="turbo")
    finally:
        store.close()


def test_group_scope_still_cannot_read_owner_files_in_retrieval(tmp_path):
    """群聊即使路由到 retrieval，也拿不到所有者的工作区文件。"""
    gateway = _RouterGateway()
    router = ModeRouter(AppConfig())
    agent, store, sid = _agent(tmp_path, gateway, names=("get_time", "web_read", "files_read"), mode_router=router)
    try:
        agent.run_turn(
            "查一下配置文件",
            personal_context=False,
            channel="qq",
            context_scope="group:200",
            mode="retrieval",
        )
        assert "files_read" not in gateway.seen_tools[0]
        sent = set(gateway.seen_tools[0]) - {REQUEST_MODE_TOOL}
        assert sent <= GROUP_ALLOWED_TOOLS
    finally:
        store.close()


def test_forged_write_tool_is_rejected_in_light_mode(tmp_path):
    """轻量档位下模型伪造写工具名，派发处照样拒绝（规格与派发双保险）。"""
    executed: list[str] = []

    class _ForgingGateway:
        def __init__(self) -> None:
            self.seen_tools: list[list[str]] = []

        def resolve(self, _route: str) -> str:
            return "fake/model"

        def complete(self, messages, *, model, tools=None, purpose="chat", **kwargs):
            self.seen_tools.append([t["function"]["name"] for t in (tools or [])])
            if len(self.seen_tools) == 1:
                return SimpleNamespace(text="", tool_calls=[_call("files_write", path="x")])
            return SimpleNamespace(text="已经处理完。", tool_calls=[])

    gateway = _ForgingGateway()
    agent, store, sid = _agent(
        tmp_path, gateway, names=("get_time", "files_write"), mode_router=ModeRouter(AppConfig())
    )
    # 把写工具注册成真能执行的（证明拒绝来自档位，而不是工具不存在）
    agent.registry.register(
        Tool(
            "files_write",
            "写文件",
            {"type": "object", "properties": {}},
            lambda _a: executed.append("wrote") or "已写入",
            toolset="test",
        )
    )
    try:
        agent.chat("帮我写点东西", mode="chat")
        assert executed == [], "chat 档位不得执行写工具"
    finally:
        store.close()


def test_cli_exposes_mode_command_and_valid_choices():
    """CLI 提供 /mode 命令，四个档位值与内核一致。"""
    from omni.channels import cli

    assert "/mode" in cli.HELP
    assert cli.MODE_CHOICES == ("auto", "chat", "retrieval", "agent")
    # 档位取值必须与内核枚举一致，避免两处漂移出未定义的档位
    assert set(cli.MODE_CHOICES) == {"auto"} | {m.value for m in TurnMode}
