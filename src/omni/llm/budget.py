"""费用预留与结算的契约（D10）。

预算是**确定性代码约束**，不是提示词里的一句"请节约"：

- 调用前：按"输入估算 + 输出上界"算出这次最多可能花多少，先**预留**；
  预留不成功就不发请求——不是等花超了再报错。
- 调用后：拿供应商上报的 usage 结算（预留退回，实际计入）；供应商没报 usage
  或调用中途失败时，**保守保留**（超时/连接中断可能已经计费）。
- 父子运行共享同一份预算对象：子任务多花一美元，父任务就少一美元。

这一层只定义数字与契约，不碰 SQLite（`memory/usage.py` 才是仓储）：
`llm` 层不允许 import `memory`（见 tests/test_arch.py）。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Protocol

from .usage import MICRO, Price, Usage

# 结算来源：真实上报 / 估算 / 未上报按预留保留 / 未知（单价不明，记 NULL）/ 确认未计费
SOURCE_REPORTED = "reported"
SOURCE_ESTIMATED = "estimated"
SOURCE_RESERVED = "reserved"
SOURCE_UNKNOWN = "unknown"
SOURCE_NOT_BILLED = "not_billed"


@dataclass(frozen=True)
class Quote:
    """一次调用前的报价：预留多少、依据是什么。"""

    micro: int | None  # None = 单价未知，无法预留
    prompt_tokens: int
    completion_tokens: int
    price: Price

    @property
    def reservable(self) -> bool:
        return self.micro is not None


@dataclass(frozen=True)
class Reservation:
    """预留凭据：成功预留后的句柄，结算时必须原样交回。"""

    day: str
    micro: int
    quote: Quote
    ledger_held: bool = False
    run_held: bool = False

    @property
    def reserved(self) -> bool:
        return self.micro > 0 and (self.ledger_held or self.run_held)


class Ledger(Protocol):
    """跨进程费用账本（实现见 `memory/usage.py`）。`limit_micro<=0` 表示不限。"""

    def reserve(self, *, day: str, micro: int, limit_micro: int) -> tuple[bool, int]:
        """尝试预留；返回 (是否成功, 今日已预留+已花费)。"""

    def settle(self, *, day: str, reserved_micro: int, charged_micro: int) -> None:
        """结算：退回预留，把实际花费计入。"""

    def record(self, **fields: object) -> None:
        """记一条调用流水（只用于观测，失败不影响对话）。"""


class NullLedger:
    """没有注入账本时的空实现（嵌入式用法/单测）：不落库，预算仍按内存判定。"""

    def reserve(self, *, day: str, micro: int, limit_micro: int) -> tuple[bool, int]:
        return True, micro

    def settle(self, *, day: str, reserved_micro: int, charged_micro: int) -> None:
        return None

    def record(self, **fields: object) -> None:
        return None


class RunBudget:
    """单次运行的费用上限（父子共享**同一个实例**）。

    `limit_micro<=0` 表示未设上限，此时只累计不拦截——但累计值仍会被记入
    run 事件与界面，便于事后解释"这轮花了多少"。
    """

    def __init__(self, limit_micro: int = 0) -> None:
        self.limit_micro = max(0, int(limit_micro))
        self._lock = threading.Lock()
        self.spent_micro = 0
        self.reserved_micro = 0
        self.calls = 0
        self.blocked = 0

    def try_reserve(self, micro: int | None) -> bool:
        """预留；`None`（单价未知）在不限额时放行、有限额时拒绝。"""
        with self._lock:
            if micro is None:
                if self.limit_micro:
                    self.blocked += 1
                    return False
                return True
            micro = max(0, int(micro))
            if self.limit_micro and self.spent_micro + self.reserved_micro + micro > self.limit_micro:
                self.blocked += 1
                return False
            self.reserved_micro += micro
            return True

    def settle(self, reserved_micro: int, charged_micro: int | None) -> None:
        with self._lock:
            self.reserved_micro = max(0, self.reserved_micro - max(0, int(reserved_micro)))
            self.spent_micro += max(0, int(charged_micro or 0))
            self.calls += 1

    def release(self, reserved_micro: int | None) -> None:
        if not reserved_micro:
            return
        with self._lock:
            self.reserved_micro = max(0, self.reserved_micro - max(0, int(reserved_micro)))

    @property
    def remaining_micro(self) -> int | None:
        with self._lock:
            if not self.limit_micro:
                return None
            return max(0, self.limit_micro - self.spent_micro - self.reserved_micro)

    def brief(self) -> dict[str, object]:
        with self._lock:
            return {
                "limit_micro": self.limit_micro or None,
                "spent_micro": self.spent_micro,
                "reserved_micro": self.reserved_micro,
                "calls": self.calls,
                "blocked": self.blocked,
            }

    def child(self) -> RunBudget:
        """子运行**共用**父的额度：返回自身，不是副本。"""
        return self


def settle_amount(
    usage: Usage, price: Price, reserved_micro: int
) -> tuple[int | None, str]:
    """结算金额与来源。返回 `(微美元或 None, 来源)`。

    - 有 usage 且有单价 → 真实金额；
    - 有 usage 但单价未知 → `None`（未知不是零）；
    - 无 usage 且有预留 → 按预留保守保留；
    - 无 usage 且无单价 → `None`。
    """
    if usage.reported:
        if price.known:
            cost = price.cost_usd(usage.prompt_tokens or 0, usage.completion_tokens or 0)
            if cost is None:
                return None, SOURCE_UNKNOWN
            micro = int(round(cost * MICRO))
            if micro == 0 and cost > 0:
                micro = 1
            return micro, SOURCE_REPORTED
        return None, SOURCE_UNKNOWN
    if reserved_micro > 0:
        return reserved_micro, SOURCE_RESERVED
    return None, SOURCE_UNKNOWN
