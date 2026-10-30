"""进程边界：argv 执行、输出上限、超时后的进程树回收与"副作用是否已知"。

为什么单独一层（A19）：
- 旧实现直接用 `subprocess.run(shell=True)`，超时只处理直接子进程，
  Windows 上孙进程会活下来继续写文件，而返回值却写着"已终止、可重试"；
- 现在统一 spawn → 有界读 → 超时杀整棵进程树 → 回报"已确认回收 / 回收状态未知"，
  由工具层决定是否允许重试；写类命令在回收状态未知时一律不给重试建议。

明确不做：不做沙箱、不做权限降级；环境最小化只对 SAFE 观察生效，
`run_command` 保留用户环境（它本来就等价于用户在终端里敲命令）。
"""
from __future__ import annotations

import os
import signal
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

MAX_READ_BYTES = 256 * 1024  # 单流读取上限；超出部分丢弃但继续排空，避免管道写满阻塞
KILL_GRACE_S = 5.0

# SAFE 观察的最小环境：只够定位系统程序和编码，不继承用户的密钥与凭证
_ENV_WHITELIST = (
    "PATH",
    "PATHEXT",
    "SystemRoot",
    "SystemDrive",
    "windir",
    "ComSpec",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "HOMEDRIVE",
    "HOMEPATH",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "LANG",
    "LC_ALL",
)


@dataclass(frozen=True)
class ProcResult:
    """一次执行的结果；`reclaimed` 只表达"进程树是否确认没了"，不是业务成功。"""

    code: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    reclaimed: bool = True


def minimal_env() -> dict[str, str]:
    """SAFE 观察用的环境：白名单变量 + 关闭 pager/交互/可选锁。"""
    env = {name: os.environ[name] for name in _ENV_WHITELIST if name in os.environ}
    env.setdefault("PYTHONUTF8", "1")
    # git 观察时不能弹 pager、不能等凭据输入、不能顺手刷新 index（会写盘）
    env["GIT_PAGER"] = "cat"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    # 系统级 gitconfig 可能挂 pager/外部 diff 驱动；读观察不需要它
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def user_env() -> dict[str, str]:
    """run_command 的环境：用户自己的环境（等价于终端），仅补 UTF-8。"""
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    return env


def _drain(pipe, sink: bytearray) -> None:
    """有界读取：超过上限的内容丢弃但仍继续排空，防止子进程写满管道卡死。"""
    try:
        while True:
            chunk = pipe.read(8192)
            if not chunk:
                break
            if len(sink) < MAX_READ_BYTES:
                sink.extend(chunk[: MAX_READ_BYTES - len(sink)])
    except (OSError, ValueError):
        pass
    finally:
        try:
            pipe.close()
        except (OSError, ValueError):
            pass


def _terminate_tree(proc: subprocess.Popen) -> bool:
    """杀掉整棵进程树；返回是否确认回收（不能确认就是 unknown）。"""
    try:
        if os.name == "nt":
            system_root = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
            taskkill = Path(system_root) / "System32" / "taskkill.exe"
            subprocess.run(
                [str(taskkill), "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=KILL_GRACE_S,
                check=False,
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        # taskkill 失败也继续等主进程；能不能确认回收由下面的 wait 判定
        pass
    try:
        proc.wait(timeout=KILL_GRACE_S)
    except subprocess.TimeoutExpired:
        return False
    return True


def _spawn(
    target: list[str] | str,
    timeout_s: float,
    cwd: Path,
    env: dict[str, str],
    *,
    shell: bool,
) -> ProcResult:
    kwargs: dict = {
        "cwd": str(cwd),
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(target, shell=shell, **kwargs)
    except OSError as exc:
        return ProcResult(code=-1, stdout=b"", stderr=str(exc).encode("utf-8", "replace"))

    out, err = bytearray(), bytearray()
    readers = [
        threading.Thread(target=_drain, args=(proc.stdout, out), daemon=True),
        threading.Thread(target=_drain, args=(proc.stderr, err), daemon=True),
    ]
    for reader in readers:
        reader.start()

    timed_out = False
    reclaimed = True
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        reclaimed = _terminate_tree(proc)
    for reader in readers:
        reader.join(timeout=KILL_GRACE_S)
    if any(reader.is_alive() for reader in readers):
        # 管道没读完说明还有后代进程握着句柄：不能声称已回收
        reclaimed = False

    code = proc.returncode if proc.returncode is not None else -1
    return ProcResult(
        code=code,
        stdout=bytes(out),
        stderr=bytes(err),
        timed_out=timed_out,
        reclaimed=reclaimed,
    )


def run_argv(argv: list[str], timeout_s: float, cwd: Path, env: dict[str, str]) -> ProcResult:
    """受控命令（SAFE）：argv + shell=False，没有 shell 参与。"""
    return _spawn(argv, timeout_s, cwd, env, shell=False)


def run_shell(command: str, timeout_s: float, cwd: Path, env: dict[str, str]) -> ProcResult:
    """任意命令（CONFIRM）：保留 shell 语义，但同样有输出上限与进程树回收。"""
    return _spawn(command, timeout_s, cwd, env, shell=True)
