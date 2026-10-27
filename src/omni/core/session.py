"""会话作用域：同一会话串行、不同会话并行、排队有界、跨进程有租约。

背景（A06）：Web 的 `/api/chat` 以前先改 `agent.session_id` 再 `chat()`，回答与 review
也回头读这个共享值。两个并发请求会互相串话——用户 A 的提问出现在 B 的历史里，
A 的记忆整理被记到 B 的会话上。"当前选中会话"本质上属于**客户端状态**，不该是进程级变量。

两层闸门，各管一段：

| 层 | 位置 | 管什么 | 越界的后果 |
| --- | --- | --- | --- |
| `SessionGate` | 进程内（事件循环） | 同一会话的回合按到达顺序执行；等待队列有上限 | 满了直接返回 busy，不无限堆积 |
| `SessionLeases` | `state.db` | 同一会话同一时刻只有一个**进程**在跑 | 另一个进程拿到 busy（带持有者），不并发写同一段历史 |

明确不做：不用一把全局锁把所有入口永远串行——不同会话必须能并行跑。
"""
from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Iterator
from dataclasses import dataclass

from ..memory.state import StateStore, StateUnavailable


class SessionBusy(RuntimeError):
    """会话正在被处理（本进程排队满，或另一个进程持有租约）。"""

    def __init__(self, message: str, *, holder: str | None = None) -> None:
        super().__init__(message)
        self.holder = holder


@dataclass
class _Slot:
    lock: asyncio.Lock
    waiting: int = 0


class SessionGate:
    """进程内会话闸门。

    `capacity` 是**等待队列**上限（不含正在执行的那一个）。默认 1：允许排队一条，
    再来就直接 busy——本地单用户场景下排队比堆任务更有用，堆着只会拖长响应。
    """

    def __init__(self, capacity: int = 1) -> None:
        self._capacity = max(1, int(capacity))
        self._slots: dict[tuple[int, int], _Slot] = {}

    def waiting(self, session_id: int) -> int:
        for (loop_id, sid), slot in self._slots.items():
            if sid == int(session_id):
                return slot.waiting
        return 0

    @contextlib.asynccontextmanager
    async def turn(self, session_id: int):
        sid = int(session_id)
        # asyncio 原语与事件循环绑定：按 (loop, session) 分槽，测试里 asyncio.run 各起一个循环也不会串
        key = (id(asyncio.get_running_loop()), sid)
        slot = self._slots.get(key)
        if slot is None:
            slot = self._slots[key] = _Slot(lock=asyncio.Lock())
        if slot.waiting >= self._capacity:
            raise SessionBusy(
                f"会话 {sid} 正忙（已有 {slot.waiting} 条在排队）；请等上一条回答完再发",
            )
        slot.waiting += 1
        try:
            await slot.lock.acquire()
        finally:
            slot.waiting -= 1
        try:
            yield
        finally:
            slot.lock.release()
            if not slot.lock.locked() and slot.waiting == 0:
                self._slots.pop(key, None)


class SessionLeases:
    """跨进程会话租约（落在 `state.db`）。

    拿不到就返回 busy 并带上持有者——不要"等一会儿再试"，等待应当由用户决定。
    进程崩溃后租约按 TTL 自动过期，不需要人工解锁。
    """

    def __init__(
        self,
        state: StateStore,
        *,
        owner: str | None = None,
        ttl_s: float = 600.0,
    ) -> None:
        self._state = state
        self.owner = owner or f"pid:{os.getpid()}"
        self.ttl_s = float(ttl_s)

    @contextlib.contextmanager
    def hold(self, session_id: int) -> Iterator[str]:
        """占用会话；被别的进程占着时抛 SessionBusy（带持有者）。"""
        try:
            lease = self._state.acquire_session_lease(
                session_id, self.owner, ttl_s=self.ttl_s
            )
        except StateUnavailable as exc:
            # 状态库不可用时不假装互斥成立：宁可拒绝，也不要两个进程同时写一段历史
            raise SessionBusy(f"无法确认会话租约（{exc}）") from exc
        if lease is None:
            current = self._state.session_lease(session_id)
            holder = current.owner if current else "未知进程"
            raise SessionBusy(f"会话 {session_id} 正被 {holder} 处理", holder=holder)
        try:
            yield lease.token
        finally:
            self._state.release_session_lease(lease.token)
