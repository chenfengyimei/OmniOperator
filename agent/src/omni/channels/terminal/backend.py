"""ConPTY 平台适配：检测可用性与创建进程；不管理会话池或 HTTP。"""
import shutil

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


def shell_options() -> dict[str, list[str]]:
    candidates = {'powershell': ['powershell.exe', '-NoLogo', '-NoProfile'],
                  'pwsh': ['pwsh.exe', '-NoLogo', '-NoProfile'], 'cmd': ['cmd.exe', '/D']}
    return {name: [resolved, *args[1:]] for name, args in candidates.items()
            if (resolved := shutil.which(args[0]))}


def open_process(rows: int, cols: int, *, shell: str | None = None, cwd=None):
    if not AVAILABLE or PtyProcess is None:
        raise RuntimeError(f"pywinpty 不可用：{IMPORT_ERROR}")
    if not 20 <= cols <= 500 or not 5 <= rows <= 200:
        raise RuntimeError('终端尺寸超限')
    if shell is None:
        return PtyProcess.spawn(SHELL_COMMAND, dimensions=(int(rows), int(cols)))
    options = shell_options()
    if shell not in options:
        raise RuntimeError('Shell 不可用或不在固定选项内')
    return PtyProcess.spawn(options[shell], cwd=str(cwd), dimensions=(int(rows), int(cols)))
