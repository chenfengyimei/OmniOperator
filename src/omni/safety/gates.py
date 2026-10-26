"""审批闸门：工具风险分级 + 按上下文选择审批策略（Strategy 模式）。

设计约定：
- Risk 在每个工具上声明（SAFE / CONFIRM / DANGEROUS），由"触发上下文"决定放行策略；
- Approver 是纯决策接口，不知道具体工具类型的存在——依赖方向保持 tools → safety 单向；
- 上下文默认 fail-closed：没人能点确认的场景（cron/心跳）必须用 DenyApprover。
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from enum import Enum
from typing import Any


class Risk(str, Enum):
    SAFE = "safe"            # 只读/本地无害操作，直通
    CONFIRM = "confirm"      # 需要人确认的写操作（发消息、发布…）
    DANGEROUS = "dangerous"  # 高危（支付/删除/安装…），P3 接入显式授权前一律拒绝


@dataclass
class Decision:
    allowed: bool
    reason: str = ""


class Approver(abc.ABC):
    """审批策略接口：给定工具，决定是否放行。"""

    @abc.abstractmethod
    def check(self, tool: Any) -> Decision: ...


class AllowAllApprover(Approver):
    """测试/受控场景：全部放行。"""

    def check(self, tool: Any) -> Decision:
        return Decision(True)


class DenyApprover(Approver):
    """无人值守上下文（cron/心跳，P5）：非 safe 一律拒绝（fail-closed）。"""

    def check(self, tool: Any) -> Decision:
        if getattr(tool, "risk", Risk.SAFE) == Risk.SAFE:
            return Decision(True)
        return Decision(False, "无人值守上下文默认拒绝非 safe 工具")


class ConsoleApprover(Approver):
    """交互式 CLI 上下文：confirm 级当场问用户；dangerous 级拒绝。"""

    def __init__(self, console: Any) -> None:
        self._console = console

    def check(self, tool: Any) -> Decision:
        risk = getattr(tool, "risk", Risk.SAFE)
        if risk == Risk.SAFE:
            return Decision(True)
        if risk == Risk.DANGEROUS:
            return Decision(False, "dangerous 工具需要 P3 的显式授权机制，当前一律拒绝")
        answer = self._console.input(
            f"[bold yellow]即将执行工具 {tool.name}（{tool.description}），允许? [y/N][/] "
        ).strip().lower()
        if answer in ("y", "yes"):
            return Decision(True)
        return Decision(False, "用户在确认时拒绝了该操作")


class WebApprover(Approver):
    """Web 工作台上下文：CONFIRM 级弹工作台确认横幅（阻塞等用户点击，超时拒）。"""

    def __init__(self, broker: Any) -> None:
        self._broker = broker

    def check(self, tool: Any) -> Decision:
        risk = getattr(tool, "risk", Risk.SAFE)
        if risk == Risk.SAFE:
            return Decision(True)
        if risk == Risk.DANGEROUS:
            return Decision(False, "dangerous 级操作在当前版本一律拒绝")
        return self._broker.request(tool)
