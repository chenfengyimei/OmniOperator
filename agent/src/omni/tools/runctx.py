"""低层可见的"当前运行"引用（D09）。

`tools/` 不允许 import `core/`（分层守卫），但委派工具必须知道三件事：
我在哪个 run 里、还能用几步、父 run 是否已经被取消。所以这里只定义**形状**：
`RunHandle` 是一个协议，core 的 `RunContext` 满足它；`RunRef` 是 thread-local 环境值，
由 Agent 在回合开始时绑定、回合结束恢复（与 `SessionRef` 同一套做法）。
"""
from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol, runtime_checkable


@runtime_checkable
class RunHandle(Protocol):
    """一次运行的最小对外形状（core.RunContext 实现它）。"""

    run_id: str
    parent_run_id: str | None
    channel: str
    scope: str          # "private" / "group"：委派出去也不该让群聊拿到私人上下文
    steps_left: int

    @property
    def cancelled(self) -> bool: ...


class RunRef:
    """thread-local 的"当前 run"：多通道并发时各看各的，不共享。"""

    def __init__(self) -> None:
        self._local = threading.local()

    def set(self, handle: RunHandle | None) -> None:
        self._local.run = handle

    def get(self) -> RunHandle | None:
        return getattr(self._local, "run", None)

    @contextmanager
    def bind(self, handle: RunHandle | None) -> Iterator[None]:
        previous = self.get()
        self.set(handle)
        try:
            yield
        finally:
            self.set(previous)
