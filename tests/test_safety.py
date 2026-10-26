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
