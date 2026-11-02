"""运行（run）的身份、取消信号与上下文（D09）。

一次回合 = 一个 run。run 的**状态**落在 `state.db`（谁、哪个会话、停在哪一步），
**取消**是内存里的协作式信号（线程之间、父子 run 之间传播）。

三条纪律：
1. **取消是协作式的**：不杀线程、不假装已停止。检查点在"每次模型调用前、模型返回后、
   每次工具调用前"；正在进行的模型请求会在返回后立刻生效。所以状态先变
   `cancelling`（phase），真正停下来才写 `cancelled`。
2. **父子只缩不扩**：子 run 的步数上限不超过父的剩余步数，父取消子必取消；
   子 run 不能换一个更宽松的审批器。
3. **取消要撤销待批准项**：审批器在等待期间轮询取消信号（见 safety/broker.py），
   否则界面上还能点"同意"去执行一个没人等的动作。
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..llm.budget import RunBudget
from ..memory.runs import Run, RunStore


class RunCancelled(RuntimeError):
    """运行被取消（协作式检查点抛出）。"""

    def __init__(self, reason: str = "用户取消") -> None:
        self.reason = reason
        super().__init__(reason)


class RunBudgetExceeded(RuntimeError):
    """费用预算拦住了这次调用（**没有**发出新的模型请求）。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class RunIdentity:
    """一次运行的身份：谁、在哪个会话、从哪个通道来、父 run 是谁。"""

    run_id: str
    session_id: int | None = None
    channel: str = "cli"
    principal: str = "owner"
    parent_run_id: str | None = None
    scope: str = "private"  # private / group

    @classmethod
    def new(cls, **kwargs: Any) -> RunIdentity:
        return cls(run_id=uuid.uuid4().hex[:16], **kwargs)


class CancelToken:
    """一次运行的取消信号；父 token 取消会连带取消所有子 token。"""

    def __init__(self, reason: str = "") -> None:
        self._lock = threading.Lock()
        self._cancelled = False
        self._reason = reason
        self._children: list[CancelToken] = []

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    @property
    def reason(self) -> str:
        with self._lock:
            return self._reason

    def cancel(self, reason: str = "用户取消") -> bool:
        """返回是否由这次调用完成了状态翻转（重复取消返回 False）。"""
        with self._lock:
            if self._cancelled:
                return False
            self._cancelled = True
            self._reason = reason or "用户取消"
            children = list(self._children)
        for child in children:
            child.cancel(f"父运行已取消：{self._reason}")
        return True

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise RunCancelled(self.reason)

    # ---------- 父子关系 ----------

    def link_child(self, child: CancelToken) -> None:
        with self._lock:
            if self._cancelled:
                cancelled, reason = True, self._reason
            else:
                self._children.append(child)
                cancelled, reason = False, ""
        if cancelled:  # 父已取消：立刻传染，不留下孤儿运行
            child.cancel(f"父运行已取消：{reason}")

    def unlink_child(self, child: CancelToken) -> None:
        with self._lock:
            if child in self._children:
                self._children.remove(child)


