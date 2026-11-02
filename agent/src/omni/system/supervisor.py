"""服务守护：让常驻服务在 agent 运行期间保持在线。

在此之前，服务只能靠工作台按钮手动拉起（`bootstrap` 只构造 `ServiceManager`，从不 start），
进程一死就再也不会回来。这里补上"谁负责让它们在跑"这件事：

每 `supervise_interval_s` 收一轮 → 期望状态由配置推导 → 不在跑就拉起 → 失败按指数退避 →
连续失败到上限就停止重试并保留告警。记账落 `data/run/supervisor.json`（易失的机器本地状态，
不进 `state.db`，也不进备份）。

职责：期望状态推导、拉起与重启、退避与上限、单实例互斥、记账。
明确不做：不实现进程操作（`ServiceManager` 的事）、不写配置（只热读最新策略）、
不终止身份不可核验的进程（沿用 `ServiceManager` 的边界）、不做开机自启。
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import load_config
from .atomic import atomic_write_text
from .filelock import FileLock, LockTimeout
from .services import ALL_SERVICES, ServiceError, ServiceManager

SUPERVISOR_VERSION = 1
DEFAULT_INTERVAL_S = 10.0
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_BACKOFF_CAP_S = 300
BACKOFF_BASE_S = 5.0
# 连续稳定运行到这个时长才把失败计数清零：刚起来就崩不该被当成"恢复正常"
STABLE_RESET_S = 300.0


@dataclass(frozen=True)
class Policy:
    """一轮守护要用到的全部策略；每轮从磁盘热读，改设置不用重启工作台。"""

    autostart: bool = True
    keep_running_after_exit: bool = True
    interval_s: float = DEFAULT_INTERVAL_S
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    backoff_cap_s: int = DEFAULT_BACKOFF_CAP_S
    health_timeout_s: int = 45
    napcat_qq: str = ""
    qq_enabled: bool = False
    napcat_available: bool = False

    @classmethod
    def from_config(cls, config: Any, *, napcat_available: bool, detected_uin: str | None = None) -> Policy:
        system = config.system
        # NapCat 的 QQ 号优先级：显式配置 → 所有者 QQ → 上次登录留下的 onebot11_*.json
        explicit = str(getattr(system, "napcat_qq", "") or "").strip()
        owner = getattr(config.qq, "owner_qq", 0) or 0
        qq = explicit or (str(owner) if owner else "") or (detected_uin or "")
        return cls(
            autostart=bool(getattr(system, "autostart", True)),
            keep_running_after_exit=bool(getattr(system, "keep_running_after_exit", True)),
            interval_s=max(2.0, float(getattr(system, "supervise_interval_s", DEFAULT_INTERVAL_S))),
            max_attempts=max(0, int(getattr(system, "restart_max_attempts", DEFAULT_MAX_ATTEMPTS))),
            backoff_cap_s=max(10, int(getattr(system, "restart_backoff_cap_s", DEFAULT_BACKOFF_CAP_S))),
            health_timeout_s=max(15, int(getattr(system, "health_timeout_s", 45))),
            napcat_qq=qq,
            qq_enabled=bool(getattr(config.qq, "enabled", False)),
            napcat_available=napcat_available,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "autostart": self.autostart,
            "keep_running_after_exit": self.keep_running_after_exit,
            "interval_s": self.interval_s,
            "max_attempts": self.max_attempts,
            "backoff_cap_s": self.backoff_cap_s,
            "health_timeout_s": self.health_timeout_s,
            "napcat_qq": self.napcat_qq,
            "qq_enabled": self.qq_enabled,
            "napcat_available": self.napcat_available,
        }


def desired_state(name: str, policy: Policy) -> tuple[str, str]:
    """(期望状态, 不启动的原因)。原因非空即表示"这个不是故障，是本来就不该起"。"""
    if not policy.autostart:
        return "stopped", "自动启动已关闭"
    if policy.max_attempts <= 0:
        # 上限为 0 就是"别自动拉起"：留一个显式出口，免得写成"连续 0 次启动失败"
        return "stopped", "自动拉起已关闭（restart_max_attempts=0）"
    if name == "napcat":
        if not policy.napcat_available:
            return "stopped", "未找到 napcat.bat（检查 system.napcat_dir）"
        return "running", ""
    if name == "omni-qq":
        if not policy.qq_enabled:
            # qq.enabled=false 时 omni-qq 启动即 SystemExit；照着拉会变成"起-退-起"死循环
            return "stopped", "qq.enabled=false"
        return "running", ""
    if name == "omni-heartbeat":
        return "running", ""
    return "stopped", "未知服务"


def backoff_delay(attempts: int, cap_s: int, *, base_s: float = BACKOFF_BASE_S) -> float:
    """第 n 次失败后等多久（5、10、20…，封顶 cap）。"""
    if attempts <= 0:
        return 0.0
    return float(min(base_s * (2 ** (attempts - 1)), float(cap_s)))


@dataclass
class _Entry:
    desired: str = "stopped"
    skipped: str = ""
    attempts: int = 0
    next_retry_at: float = 0.0
    restarts_total: int = 0
    running_since: float = 0.0
    last_exit: dict[str, Any] = field(default_factory=dict)
    last_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "desired": self.desired,
            "skipped": self.skipped,
            "attempts": self.attempts,
            "next_retry_at": self.next_retry_at,
            "restarts_total": self.restarts_total,
            "running_since": self.running_since,
            "last_exit": self.last_exit,
            "last_error": self.last_error,
        }


class ServiceSupervisor:
    """工作台进程内的服务守护者；同一时刻只允许一个实例真的动手（文件锁）。"""

    def __init__(
        self,
        services: ServiceManager,
        *,
        run_dir: Path,
        config_path: Path | None = None,
        enabled: bool = True,
        clock=time.time,
        lock_timeout_s: float = 0.05,
    ) -> None:
        self._services = services
        self._run_dir = Path(run_dir)
        self._config_path = Path(config_path) if config_path else None
        self._enabled = enabled
        self._clock = clock
        self._lock_timeout_s = lock_timeout_s
        self._entries: dict[str, _Entry] = {name: _Entry() for name in ALL_SERVICES}
        self._policy: Policy | None = None
        self._mode = "idle"  # idle | active | standby | disabled
        self._holder = ""
        self._last_error = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock: FileLock | None = None
        self._mutex = threading.RLock()
        # 一轮只允许一个执行者：守护线程和 API 的"立即收一轮"撞在一起会重复 spawn
        self._run_lock = threading.Lock()

    # ---------------------------------------------------------------- 生命周期

    def start(self) -> dict[str, Any]:
        """幂等；拿不到守护锁就退成 standby（只读，不动任何进程）。"""
        with self._mutex:
            if not self._enabled:
                self._mode = "disabled"
                return {"supervised": False, "mode": "disabled", "reason": "隔离实例或已禁用，不管理本机服务"}
            if self._thread is not None and self._thread.is_alive():
                return {"supervised": self._mode == "active", "mode": self._mode, "already": True}
            self._run_dir.mkdir(parents=True, exist_ok=True)
            lock = FileLock(
                self._run_dir / "supervisor.lock", timeout_s=self._lock_timeout_s, owner=f"workbench pid:{os.getpid()}"
            )
            try:
                lock.acquire()
            except LockTimeout:
                self._mode = "standby"
                self._holder = lock.holder()
                return {"supervised": False, "mode": "standby", "holder": self._holder}
            self._lock = lock
            self._mode = "active"
            self._holder = f"pid:{os.getpid()}"
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="service-supervisor", daemon=True)
            self._thread.start()
            return {"supervised": True, "mode": "active"}

    def stop(self, *, stop_services: bool = False, wait_s: float = 3.0) -> dict[str, Any]:
        """停守护线程；`stop_services` 为真时再请求服务退出（协作式，最多等 wait_s）。"""
        with self._mutex:
            self._stop.set()
            thread = self._thread
        if thread is not None:
            thread.join(timeout=max(0.5, float(wait_s)))
        requested: list[str] = []
        errors: list[str] = []
        if stop_services:
            per_service = max(0.5, float(wait_s) / max(1, len(ALL_SERVICES)))
            for name in ALL_SERVICES:
                try:
                    self._services.request_stop(name, wait_s=per_service)
                    requested.append(name)
                except ServiceError as exc:
                    if "未在运行" not in str(exc):
                        errors.append(f"{name}：{exc}")
        with self._mutex:
            self._thread = None
            self._mode = "idle" if self._enabled else "disabled"
            lock, self._lock = self._lock, None
        if lock is not None:
            lock.release()
        return {"mode": self._mode, "requested": requested, "errors": errors}

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.reconcile_once()
            except Exception as exc:  # noqa: BLE001 —— 单轮失败不能杀掉守护线程
                with self._mutex:
                    self._last_error = f"守护轮次失败：{type(exc).__name__}: {exc}"
            interval = self._policy.interval_s if self._policy else DEFAULT_INTERVAL_S
            self._stop.wait(interval)

    # ---------------------------------------------------------------- 单轮

    def reconcile_once(self) -> dict[str, Any]:
        """跑一轮：热读策略 → 逐个服务判定 → 该起的起 → 记账落盘。"""
        with self._run_lock:
            return self._reconcile_round()

    def _reconcile_round(self) -> dict[str, Any]:
        if getattr(self, "maintenance", lambda: False)():
            return self.snapshot()
        with self._mutex:
            if self._mode != "active":
                return self.snapshot()
        policy = self._load_policy()
        if policy is None:
            with self._mutex:
                # 读不动配置 ≠ 关掉守护：本轮不动任何进程，上一轮策略继续生效
                self._last_error = "配置读取失败，本轮未启动也未停止任何服务"
            return self.snapshot()
        with self._mutex:
            self._policy = policy
        for name in ALL_SERVICES:
            try:
                self._reconcile_service(name, policy)
            except Exception as exc:  # noqa: BLE001 —— 单个服务出错不拖垮其它服务
                with self._mutex:
                    self._entries[name].last_error = f"{type(exc).__name__}: {exc}"
        self._persist()
        return self.snapshot()

    def _reconcile_service(self, name: str, policy: Policy) -> None:
        desired, skipped = desired_state(name, policy)
        now = self._clock()
        entry = self._entries[name]
        entry.desired = desired
        entry.skipped = skipped
        if desired != "running":
            entry.next_retry_at = 0.0
            return
        status = self._services.status(name)
        if status.get("running"):
            if not entry.running_since:
                entry.running_since = now
            if entry.attempts and now - entry.running_since >= STABLE_RESET_S:
                entry.attempts = 0
            entry.next_retry_at = 0.0
            return
        # 没在跑：先看是否还在退避窗口
        entry.running_since = 0.0
        if entry.attempts >= policy.max_attempts:
            entry.next_retry_at = 0.0
            return  # 已达尝试上限，放弃重试；告警在 snapshot 里给
        if entry.next_retry_at and now < entry.next_retry_at:
            return  # 退避窗口内，先不重试
        if entry.attempts and not entry.next_retry_at:
            # 上一次尝试没能稳住（进程随后就没了）→ 先退避再重试。
            # 不这样退避的话，崩溃循环会每轮（默认 10s）重起一次：spawn 成功就把
            # attempts 清零，于是退避与 restart_max_attempts 都形同虚设，
            # restart_given_up 永远不会出现。
            entry.next_retry_at = now + backoff_delay(entry.attempts, policy.backoff_cap_s)
            return
        verdict = self._services.reconcile(name)
        if verdict.get("state") == "blocked_unverified":
            entry.skipped = "存在不可核验的旧进程，未接管"
            return
        try:
            self._services.start(name, qq=policy.napcat_qq if name == "napcat" else "")
        except ServiceError as exc:
            entry.attempts += 1
            entry.last_error = str(exc)
            entry.last_exit = {"at": now, "reason": "start_failed", "detail": str(exc)}
            entry.next_retry_at = now + backoff_delay(entry.attempts, policy.backoff_cap_s)
            return
        except Exception as exc:  # noqa: BLE001 —— 启动失败一律记账，不让线程退出
            entry.attempts += 1
            entry.last_error = f"{type(exc).__name__}: {exc}"
            entry.last_exit = {"at": now, "reason": "start_failed", "detail": str(exc)}
            entry.next_retry_at = now + backoff_delay(entry.attempts, policy.backoff_cap_s)
            return
        entry.restarts_total += 1
        # 本次尝试计入 attempts：spawn 成功不代表服务站得住，只有连续稳定跑满
        # STABLE_RESET_S 才清零（见上面的 running 分支）。
        entry.attempts += 1
        entry.next_retry_at = 0.0
        entry.running_since = now
        entry.last_error = ""

    # ---------------------------------------------------------------- 读取

    def _load_policy(self) -> Policy | None:
        try:
            config = load_config(self._config_path) if self._config_path else load_config()
        except Exception:  # noqa: BLE001 —— 文件正被写/被写坏都不能让守护停摆
            return None
        return Policy.from_config(
            config,
            napcat_available=self._services.napcat_dir is not None,
            detected_uin=self._services.napcat_uin(),
        )

    def refresh(self) -> dict[str, Any]:
        """外部改了策略后立刻收一轮，不用等下一个间隔。"""
        return self.reconcile_once()

    def keep_running_after_exit(self) -> bool:
        """关闭工作台时该不该连带停服务；取最近一轮热读到的策略（没有就用"继续跑"）。"""
        with self._mutex:
            if self._policy is not None:
                return self._policy.keep_running_after_exit
        return True

    def snapshot(self) -> dict[str, Any]:
        with self._mutex:
            now = self._clock()
            services = {}
            for name in ALL_SERVICES:
                entry = self._entries[name]
                services[name] = {**entry.to_dict(), "alert": self._alert_for(name, entry, now)}
            return {
                "supervised": self._mode == "active",
                "mode": self._mode,
                "holder": self._holder,
                "supervisor_pid": os.getpid(),
                "policy": self._policy.to_dict() if self._policy else None,
                "last_error": self._last_error,
                "services": services,
            }

    def _alert_for(self, name: str, entry: _Entry, now: float) -> dict[str, Any] | None:
        policy = self._policy
        if entry.desired != "running" or policy is None:
            return None
        if entry.attempts >= policy.max_attempts:
            return {"code": "restart_given_up", "text": f"{name} 连续 {entry.attempts} 次未能启动，已停止自动重启"}
        if entry.attempts and entry.next_retry_at > now:
            return {"code": "restart_backoff", "text": f"{name} 启动失败，{int(entry.next_retry_at - now)} 秒后重试"}
        return None

    def _persist(self) -> None:
        with self._mutex:
            payload = {
                "version": SUPERVISOR_VERSION,
                "supervisor_pid": os.getpid(),
                "mode": self._mode,
                "updated": self._clock(),
                "policy": self._policy.to_dict() if self._policy else None,
                "services": {name: self._entries[name].to_dict() for name in ALL_SERVICES},
            }
        try:
            atomic_write_text(self._run_dir / "supervisor.json", json.dumps(payload, ensure_ascii=False))
        except OSError:
            pass
