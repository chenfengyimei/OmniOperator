"""终端会话池：并发上限、关闭预算和残留报告。"""
from __future__ import annotations
import threading
from pathlib import Path
from ...config import PROJECT_ROOT
from . import backend
from .backend import DEFAULT_COLS, DEFAULT_ROWS, MAX_SESSIONS
from .session import TerminalSession
from .managed import ManagedTerminal


class TerminalManager:
    """会话池：限流 + 计数（WS 断开由路由负责归还）。"""

    def __init__(self, max_sessions: int = MAX_SESSIONS, *, roots=None, buffer_bytes=1048576, detach_ttl_s=600) -> None:
        self._max = max_sessions
        self._count = 0
        self._sessions: set[TerminalSession] = set()
        self._closed = False
        self._lock = threading.Lock()
        self._roots = [Path(p).resolve() for p in (roots or [PROJECT_ROOT])]
        self._buffer_bytes, self._detach_ttl = buffer_bytes, detach_ttl_s
        self._managed: dict[str, ManagedTerminal] = {}
        self._stop = threading.Event()
        self._reaper = None

    @property
    def available(self) -> bool:
        return backend.AVAILABLE

    @property
    def unavailable_reason(self) -> str:
        return backend.IMPORT_ERROR

    def open(self, cols: int = DEFAULT_COLS, rows: int = DEFAULT_ROWS, *, shell=None, cwd=None) -> TerminalSession:
        with self._lock:
            if self._closed:
                raise RuntimeError("终端管理器正在关闭")
            if self._count >= self._max:
                raise RuntimeError(f"终端会话数已达上限（{self._max}），先关闭不用的")
            session = TerminalSession(cols, rows, **({'shell':shell,'cwd':cwd} if shell else {}))
            self._count += 1
            self._sessions.add(session)
            return session

    def options(self):
        return {'available':self.available, 'shells':list(backend.shell_options()),
                'roots':[str(p) for p in self._roots], 'max_sessions':self._max,
                'detach_ttl_s':self._detach_ttl}

    def snapshot(self):
        with self._lock:
            return {'available':self.available,'active':self._count,'limit':self._max,
                    'retained':len(self._managed),'closing':self._closed,
                    'readers':sum(item.worker.is_alive() for item in self._managed.values())}

    def create(self, owner, *, shell='powershell', cwd=None, cols=120, rows=30):
        root = Path(cwd or self._roots[0])
        if not root.is_absolute():
            root = self._roots[0] / root
        root = root.resolve(strict=True)
        if not root.is_dir() or not any(root.is_relative_to(p) for p in self._roots):
            raise RuntimeError('终端目录必须位于配置工作区内')
        session = self.open(cols, rows, shell=shell, cwd=root)
        item = ManagedTerminal(session, owner, shell, str(root), buffer_bytes=self._buffer_bytes, on_exit=self.close)
        with self._lock:
            if self._closed:
                self.close_later(session)
                raise RuntimeError('终端管理器正在关闭')
            self._managed[item.id] = item
            for key in list(self._managed):
                old = self._managed[key]
                if len(self._managed) > 32 and old.status == 'exited' and not old.connected:
                    self._managed.pop(key)
            if self._reaper is None:
                self._reaper = threading.Thread(target=self._reap, daemon=True, name='terminal-reaper')
                self._reaper.start()
        return item

    def close_later(self, session):
        threading.Thread(target=self.close, args=(session,), daemon=True).start()

    def get(self, owner, identifier):
        with self._lock:
            item = self._managed.get(identifier)
            if item is None or item.owner != owner:
                raise KeyError('终端不存在')
            return item

    def list_owned(self, owner):
        with self._lock:
            items = [item for item in self._managed.values() if item.owner == owner]
        return [item.brief() for item in items]

    def _reap(self):
        while not self._stop.wait(1):
            with self._lock:
                items = list(self._managed.values())
            for item in items:
                if item.status != 'exited' and item.expired(self._detach_ttl):
                    self.close(item.session)

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
        self._stop.set()
        with self._lock:
            self._closed = True
            sessions = list(self._sessions)
        workers = [threading.Thread(target=self.close, args=(session,), daemon=True) for session in sessions]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(max(0, deadline - time.monotonic()))
        for item in list(self._managed.values()):
            item.worker.join(max(0, deadline - time.monotonic()))
        if self._reaper is not None:
            self._reaper.join(max(0,deadline-time.monotonic()))
        with self._lock:
            residual = max(len(self._sessions), sum(item.worker.is_alive() for item in self._managed.values()))
            if self._reaper is not None and self._reaper.is_alive():residual+=1
            return {"complete": not residual, "residual": residual}