@dataclass
class RunContext:
    """一次运行的运行时上下文（满足 tools.runctx.RunHandle 的形状）。"""

    identity: RunIdentity
    store: RunStore
    max_steps: int
    cancel_token: CancelToken = field(default_factory=CancelToken)
    steps_used: int = 0
    # 费用预算：父子 run 共享同一个 RunBudget 实例（子多花 = 父少花）
    budget: RunBudget = field(default_factory=RunBudget)
    # 能力档位元数据（D28）：由内核在判定后写入，通道据此回显本回合用了哪一档
    requested_mode: str = ""
    mode: str = ""
    mode_source: str = ""
    escalated: bool = False
    attached_image_ids: set[str] = field(default_factory=set)
    image_messages: dict[str, dict] = field(default_factory=dict, repr=False)
    image_notices: set[tuple[str, str]] = field(default_factory=set, repr=False)

    @property
    def _attached_image_ids(self) -> set[str]:
        """兼容旧嵌入调用；实际状态只有 attached_image_ids 一份。"""
        return self.attached_image_ids

    @_attached_image_ids.setter
    def _attached_image_ids(self, value: set[str]) -> None:
        self.attached_image_ids = value

    # ---------- RunHandle ----------

    @property
    def run_id(self) -> str:
        return self.identity.run_id

    @property
    def parent_run_id(self) -> str | None:
        return self.identity.parent_run_id

    @property
    def channel(self) -> str:
        return self.identity.channel

    @property
    def scope(self) -> str:
        return self.identity.scope

    @property
    def cancelled(self) -> bool:
        return self.cancel_token.cancelled

    @property
    def steps_left(self) -> int:
        return max(0, self.max_steps - self.steps_used)

    # ---------- 检查与记账 ----------

    def check(self) -> None:
        self.cancel_token.raise_if_cancelled()

    def consume_step(self) -> bool:
        """模型调用前记账；返回 False 表示步数预算已用完（不是取消）。"""
        if self.steps_left <= 0:
            return False
        self.steps_used += 1
        return True

    def event(self, kind: str, **data: Any) -> int:
        try:
            return self.store.append_event(self.run_id, kind, data)
        except Exception:  # noqa: BLE001 —— 记录失败不能把回合打断
            return -1

    def phase(self, name: str) -> None:
        """更新运行阶段；取消已请求后是 no-op，绝不覆写"cancelling"。

        竞态背景：cancel() 先写"cancelling"再翻转 token；若取消恰落在
        register→首次 phase("model") 的组装窗口、或 phase 写入的瞬间，
        后续阶段写入会把"正在取消"覆写回"model"——界面就看不到正在取消了。
        写入后复查一次 token：取消在写入期间落地就补写"cancelling"自愈。
        """
        try:
            if not self.cancel_token.cancelled:
                self.store.touch(self.run_id, phase=name)
                if self.cancel_token.cancelled:
                    self.store.touch(self.run_id, phase="cancelling")
        except Exception:  # noqa: BLE001, S110
            pass

    def finish(self, status: str, *, error_code: str = "", error_text: str = "") -> None:
        try:
            self.store.finish(self.run_id, status, error_code=error_code, error_text=error_text)
        except Exception:  # noqa: BLE001, S110
            pass


@dataclass(frozen=True)
class TurnResult:
    """一次回合的结果（通道需要 run_id 去查事件流）。"""

    answer: str
    run_id: str
    status: str
    error_code: str = ""
    cost_micro: int | None = None
    budget: dict[str, Any] = field(default_factory=dict)
    # 能力档位（D28）：请求的档位与最终生效的档位；未启用路由时为空。
    requested_mode: str = ""
    mode: str = ""
    mode_source: str = ""
    escalated: bool = False


class ActiveRuns:
    """本进程内正在跑的 run（取消入口需要拿到 token）。

    跨进程的取消不做：另一个进程里的线程没法被本进程叫停，假装能停才是危险的。
    查不到就如实回 404/409，让界面显示"该运行不在本进程"。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, RunContext] = {}

    def register(self, run: RunContext) -> None:
        with self._lock:
            self._runs[run.run_id] = run

    def unregister(self, run_id: str) -> None:
        with self._lock:
            self._runs.pop(run_id, None)

    def get(self, run_id: str) -> RunContext | None:
        with self._lock:
            return self._runs.get(run_id)

    def contexts(self) -> list[RunContext]:
        """本进程活着的 RunContext（含回合内临时状态，如已附图 id；D15 产物清理用）。

        与 `snapshot()` 刻意分开：那个返回**库内 `Run` 记录**给界面看，
        这个返回活对象——同名曾导致后者覆盖前者、产物保引用静默失效。
        """
        with self._lock:
            return list(self._runs.values())

    def cancel(self, run_id: str, reason: str = "用户取消") -> bool:
        run = self.get(run_id)
        if run is None:
            return False
        # **先翻转信号、再落库"cancelling"**（顺序是正确性要求，不是风格）：
        # phase() 以 token 为准跳写并在写后复查自愈。若先落库再翻转，worker 的
        # phase() 可能在"库已写、token 未翻"的窗口里完成两次检查——"model"覆写
        # "cancelling"且自愈不触发（并发负载下真实发生过两次）。
        flipped = run.cancel_token.cancel(reason)
        run.event("cancel_requested", reason=reason)
        try:
            run.store.touch(run_id, phase="cancelling")
        except Exception:  # noqa: BLE001, S110
            pass
        return flipped

    def cancel_all(self, reason: str = "进程退出") -> int:
        with self._lock:
            runs = list(self._runs.values())
        return sum(1 for run in runs if run.cancel_token.cancel(reason))

    def snapshot(self) -> list[Run]:
        """给界面看：本进程活着的 run 的库内状态。"""
        out: list[Run] = []
        for run in list(self._runs.values()):
            record = run.store.get(run.run_id)
            if record is not None:
                out.append(record)
        return out
