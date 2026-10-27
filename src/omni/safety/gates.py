"""审批闸门：工具风险分级 + 按上下文选择审批策略（Strategy 模式）。

设计约定：
- Risk 在每个工具上声明（SAFE / CONFIRM / DANGEROUS），由"触发上下文"决定放行策略；
- Approver 是纯决策接口，不知道具体工具类型的存在——依赖方向保持 tools → safety 单向；
- 上下文默认 fail-closed：没人能点确认的场景（cron/心跳）必须用 DenyApprover。
"""
from __future__ import annotations

import abc
import json
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


def private_target_allowed(allowed_users, target: int) -> Decision:
    """QQ 私聊/主动投递的唯一判定入口（A03/A04 的根因修复）。

    为什么收在一处：旧代码在两处写出了相反的语义——
    入站 `if allowed_users and ...`（空名单=不限）、工具 `if user not in allowed`（空名单=全拒）。
    现在统一为 **空白名单 = 谁都不许**：没有配置所有者等于没有授权任何目标。
    """
    allowed = {int(user) for user in (allowed_users or [])}
    if not allowed:
        return Decision(False, "QQ 私聊白名单为空——先配置所有者的 QQ 号；空名单不等于不限")
    if int(target) not in allowed:
        return Decision(False, f"QQ {target} 不在私聊白名单内")
    return Decision(True)


class Approver(abc.ABC):
    """审批策略接口：给定工具与本次调用参数，决定是否放行。

    arguments 可为 None（旧式调用）；smart 档需要看参数才能判风险，
    横幅/终端问询也能展示实际参数。
    """

    allows_auto_approval: bool = True

    @abc.abstractmethod
    def check(self, tool: Any, arguments: Any = None) -> Decision: ...


class AllowAllApprover(Approver):
    """测试/受控场景：全部放行。"""

    def check(self, tool: Any, arguments: Any = None) -> Decision:
        return Decision(True)


class DenyApprover(Approver):
    """无人值守上下文（cron/心跳，P5）：非 safe 一律拒绝（fail-closed）。"""

    allows_auto_approval = False

    def check(self, tool: Any, arguments: Any = None) -> Decision:
        if getattr(tool, "risk", Risk.SAFE) == Risk.SAFE:
            return Decision(True)
        return Decision(False, "无人值守上下文默认拒绝非 safe 工具")


class ConsoleApprover(Approver):
    """交互式 CLI 上下文：confirm 级当场问用户；dangerous 级拒绝。"""

    def __init__(self, console: Any) -> None:
        self._console = console

    def check(self, tool: Any, arguments: Any = None) -> Decision:
        risk = getattr(tool, "risk", Risk.SAFE)
        if risk == Risk.SAFE:
            return Decision(True)
        if risk == Risk.DANGEROUS:
            return Decision(False, "dangerous 工具需要 P3 的显式授权机制，当前一律拒绝")
        args_text = ""
        if arguments:
            import json as _json

            try:
                args_text = _json.dumps(arguments, ensure_ascii=False)[:300]
            except (TypeError, ValueError):
                args_text = str(arguments)[:300]
        answer = self._console.input(
            f"[bold yellow]即将执行工具 {tool.name} {args_text}（{tool.description}），允许? [y/N][/] "
        ).strip().lower()
        if answer in ("y", "yes"):
            return Decision(True)
        return Decision(False, "用户在确认时拒绝了该操作")


class WebApprover(Approver):
    """Web 工作台上下文：CONFIRM 级弹工作台确认横幅（阻塞等用户点击，超时拒）。"""

    def __init__(self, broker: Any) -> None:
        self._broker = broker

    def check(self, tool: Any, arguments: Any = None) -> Decision:
        risk = getattr(tool, "risk", Risk.SAFE)
        if risk == Risk.SAFE:
            return Decision(True)
        if risk == Risk.DANGEROUS:
            return Decision(False, "dangerous 级操作在当前版本一律拒绝")
        return self._broker.request(
            tool, detail=json.dumps(arguments or {}, ensure_ascii=False, indent=2)
        )
