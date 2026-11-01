"""本地服务管理：NapCat 与 OmniOperator 常驻服务的 启动/停止/状态。

判定优先级：
- pid 文件（data/run/<name>.pid）：服务自己启动时写入（无论谁拉起）；
- 端口探活（NapCat 3001/6099）：外部拉起的兜底；
- omni-qq 连接探测：到 3001 的 ESTABLISHED 连接里非监听方的 python 进程。

停止只接受完整启动记录，核验创建时间/路径后由保留句柄终止已观察的进程树。
旧裸 PID 与外部端口仅用于提示，不授权终止；启动/停止/登记按服务跨进程互斥。

本模块不做的事：不守护、不重启、不推导"应该在跑"（那是 `system/supervisor.py` 的职责）；
`reconcile()` 只收敛**记录**——进程已死、PID 被复用、文件损坏才清，活着但身份不可核验的
一律标记 `blocked_unverified` 留着，交给人判断。
"""
from __future__ import annotations

import base64
import json
import os
import platform
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..config import DATA_DIR, PROJECT_ROOT
from .atomic import atomic_write_text
from .filelock import FileLock
from .process_identity import (
    ProcessIdentity,
    ProcessIdentityError,
    confirmed_exited,
    inspect_process,
    terminate_verified_tree,
)
from .provenance import source_fingerprint
from .service_state import read_state, state_path

RUN_DIR = DATA_DIR / "run"
LOG_DIR = DATA_DIR / "system_logs"

OMNI_SERVICES = ("omni-qq", "omni-heartbeat")
ALL_SERVICES = ("napcat",) + OMNI_SERVICES

_SERVICE_LABELS = {
    "napcat": "NapCat（QQ 协议端）",
    "omni-qq": "QQ 通道（omni-qq）",
    "omni-heartbeat": "心跳调度（omni-heartbeat）",
}

# 旧版裸 PID 记录只存了一个数字，无法与进程身份核对；清理前用它判断
# "这个 pid 现在还是不是我们那类进程"。镜像名对不上就是 PID 复用，记录已无意义。
_PYTHON_IMAGES = frozenset({"python.exe", "pythonw.exe", "python", "python3"})
_NAPCAT_IMAGES = frozenset({"cmd.exe", "node.exe", "napcat.exe", "napcat.bat"})


def _expected_images(name: str) -> frozenset[str]:
    return _PYTHON_IMAGES if name in OMNI_SERVICES else _NAPCAT_IMAGES


# 对外只暴露五态：记录可信 / 记录失效（进程没了或 PID 被复用）/ 无记录 / 文件损坏 / 活着的不可核验
_PUBLIC_STATE = {
    "legacy_dead": "stale",
    "legacy_reused": "stale",
    "legacy_blocked": "blocked_unverified",
}



class ServiceError(RuntimeError):
    """服务操作失败（未安装/已在运行/停止失败等，信息自包含）。

    `uncertain=True` 表示操作可能已部分生效（进程可能已启动、停止结果未确认），
    消费方必须按"副作用未知"处理；缺省 False 表示确定没有改变任何状态。
    """

    def __init__(self, message: str, *, uncertain: bool = False) -> None:
        super().__init__(message)
        self.uncertain = uncertain


# ---------- 进程 / 端口工具 ----------


