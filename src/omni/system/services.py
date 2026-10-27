"""本地服务管理：NapCat 与 OmniOperator 常驻服务的 启动/停止/状态。

判定优先级：
- pid 文件（data/run/<name>.pid）：服务自己启动时写入（无论谁拉起）；
- 端口探活（NapCat 3001/6099）：外部拉起的兜底；
- omni-qq 连接探测：到 3001 的 ESTABLISHED 连接里非监听方的 python 进程。

停止一律进程树杀（Windows taskkill /T；POSIX killpg）——napcat.bat → node.exe、
uv run → python.exe 都是父子链。
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import platform
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from collections.abc import Callable

from ..config import DATA_DIR, PROJECT_ROOT

RUN_DIR = DATA_DIR / "run"
LOG_DIR = DATA_DIR / "system_logs"

OMNI_SERVICES = ("omni-qq", "omni-heartbeat")
ALL_SERVICES = ("napcat",) + OMNI_SERVICES

_SERVICE_LABELS = {
    "napcat": "NapCat（QQ 协议端）",
    "omni-qq": "QQ 通道（omni-qq）",
    "omni-heartbeat": "心跳调度（omni-heartbeat）",
}


class ServiceError(RuntimeError):
    """服务操作失败（未安装/已在运行/停止失败等，信息自包含）。"""


# ---------- 进程 / 端口工具 ----------


def _pid_alive(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    if platform.system() == "Windows":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return False
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def _port_listening(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=0.6):
            return True
    except OSError:
        return False


def _netstat_pids(port: int, state: str) -> list[int]:
    """按端口找 pid（Windows: netstat -ano；POSIX: lsof）。state: LISTEN/ESTABLISHED。

    注意：中文 Windows 的 netstat 输出是 GBK，必须按字节捕获后容错解码，
    否则读线程 UnicodeDecodeError 会让 stdout 变 None。
    """
    pids: list[int] = []
    try:
        if platform.system() == "Windows":
            raw = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"], capture_output=True, timeout=8
            ).stdout or b""
            out = raw.decode("utf-8", errors="replace")
            for line in out.splitlines():
                upper = line.upper()
                if f":{port} " in upper and state in upper:
                    parts = line.split()
                    if parts and parts[-1].isdigit():
                        pids.append(int(parts[-1]))
        else:
            raw = subprocess.run(
                ["lsof", "-sT", "-i", f":{port}"], capture_output=True, timeout=8
            ).stdout or b""
            out = raw.decode("utf-8", errors="replace")
            for line in out.splitlines():
                parts = line.split()
                if len(parts) > 1 and parts[1].isdigit():
                    pids.append(int(parts[1]))
    except (OSError, subprocess.SubprocessError):
        pass
    return list(dict.fromkeys(pids))


def _process_name(pid: int) -> str:
    try:
        if platform.system() == "Windows":
            raw = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                timeout=8,
            ).stdout or b""
            out = raw.decode("utf-8", errors="replace").strip()
            return out.split('","')[0].strip('"') if out else ""
        return Path(f"/proc/{pid}/comm").read_text().strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _tree_kill(pid: int) -> None:
    if platform.system() == "Windows":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, timeout=20
        )
    else:
        try:
            os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
        except OSError:
            pass


def _spawn(argv: list[str], cwd: Path, log_path: Path) -> int:
    """分离进程启动，stdout/stderr 落日志文件；返回 pid。"""
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    kwargs: dict[str, Any] = {}
    if platform.system() == "Windows":
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED | NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log_file:
        proc = subprocess.Popen(
            argv, cwd=str(cwd), stdout=log_file, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, env=env, shell=False, **kwargs,
        )
    return proc.pid


# ---------- 服务管理器 ----------


class ServiceManager:
    def __init__(
        self,
        napcat_dir: Path | None = None,
        *,
        napcat_ws_port: int = 3001,
        napcat_webui_port: int = 6099,
        run_dir: Path | None = None,
        log_dir: Path | None = None,
        port_prober: Callable[[int, str], list[int]] | None = None,
        killer: Callable[[int], None] | None = None,
        spawner: Callable[[list[str], Path, Path], int] | None = None,
    ) -> None:
        self._napcat_dir = Path(napcat_dir) if napcat_dir else None
        self._ws_port = napcat_ws_port
        self._webui_port = napcat_webui_port
        # 探测/终止/启动都可替换：隔离实例必须给出"不碰真机进程"的实现，
        # 否则 `stop()` 的端口回退会把用户正在跑的真服务杀掉
        self._port_pids = port_prober or _netstat_pids
        self._kill = killer or _tree_kill
        self._spawn = spawner or _spawn
        # pid/日志目录可注入：测试必须与真机的 data/run 隔离，
        # 否则"未安装"类测试会读到真实 pid，甚至把真服务当测试残留杀掉
        self._run_dir = Path(run_dir) if run_dir else RUN_DIR
        self._log_dir = Path(log_dir) if log_dir else LOG_DIR
        self._run_dir.mkdir(parents=True, exist_ok=True)
        self._log_dir.mkdir(parents=True, exist_ok=True)

    # ---------- NapCat ----------

    @property
    def napcat_dir(self) -> Path | None:
        if self._napcat_dir and (self._napcat_dir / "napcat.bat").exists():
            return self._napcat_dir
        return None

    def _napcat_pid(self) -> int | None:
        for pid in self._port_pids(self._ws_port, "LISTEN") or self._port_pids(
            self._webui_port, "LISTEN"
        ):
            return pid
        return None

    def napcat_start(self, qq: str = "") -> dict[str, Any]:
        napcat_dir = self.napcat_dir
        if napcat_dir is None:
            raise ServiceError("未找到 NapCat（检查 config.yaml system.napcat_dir 指向 napcat.bat 所在目录）")
        if self._napcat_pid():
            raise ServiceError("NapCat 已在运行")
        argv = ["cmd", "/c", str(napcat_dir / "napcat.bat")]
        if qq.strip():
            argv += ["-q", qq.strip()]
        pid = self._spawn(argv, cwd=napcat_dir, log_path=self._log_dir / "napcat.log")
        self._write_pid("napcat", pid)
        return {"name": "napcat", "pid": pid, "started": True}

    def napcat_stop(self) -> dict[str, Any]:
        pid = self._read_pid("napcat") or self._napcat_pid()
        if not pid or not _pid_alive(pid):
            self._clear_pid("napcat")
            raise ServiceError("NapCat 未在运行")
        self._kill(pid)
        self._clear_pid("napcat")
        return {"name": "napcat", "stopped": True}

    def napcat_qr(self) -> dict[str, Any]:
        """扫码登录所需：二维码图片（base64）+ 登录状态。"""
        logged_in = bool(self._napcat_uin()) and _port_listening(self._ws_port)
        payload: dict[str, Any] = {"logged_in": logged_in, "available": False,
                                   "image": "", "uin": self._napcat_uin()}
        napcat_dir = self.napcat_dir
        if napcat_dir is None:
            return payload
        qr = napcat_dir / "napcat" / "cache" / "qrcode.png"
        if qr.exists():
            try:
                payload["image"] = "data:image/png;base64," + base64.b64encode(
                    qr.read_bytes()
                ).decode()
                payload["available"] = True
                payload["modified"] = qr.stat().st_mtime
            except OSError:
                pass
        return payload

    def _napcat_uin(self) -> str | None:
        napcat_dir = self.napcat_dir
        if napcat_dir is None:
            return None
        for cfg in (napcat_dir / "napcat" / "config").glob("onebot11_*.json"):
            uin = cfg.stem.removeprefix("onebot11_")
            if uin.isdigit():
                return uin
        return None

    # ---------- OmniOperator 常驻服务 ----------

    def _omni_argv(self, name: str) -> tuple[list[str], Path]:
        if name not in OMNI_SERVICES:
            raise ServiceError(f"未知服务 {name}")
        script = PROJECT_ROOT / ".venv" / ("Scripts" if platform.system() == "Windows" else "bin") / name
        if script.exists():
            return [str(script)], PROJECT_ROOT
        return ["uv", "run", name], PROJECT_ROOT

    def omni_start(self, name: str) -> dict[str, Any]:
        if self._omni_pid(name):
            raise ServiceError(f"{name} 已在运行")
        argv, cwd = self._omni_argv(name)
        pid = self._spawn(argv, cwd=cwd, log_path=self._log_dir / f"{name}.log")
        self._write_pid(name, pid)
        return {"name": name, "pid": pid, "started": True}

    def omni_stop(self, name: str) -> dict[str, Any]:
        pid = self._read_pid(name)
        if not pid or not _pid_alive(pid):
            self._clear_pid(name)
            raise ServiceError(f"{name} 未在运行")
        self._kill(pid)
        self._clear_pid(name)
        return {"name": name, "stopped": True}

    def _omni_pid(self, name: str) -> int | None:
        pid = self._read_pid(name)
        if pid and _pid_alive(pid):
            return pid
        if name != "omni-qq":
            return None  # netstat 兜底只对 WS 客户端 omni-qq 有意义
        # 兜底：到 NapCat WS 端口的 ESTABLISHED 连接里找 python 进程（omni-qq）
        listeners = set(self._port_pids(self._ws_port, "LISTEN"))
        for pid in self._port_pids(self._ws_port, "ESTABLISHED"):
            if pid in listeners or pid == os.getpid():
                continue
            if "python" in _process_name(pid).lower():
                return pid
        return None

    # ---------- 统一入口 ----------

    def status(self, name: str) -> dict[str, Any]:
        if name == "napcat":
            pid = self._read_pid("napcat") or self._napcat_pid()
            running = (pid is not None and _pid_alive(pid)) or bool(self._napcat_pid())
            detail = f"已登录 {self._napcat_uin()}" if self._napcat_uin() else "未登录（需扫码）"
            return {"name": name, "label": _SERVICE_LABELS[name], "running": running,
                    "pid": pid, "detail": detail}
        pid = self._omni_pid(name)
        return {"name": name, "label": _SERVICE_LABELS[name],
                "running": pid is not None, "pid": pid, "detail": ""}

    def overview(self) -> dict[str, Any]:
        services = [self.status(n) for n in ALL_SERVICES]
        qr = self.napcat_qr()
        return {
            "services": services,
            "napcat": {"installed": self.napcat_dir is not None,
                       "logged_in": qr["logged_in"], "uin": qr["uin"],
                       "qr_available": qr["available"]},
            "web": {"running": True, "pid": os.getpid()},  # 工作台自己
        }

    def start(self, name: str, qq: str = "") -> dict[str, Any]:
        if name == "napcat":
            return self.napcat_start(qq)
        return self.omni_start(name)

    def stop(self, name: str) -> dict[str, Any]:
        if name == "napcat":
            return self.napcat_stop()
        return self.omni_stop(name)

    def log_tail(self, name: str, lines: int = 80) -> str:
        path = self._log_dir / f"{name}.log"
        if not path.exists():
            return f"（{name} 暂无工作台侧日志；外部启动的进程日志见其自身终端）"
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "（日志读取失败）"
        return "\n".join(content.splitlines()[-lines:])

    # ---------- pid 文件 ----------

    def _pid_path(self, name: str) -> Path:
        return self._run_dir / f"{name}.pid"

    def _read_pid(self, name: str) -> int | None:
        try:
            return int(self._pid_path(name).read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None

    def _write_pid(self, name: str, pid: int) -> None:
        self._pid_path(name).write_text(str(pid), encoding="utf-8")

    def _clear_pid(self, name: str) -> None:
        self._pid_path(name).unlink(missing_ok=True)
