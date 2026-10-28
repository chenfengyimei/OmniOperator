"""ConPTY 平台适配：检测可用性与创建进程；不管理会话池或 HTTP。"""
try:
    from winpty import PtyProcess

    AVAILABLE = True
    IMPORT_ERROR = ""
except ImportError as exc:  # 非 Windows / 未安装依赖时降级为不可用
    PtyProcess = None
    AVAILABLE = False
    IMPORT_ERROR = str(exc)

SHELL_COMMAND = "powershell.exe -NoLogo -NoProfile"
DEFAULT_COLS = 120
DEFAULT_ROWS = 30
MAX_SESSIONS = 4


def open_process(rows: int, cols: int):
    if not AVAILABLE or PtyProcess is None:
        raise RuntimeError(f"pywinpty 不可用：{IMPORT_ERROR}")
    return PtyProcess.spawn(SHELL_COMMAND, dimensions=(int(rows), int(cols)))
