"""终端会话池：并发上限、关闭预算和残留报告。"""
from __future__ import annotations
import threading
from . import backend
from .backend import DEFAULT_COLS, DEFAULT_ROWS, MAX_SESSIONS
from .session import TerminalSession


class TerminalManager:
    """会话池：限流 + 计数（WS 断开由路由负责归还）。"""

    def __init__(self, max_sessions: int = MAX_SESSIONS) -> None:
        self._max = max_sessions
        self._count = 0
        self._sessions: set[TerminalSession] = set()
        self._closed = False
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return backend.AVAILABLE

    @property
    def unavailable_reason(self) -> str:
        return backend.IMPORT_ERROR

    def open(self, cols: int = DEFAULT_COLS, rows: int = DEFAULT_ROWS) -> TerminalSession:
        with self._lock:
            if self._closed:
                raise RuntimeError("终端管理器正在关闭")
            if self._count >= self._max:
                raise RuntimeError(f"终端会话数已达上限（{self._max}），先关闭不用的")
            session = TerminalSession(cols, rows)
            self._count += 1
            self._sessions.add(session)
            return session

    def close(self, session: TerminalSession) -> bool:
        done = session.kill()
        with self._lock:
            if done and session in self._sessions:
                self._sessions.remove(session)
                self._count = len(self._sessions)
        return done

    def close_all(self, wait_s: float = 5) -> dict:
        import time
        deadline = time.monotonic() + max(0, wait_s)
        with self._lock:
            self._closed = True
            sessions = list(self._sessions)
        workers = [threading.Thread(target=self.close, args=(session,), daemon=True) for session in sessions]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(max(0, deadline - time.monotonic()))
        with self._lock:
            return {"complete": not self._sessions, "residual": len(self._sessions)}
