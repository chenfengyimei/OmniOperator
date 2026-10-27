"""工作台内置终端（Windows ConPTY）：一个 WS 连接 = 一个独立 PowerShell 会话。

为什么：服务与日常操作都在浏览器里完成，不再弹出/占用 Windows 终端窗口；
这也是 agent 与用户共用本机操作面的观察窗（工作台 → 终端按钮）。

安全边界：
- 仅随工作台监听 127.0.0.1（web.py 默认绑定），本机使用者即是操作者，不提供跨机访问；
- 会话数有上限（防资源泄漏），WS 断开即杀进程；
- Windows 专用（pywinpty/ConPTY）；其他平台后续可用 pty 模块补齐。
"""
from __future__ import annotations

import threading

try:
    from winpty import PtyProcess

    _AVAILABLE = True
    _IMPORT_ERROR = ""
except ImportError as exc:  # 非 Windows / 未安装依赖时降级为不可用
    _AVAILABLE = False
    _IMPORT_ERROR = str(exc)

SHELL_COMMAND = "powershell.exe -NoLogo -NoProfile"
DEFAULT_COLS = 120
DEFAULT_ROWS = 30
MAX_SESSIONS = 4


class TerminalSession:
    """单个终端会话：ConPTY 包一个 PowerShell 进程（读写需串行，加锁）。"""

    def __init__(self, cols: int = DEFAULT_COLS, rows: int = DEFAULT_ROWS) -> None:
        if not _AVAILABLE:
            raise RuntimeError(f"pywinpty 不可用：{_IMPORT_ERROR}")
        self._pty = PtyProcess.spawn(
            SHELL_COMMAND, dimensions=(int(rows), int(cols))
        )
        self._lock = threading.Lock()

    def read(self, size: int = 65536) -> str:
        """阻塞读一段终端输出——必须放在后台线程跑，别堵事件循环。"""
        with self._lock:
            return self._pty.read(size)

    def write(self, data: str) -> None:
        with self._lock:
            self._pty.write(data)

    def resize(self, cols: int, rows: int) -> None:
        with self._lock:
            self._pty.setwinsize(int(rows), int(cols))

    def isalive(self) -> bool:
        return self._pty.isalive()

    def kill(self) -> None:
        try:
            if self._pty.isalive():
                self._pty.terminate(force=True)
        except Exception:  # noqa: BLE001 —— 终止失败也无从恢复，尽力而为
            pass


class TerminalManager:
    """会话池：限流 + 计数（WS 断开由路由负责归还）。"""

    def __init__(self, max_sessions: int = MAX_SESSIONS) -> None:
        self._max = max_sessions
        self._count = 0
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return _AVAILABLE

    @property
    def unavailable_reason(self) -> str:
        return _IMPORT_ERROR

    def open(self, cols: int = DEFAULT_COLS, rows: int = DEFAULT_ROWS) -> TerminalSession:
        with self._lock:
            if self._count >= self._max:
                raise RuntimeError(f"终端会话数已达上限（{self._max}），先关闭不用的")
            session = TerminalSession(cols, rows)
            self._count += 1
            return session

    def close(self, session: TerminalSession) -> None:
        session.kill()
        with self._lock:
            self._count = max(0, self._count - 1)
