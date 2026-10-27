"""审批闸门单测：三种上下文策略的风险分级行为。"""
from omni.safety.gates import (
    AllowAllApprover,
    ConsoleApprover,
    DenyApprover,
    Risk,
)
from omni.tools.registry import Tool


def _tool(risk: Risk) -> Tool:
    return Tool(
        name="demo",
        description="演示工具",
        parameters={"type": "object", "properties": {}, "required": []},
        func=lambda _args: "ok",
        risk=risk,
    )


def test_allow_all_approver():
    approver = AllowAllApprover()
    assert approver.check(_tool(Risk.SAFE)).allowed
    assert approver.check(_tool(Risk.DANGEROUS)).allowed


def test_deny_approver_fail_closed():
    approver = DenyApprover()
    assert approver.check(_tool(Risk.SAFE)).allowed
    for risk in (Risk.CONFIRM, Risk.DANGEROUS):
        decision = approver.check(_tool(risk))
        assert not decision.allowed
        assert decision.reason


class FakeConsole:
    def __init__(self, answer: str) -> None:
        self.answer = answer

    def input(self, prompt: str = "") -> str:
        return self.answer


def test_console_approver_confirm_asks_user():
    assert ConsoleApprover(FakeConsole("y")).check(_tool(Risk.CONFIRM)).allowed
    assert ConsoleApprover(FakeConsole("yes")).check(_tool(Risk.CONFIRM)).allowed
    decision = ConsoleApprover(FakeConsole("n")).check(_tool(Risk.CONFIRM))
    assert not decision.allowed and decision.reason


def test_console_approver_safe_and_dangerous():
    approver = ConsoleApprover(FakeConsole("y"))
    assert approver.check(_tool(Risk.SAFE)).allowed
    # dangerous 在任何交互上下文也拒绝（P3 引入显式授权机制前）
    assert not approver.check(_tool(Risk.DANGEROUS)).allowed


# ---------- smart 审批档（辅助模型预筛） ----------

from types import SimpleNamespace  # noqa: E402

from omni.safety.smart import SMART_SYSTEM_PROMPT, SmartApprover  # noqa: E402


def _ask_factory(text: str = "ESCALATE", fail: bool = False):
    """裁决函数假件：返回预设一词裁决，可注入失败；记录调用供断言。"""
    calls: list[list[str]] = []

    def ask(system: str, user: str) -> str:
        if fail:
            raise RuntimeError("裁决模型超时")
        calls.append([system, user])
        return text

    ask.calls = calls  # type: ignore[attr-defined]
    return ask


def _confirm_tool() -> Tool:
    return _tool(Risk.CONFIRM)


def test_smart_allows_obvious_safe_operation():
    ask = _ask_factory("ALLOW")
    approver = SmartApprover(base=DenyApprover(), ask_model=ask)
    decision = approver.check(_confirm_tool(), {"command": "uv run pytest"})
    assert decision.allowed and "smart" in decision.reason
    # 裁决 prompt：参数包在 tool-call 定界符里 + 系统提示词声明不可信
    assert "<tool-call>" in ask.calls[0][1]
    assert "uv run pytest" in ask.calls[0][1]
    assert "不可信输入" in SMART_SYSTEM_PROMPT


def test_smart_denies_dangerous_operation():
    approver = SmartApprover(base=DenyApprover(), ask_model=_ask_factory("DENY"))
    decision = approver.check(_confirm_tool(), {"command": "del C:\\Users"})
    assert not decision.allowed and "smart" in decision.reason


def test_smart_escalates_to_base_approver():
    approver = SmartApprover(base=DenyApprover(), ask_model=_ask_factory("ESCALATE"))
    decision = approver.check(_confirm_tool(), {"command": "????"})
    assert not decision.allowed and "无人值守" in decision.reason  # base 的裁决


def test_smart_failure_falls_back_to_base():
    approver = SmartApprover(base=DenyApprover(), ask_model=_ask_factory(fail=True))
    decision = approver.check(_confirm_tool(), {"command": "x"})
    assert not decision.allowed  # 回退 base（fail-safe，绝不自动放行）
    # 不可解析输出同样回退
    decision2 = SmartApprover(DenyApprover(), _ask_factory("我觉得可以吧")).check(
        _confirm_tool(), None
    )
    assert not decision2.allowed


def test_smart_safe_tool_skips_verdict():
    ask = _ask_factory("ALLOW")
    approver = SmartApprover(base=AllowAllApprover(), ask_model=ask)
    assert approver.check(_tool(Risk.SAFE), {"q": "x"}).allowed
    assert ask.calls == []  # SAFE 不花裁决调用