def _pid_alive(pid: int) -> bool:
    return inspect_process(pid) is not None


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
                ["netstat", "-ano", "-p", "tcp"], capture_output=True, timeout=8, check=False
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
                ["lsof", "-sT", "-i", f":{port}"], capture_output=True, timeout=8, check=False
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
                check=False,
            ).stdout or b""
            out = raw.decode("utf-8", errors="replace").strip()
            return out.split('","')[0].strip('"') if out else ""
        return Path(f"/proc/{pid}/comm").read_text().strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _spawn(argv: list[str], cwd: Path, log_path: Path, *, environment: dict[str,str] | None = None) -> int:
    """分离进程启动，stdout/stderr 落日志文件；返回 pid。"""
    env = dict(os.environ)
    if environment:env.update(environment)
    env.setdefault("PYTHONUTF8", "1")
    bounded_python = any(part in ('omni.channels.qq', 'omni.channels.heartbeat') for part in argv)
    if bounded_python:
        env['OMNI_SERVICE_LOG'] = str(log_path.resolve())
    kwargs: dict[str, Any] = {}
    if platform.system() == "Windows":
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED | NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if bounded_python:
        # 子进程自行开关/轮转日志；父进程不传递长期占用该文件的 Windows 句柄。
        return subprocess.Popen(argv, cwd=str(cwd), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL, env=env, shell=False, **kwargs).pid
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
        killer: Callable[[ProcessIdentity], None] | None = None,
        spawner: Callable[[list[str], Path, Path], int] | None = None,
        inspector: Callable[[int], ProcessIdentity | None] | None = None,
        tcp_probe: Callable[[int], bool] | None = None,
        stop_wait_s: float = 12,
        health_timeout_s: float = 45,
        exit_checker: Callable[[ProcessIdentity], bool] | None = None,
        environment: dict[str,str] | None = None,
    ) -> None:
        self._napcat_dir = Path(napcat_dir) if napcat_dir else None
        self._ws_port = napcat_ws_port
        self._webui_port = napcat_webui_port
        # 探测/终止/启动都可替换：隔离实例必须给出"不碰真机进程"的实现，
        # 否则 `stop()` 的端口回退会把用户正在跑的真服务杀掉
        self._port_pids = port_prober or _netstat_pids
        self._kill = killer or terminate_verified_tree
        self._inspect = inspector or inspect_process
        self._exited = exit_checker or (confirmed_exited if inspector is None else lambda identity: inspector(identity.pid) != identity)
        self._tcp_probe = tcp_probe or _port_listening
        self._stop_wait_s = max(0, float(stop_wait_s))
        self._health_timeout_s = max(1.0, float(health_timeout_s))
        from functools import partial
        self._spawn = spawner or (partial(_spawn,environment=environment) if environment else _spawn)
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
        with self._lock("napcat"):
            self._check_start("napcat")
            if self._napcat_pid():
                raise ServiceError("NapCat 端口已有外部进程；未接管其停止权限")
            argv = [str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/cmd.exe"), "/c", str(napcat_dir / "napcat.bat")]
            if qq.strip():
                if not qq.strip().isdigit() or len(qq.strip()) > 12:
                    raise ServiceError("QQ 号必须为数字")
                argv += ["-q", qq.strip()]
            pid = self._spawn(argv, cwd=napcat_dir, log_path=self._log_dir / "napcat.log")
            self._write_pid("napcat", pid)
            return {"name": "napcat", "pid": pid, "started": True}

    def napcat_stop(self) -> dict[str, Any]:
        return self._stop_verified("napcat")

    def napcat_status(self) -> dict[str, Any]:
        """登录态摘要（不含二维码图片）：状态条每次轮询都要它，不能带 base64。"""
        uin = self._napcat_uin()
        return {"installed": self.napcat_dir is not None,
                "logged_in": bool(uin) and self._tcp_probe(self._ws_port),
                "uin": uin}

    def napcat_qr(self) -> dict[str, Any]:
        """扫码登录所需：二维码图片（base64）+ 登录状态。"""
        summary = self.napcat_status()
        payload: dict[str, Any] = {"logged_in": summary["logged_in"], "available": False,
                                   "image": "", "uin": summary["uin"]}
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

    def napcat_uin(self) -> str | None:
        """上次登录留下的 QQ 号（按 onebot11_*.json 文件名推断）；没登录过返回 None。"""
        return self._napcat_uin()

    # ---------- OmniOperator 常驻服务 ----------

    def _omni_argv(self, name: str) -> tuple[list[str], Path]:
        if name not in OMNI_SERVICES:
            raise ServiceError(f"未知服务 {name}")
        module = "omni.channels.qq" if name == "omni-qq" else "omni.channels.heartbeat"
        return [sys.executable, "-m", module], PROJECT_ROOT

    def omni_start(self, name: str) -> dict[str, Any]:
        with self._lock(name):
            self._check_start(name)
            argv, cwd = self._omni_argv(name)
            pid = self._spawn(argv, cwd=cwd, log_path=self._log_dir / f"{name}.log")
            self._write_pid(name, pid)
            return {"name": name, "pid": pid, "started": True}

    def omni_stop(self, name: str) -> dict[str, Any]:
        return self._stop_verified(name)

    def _omni_pid(self, name: str) -> int | None:
        pid = self._read_pid(name)
        if pid and self._inspect(pid):
            return pid
        return None

    # ---------- 统一入口 ----------

    def status(self, name: str) -> dict[str, Any]:
        self._validate_name(name)
        verdict = self._classify(name)
        verified = verdict["state"] == "verified"
        state = _PUBLIC_STATE.get(verdict["state"], verdict["state"])
        live = self._live_state(name, record_launch_id=self._launch_id(name))
        if name == "napcat":
            pid = self._read_pid("napcat") or self._napcat_pid()
            running = (pid is not None and self._inspect(pid) is not None) or bool(self._napcat_pid())
            detail = f"已登录 {self._napcat_uin()}" if self._napcat_uin() else "未登录（需扫码）"
            return {"name": name, "label": _SERVICE_LABELS[name], "running": running,
                    "pid": pid, "detail": detail + ("；身份未核验，不可停止" if running and not verified else ""),
                    "identity_verified": verified, "can_stop": verified, "state": state, **live}
        pid = self._omni_pid(name)
        return {"name": name, "label": _SERVICE_LABELS[name],
                "running": pid is not None, "pid": pid,
                "detail": "身份未核验，不可停止" if pid and not verified else "",
                "identity_verified": verified, "can_stop": verified, "state": state, **live}

    def _launch_id(self, name: str) -> str | None:
        record = self._read_record(name)
        return record["launch_id"] if record else None

    def _live_state(self, name: str, *, record_launch_id: str | None) -> dict[str, Any]:
        """服务自报的运行态快照；NapCat 是第三方进程，没有这份文件。"""
        if name not in OMNI_SERVICES:
            return {"stats": {}, "fresh": False, "health_reason": "not_applicable", "state_age_s": None}
        snap = read_state(state_path(self._run_dir, name), ttl_s=self._health_timeout_s,
                          launch_id=record_launch_id)
        return {"stats": snap["stats"], "fresh": snap["fresh"],
                "health_reason": snap["reason"], "state_age_s": snap["age_s"],
                "health": snap["health"], "note": snap["note"]}

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
        # 起之前先收敛旧记录：死进程/被复用的 PID 不构成"已在运行"，
        # 但活着的不可核验进程仍然拒绝接管（不猜、不杀）。
        self._validate_name(name)
        verdict = self.reconcile(name)
        if verdict["state"] == "blocked_unverified":
            raise ServiceError(
                f"{name} 存在不可核验的旧进程 pid={verdict['pid']}；未接管，"
                "请人工核对后清理该 PID 记录"
            )
        if name == "napcat":
            return self.napcat_start(qq)
        return self.omni_start(name)

    def stop(self, name: str) -> dict[str, Any]:
        if name == "napcat":
            return self.napcat_stop()
        return self.omni_stop(name)

    def request_stop(self, name: str, *, wait_s: float | None = None) -> dict[str, Any]:
        """只发出停止请求，不做长时间等待（关闭工作台有总预算）。

        omni-* 走协作式 `.stop` 文件：写完立刻返回，进程自己收尾；
        NapCat 是第三方进程，没有这条通道，只能核验身份后终止（用较短的等待预算）。
        """
        self._validate_name(name)
        if name not in OMNI_SERVICES:
            return self._stop_verified(name, wait_s=wait_s)
        with self._lock(name):
            record = self._read_record(name)
            identity = self._record_identity(record)
            if identity is None:
                raise ServiceError(f"{name} 未在运行或缺少可信启动记录；旧 PID/端口不授权终止")
            if self._inspect(identity.pid) != identity:
                raise ServiceError(f"{name} 进程身份不匹配或不可核验，未终止任何进程")
            atomic_write_text(self._pid_path(name).with_suffix(".stop"), record["launch_id"])
        return {"name": name, "requested": True, "graceful": True}

    def log_tail(self, name: str, lines: int = 80) -> str:
        self._validate_name(name)
        path = self._log_dir / f"{name}.log"
        if not path.exists():
            return f"（{name} 暂无工作台侧日志；外部启动的进程日志见其自身终端）"
        try:
            with path.open("rb") as handle:
                handle.seek(max(0, path.stat().st_size - 65536))
                content = handle.read(65536).decode("utf-8", errors="replace")
        except OSError:
            return "（日志读取失败）"
        return "\n".join(content.splitlines()[-max(1, min(int(lines), 500)):])

    # ---------- pid 文件 ----------

    def _pid_path(self, name: str) -> Path:
        self._validate_name(name)
        return self._run_dir / f"{name}.pid"

    def _read_pid(self, name: str) -> int | None:
        record = self._read_record(name)
        if record:
            return record["pid"]
        try:
            return int(self._pid_path(name).read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None

    @staticmethod
    def _validate_name(name: str) -> None:
        if name not in ALL_SERVICES:
            raise ServiceError(f"未知服务 {name}")

    def _lock(self, name: str) -> FileLock:
        return FileLock(self._pid_path(name).with_suffix(".lock"), timeout_s=5)

    def _read_record(self, name: str) -> dict | None:
        try:
            raw = self._pid_path(name).read_text(encoding="utf-8")
            if len(raw) > 8192:
                return None
            data = json.loads(raw)
            if (not isinstance(data, dict) or data.get("version") != 1 or data.get("service") != name or
                    not isinstance(data.get("launch_id"), str) or len(data["launch_id"]) != 32):
                return None
            return data if self._record_identity(data) is not None else None
        except (OSError, ValueError, TypeError):
            return None

    @staticmethod
    def _record_identity(record: dict | None) -> ProcessIdentity | None:
        if record is None:
            return None
        try:
            pid = record["pid"]
            created, executable = record["created"], record["executable"]
            if (type(pid) is not int or pid <= 0 or not isinstance(created, str) or not created.isdigit() or
                    not isinstance(executable, str) or not Path(executable).is_absolute()):
                return None
            return ProcessIdentity(pid, created, executable)
        except KeyError:
            return None

    def _write_pid(self, name: str, pid: int) -> dict:
        identity = self._inspect(pid)
        if identity is None:
            # 进程已经起了，但拿不到可信身份：它是活的、不受管理——状态已改变
            raise ServiceError(
                f"进程已启动但身份不可核验 pid={pid}；未登记可停止权限，请人工核查",
                uncertain=True,
            )
        record = {"version": 1, "service": name, "launch_id": uuid.uuid4().hex, **asdict(identity)}
        if name in OMNI_SERVICES:
            record['source_fingerprint'] = source_fingerprint()
            record['source_confirmed'] = pid == os.getpid()
        atomic_write_text(self._pid_path(name), json.dumps(record, ensure_ascii=False))
        return record

    # ---------- 记录收敛 ----------
    #
    # 历史包袱：早期版本只往 <name>.pid 写一个裸数字。裸 PID 无法核对身份，
    # 既不能拿来停止（会误杀复用了该 pid 的无关进程），也不该永远堵住启动。
    # `_classify` 只读判定，`reconcile` 才动文件，且只在语义确定时清。

    def _classify(self, name: str) -> dict[str, Any]:
        """只读判定 pid 记录的归属；不改任何文件（status 也用它）。"""
        path = self._pid_path(name)
        record = self._read_record(name)
        if record is not None:
            identity = self._record_identity(record)
            if identity is not None and self._inspect(identity.pid) == identity:
                return {"state": "verified", "pid": identity.pid,
                        "image": Path(identity.executable).name, "detail": "启动记录与进程身份一致"}
            return {"state": "stale", "pid": record.get("pid"), "image": "",
                    "detail": "启动记录指向的进程已退出或身份已变化"}
        if not path.exists():
            return {"state": "absent", "pid": None, "image": "", "detail": "无启动记录"}
        legacy = self._legacy_pid(name)
        if legacy is None:
            return {"state": "corrupt", "pid": None, "image": "",
                    "detail": "pid 文件无法解析（既不是记录也不是数字）"}
        identity = self._inspect(legacy)
        if identity is None:
            return {"state": "legacy_dead", "pid": legacy, "image": "",
                    "detail": "旧版裸 PID 指向的进程已不存在"}
        image = Path(identity.executable).name.lower()
        if image and image not in _expected_images(name):
            return {"state": "legacy_reused", "pid": legacy, "image": image,
                    "detail": f"旧版裸 PID 已被其他进程复用（{image}）"}
        return {"state": "legacy_blocked", "pid": legacy, "image": image,
                "detail": f"存在不可核验的旧进程 pid={legacy}（{image or '镜像未知'}），未接管"}

    def reconcile(self, name: str) -> dict[str, Any]:
        """收敛启动记录；返回 {name, state, pid, detail}。

        `verified` / `absent` / `blocked_unverified` 不动文件；`cleared_dead` /
        `cleared_reused` 删除记录；`quarantined` 把损坏文件移进 run/quarantine 留证。
        **任何分支都不会终止进程**——记录不可信时停不授权，这是既有边界。
        """
        self._validate_name(name)
        verdict = self._classify(name)
        if verdict["state"] in ("verified", "absent", "legacy_blocked"):
            if verdict["state"] == "legacy_blocked":
                return {"name": name, "state": "blocked_unverified",
                        "pid": verdict["pid"], "detail": verdict["detail"]}
            return {"name": name, "state": verdict["state"], "pid": verdict["pid"],
                    "detail": verdict["detail"]}
        with self._lock(name):
            # 拿锁期间记录可能被别的进程改写：以锁内的判定为准，避免删掉刚写好的新记录
            verdict = self._classify(name)
            state = verdict["state"]
            if state in ("verified", "absent"):
                return {"name": name, "state": state, "pid": verdict["pid"],
                        "detail": verdict["detail"]}
            if state == "legacy_blocked":
                return {"name": name, "state": "blocked_unverified",
                        "pid": verdict["pid"], "detail": verdict["detail"]}
            if state == "corrupt":
                target = self._quarantine(name)
                return {"name": name, "state": "quarantined", "pid": None,
                        "detail": f"无法解析的 pid 文件已移到 {target.name}"}
            self._pid_path(name).unlink(missing_ok=True)
            return {"name": name, "state": "cleared_reused" if state == "legacy_reused" else "cleared_dead",
                    "pid": verdict["pid"], "detail": verdict["detail"]}

    def _legacy_pid(self, name: str) -> int | None:
        """只认裸数字；合法记录由 `_read_record` 负责，损坏文件返回 None。"""
        try:
            return int(self._pid_path(name).read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None

    def _quarantine(self, name: str) -> Path:
        target_dir = self._run_dir / "quarantine"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{name}.pid.{int(time.time() * 1000)}.txt"
        self._pid_path(name).replace(target)
        return target

    def drop_record(self, name: str, *, confirm: bool = False) -> dict[str, Any]:
        """删掉启动记录文件（不碰进程）。

        只给"那个旧进程我已经自己处理了"这种人工确认过的场景用：记录不可核验时
        我们既不能拿它停止（可能误杀），也不该让它永远堵住启动。
        """
        self._validate_name(name)
        if not confirm:
            raise ServiceError("删除启动记录需要显式确认（confirm=true）")
        verdict = self._classify(name)
        if verdict["state"] == "verified":
            raise ServiceError(f"{name} 的启动记录可信（进程在运行），拒绝删除；请先正常停止")
        with self._lock(name):
            path = self._pid_path(name)
            existed = path.exists()
            path.unlink(missing_ok=True)
        return {"name": name, "state": "dropped", "pid": verdict["pid"], "existed": existed,
                "detail": "启动记录已删除；未结束任何进程"}

    def _check_start(self, name: str) -> None:
        pid = self._read_pid(name)
        if pid and self._inspect(pid):
            raise ServiceError(f"{name} 记录指向运行中的进程，请先核验并停止")
        if self._pid_path(name).exists() and self._read_record(name) is None:
            raise ServiceError(f"{name} 是旧版/损坏 PID 记录，需人工核对后清理；不会自动接管")

    def _stop_verified(self, name: str, *, wait_s: float | None = None) -> dict[str, Any]:
        wait = self._stop_wait_s if wait_s is None else max(0.0, float(wait_s))
        with self._lock(name):
            record = self._read_record(name)
            identity = self._record_identity(record)
            if identity is None:
                raise ServiceError(f"{name} 未在运行或缺少可信启动记录；旧 PID/端口不授权终止")
            if self._inspect(identity.pid) != identity:
                raise ServiceError(f"{name} 进程身份不匹配或不可核验，未终止任何进程")
            if name in OMNI_SERVICES:
                atomic_write_text(self._pid_path(name).with_suffix(".stop"), record["launch_id"])
        # 不持服务文件锁等待退出：入口清理需要同一把锁。
        if name in OMNI_SERVICES:
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline:
                if self._exited(identity):
                    with self._lock(name):
                        self._clear_pid(name, expected=record["launch_id"])
                    return {"name": name, "stopped": True, "graceful": True}
                time.sleep(0.05)
        with self._lock(name):
            current = self._read_record(name)
            if current is None or current["launch_id"] != record["launch_id"]:
                if self._exited(identity):
                    return {"name": name, "stopped": True, "graceful": True}
                raise ServiceError("启动记录已变化，未执行强制停止")
            if self._inspect(identity.pid) != identity:
                raise ServiceError("停止等待期间进程身份变化，未执行强制停止")
            try:
                self._kill(identity)
            except (ProcessIdentityError, OSError) as exc:
                # 终止信号已发出/发出失败都无法确认最终状态
                raise ServiceError(f"{name} 停止未确认：{exc}", uncertain=True) from exc
            if not self._exited(identity):
                # 强停信号已发但进程仍存活或无法确认退出
                raise ServiceError(
                    f"{name} 停止后仍存活或无法确认退出，保留启动记录", uncertain=True
                )
            self._clear_pid(name, expected=record["launch_id"])
            return {"name": name, "stopped": True, "graceful": False}

    def stop_requested(self, name: str, launch_id: str) -> bool:
        try:
            return self._pid_path(name).with_suffix(".stop").read_text(encoding="utf-8") == launch_id
        except OSError:
            return False

    def register_current(self, name: str) -> str:
        """入口自行登记当前身份；复用管理器已写的启动记录，不能覆盖另一个活进程。"""
        with self._lock(name):
            record = self._read_record(name)
            identity = self._inspect(os.getpid())
            if identity is not None and self._record_identity(record) == identity:
                if name in OMNI_SERVICES:
                    current_source = source_fingerprint()
                    if record.get('source_fingerprint') != current_source:
                        raise ServiceError('启动期间源码变化，请固定版本后重新启动；不登记验收身份')
                    record['source_confirmed'] = True
                    atomic_write_text(self._pid_path(name), json.dumps(record, ensure_ascii=False))
                return record["launch_id"]
            self._check_start(name)
            return self._write_pid(name, os.getpid())["launch_id"]

    def unregister_current(self, name: str, launch_id: str) -> None:
        with self._lock(name):
            self._clear_pid(name, expected=launch_id)

    def _clear_pid(self, name: str, *, expected: str) -> None:
        record = self._read_record(name)
        if record and record["launch_id"] == expected:
            self._pid_path(name).unlink(missing_ok=True)
        stop_path = self._pid_path(name).with_suffix(".stop")
        try:
            if stop_path.read_text(encoding="utf-8") == expected:
                stop_path.unlink(missing_ok=True)
        except OSError:
            pass
