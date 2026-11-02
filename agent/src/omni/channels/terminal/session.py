"""单终端会话的读、写、窗口尺寸和退出确认。"""

from __future__ import annotations

import threading

from . import backend
from .backend import DEFAULT_COLS, DEFAULT_ROWS


class TerminalSession:
    """单个终端会话：ConPTY 包一个 PowerShell 进程（读写需串行，加锁）。"""

    def __init__(self, cols: int = DEFAULT_COLS, rows: int = DEFAULT_ROWS, *, shell=None, cwd=None) -> None:
        self._pty = backend.open_process(rows, cols, **({"shell": shell, "cwd": cwd} if shell else {}))
        self._lock = threading.Lock()
        self._read_lock = threading.Lock()

    def read(self, size: int = 65536) -> str:
        """阻塞读一段终端输出——必须放在后台线程跑，别堵事件循环。"""
        with self._read_lock:
            return self._pty.read(size)

    def write(self, data: str) -> None:
        with self._lock:
            self._pty.write(data)

    def resize(self, cols: int, rows: int) -> None:
        if not 20 <= cols <= 500 or not 5 <= rows <= 200:
            raise ValueError("终端尺寸超限")
        with self._lock:
            self._pty.setwinsize(int(rows), int(cols))

    def isalive(self) -> bool:
        return self._pty.isalive()

    def kill(self) -> bool:
        try:
            if self._pty.isalive():
                self._pty.terminate(force=True)
            if not self._pty.isalive() and callable(getattr(self._pty, "close", None)):
                self._pty.close(force=True)
            return not self._pty.isalive()
        except Exception:  # noqa: BLE001, S110  （有意容错：边界转换/生命周期清理不阻断）
            return False
