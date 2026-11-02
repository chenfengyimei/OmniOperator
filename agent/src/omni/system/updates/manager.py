"""更新应用服务：缓存、预检和停写协调；实际替换交给独立冻结执行器。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
import weakref
from dataclasses import asdict
from pathlib import Path

from ...config import Updates, load_config
from ..config_io import commit_config_updates
from ..filelock import FileLock, LockTimeout
from ..process_identity import inspect_process, process_parents
from .archive import extract, verify
from .common import (
    OP_ID,
    REPOSITORY,
    TERMINAL,
    UpdateError,
    digest,
    error_message,
    git,
    no_links,
    read_json,
    write_json,
)
from .gate import MaintenanceGate
from .gitee import GiteeClient
from .version import identify, inventory, semver


class UpdateManager:
    def __init__(
        self,
        *,
        root: Path,
        control: Path,
        config_path: Path,
        enabled: bool,
        client=None,
        clock=time.time,
        drain_timeout: float = 120,
    ):
        self.root, self.control = Path(root).resolve(), no_links(control)
        self.config_path, self.enabled = config_path, enabled
        self.probe_mode = bool(enabled and os.environ.get("OMNI_UPDATE_PROBE"))
        self.client, self.clock = client or GiteeClient(), clock
        self.drain_timeout = drain_timeout
        self.gate = MaintenanceGate(self.control)
        weakref.finalize(self, self.gate.leave)
        self.paths = None
        self.directories = None
        self.services = None
        self._busy_probe = lambda: 0
        self._running = identify(self.root) if enabled else None
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.shutdown = None
        self.terminal_busy = lambda: 0

    def policy(self) -> dict:
        return load_config(self.config_path).updates.model_dump()

    def save_policy(self, data: dict) -> dict:
        policy = Updates.model_validate(data)
        with FileLock(self.control / "prepare.lock"):
            previous = self.policy()
            commit_config_updates(self.config_path, {"updates": policy.model_dump()})
            if previous["channel"] != policy.channel:
                last = read_json(self.control / "last-operation.json", {})
                if last:
                    state = self.operation(last["id"])
                    if state["phase"] in {"preparing", "ready"}:
                        self._state(
                            self.control / "operations" / last["id"], "cancelled", message="更新通道已切换，请重新检查"
                        )
        return self.policy()

    def version(self) -> dict:
        try:
            disk = identify(self.root)
            running = self._running or disk
            return {
                **running,
                "disk_changed": running["fingerprint"] != disk["fingerprint"],
                "disk_version": disk["version"],
                "starting": self.probe_mode,
                "automatic_install": self.enabled
                and os.name == "nt"
                and not self.probe_mode
                and self.shutdown is not None,
            }
        except (OSError, UpdateError) as exc:
            return {
                "version": (self._running or {}).get("version", "未知"),
                "installation": "unknown",
                "commit": "",
                "dirty": [],
                "disk_changed": True,
                "automatic_install": False,
                "error_code": getattr(exc, "code", "source_unavailable"),
                "repository": REPOSITORY,
            }

    def status(self) -> dict:
        policy = self.policy()
        cached = read_json(self.control / "check.json", None)
        if cached and cached.get("channel") != policy["channel"]:
            cached = {
                "channel": policy["channel"],
                "status": "not_checked",
                "candidate": None,
                "message": "通道已切换，请检查更新",
            }
        marker = self.gate.state()
        return {
            "policy": policy,
            "check": cached,
            "maintenance": {key: marker[key] for key in ("operation", "phase") if key in marker},
            "version": self.version(),
            "last_operation": read_json(self.control / "last-operation.json", None),
        }

    def check(self, *, manual: bool = True) -> dict:
        with FileLock(self.control / "check.lock", timeout_s=0):
            cached = read_json(self.control / "check.json", {})
            policy = self.policy()
            if (
                not manual
                and cached.get("channel") == policy["channel"]
                and not cached.get("error_code")
                and 0 <= self.clock() - cached.get("checked_at", 0) < 86400
            ):
                return cached
            result = {"channel": policy["channel"], "checked_at": self.clock(), "candidate": None}
            try:
                current = identify(self.root)
                candidate = self.client.candidate(policy["channel"])
                if candidate is None:
                    result.update(status="no_release", message="暂无正式发行版，可主动切换到开发版")
                else:
                    if current["installation"] == "unknown":
                        current = self._adopt_legacy(current)
                    relation = self.client.relation(current["commit"], candidate["commit"])
                    if relation == "unknown" and current["installation"] == "git":
                        # 未推送的本地提交不在 Gitee compare 范围内，已有本地对象仍可只读判定祖先。
                        try:
                            ancestor = git(self.root, "merge-base", current["commit"], candidate["commit"])
                        except UpdateError:
                            pass
                        else:
                            relation = (
                                "behind"
                                if ancestor == current["commit"]
                                else "ahead"
                                if ancestor == candidate["commit"]
                                else "diverged"
                            )
                    if candidate["version"] and semver(candidate["version"]) < semver(current["version"]):
                        relation = "ahead"
                    candidate["id"] = uuid.uuid4().hex
                    result.update(
                        candidate=candidate,
                        relation=relation,
                        status={
                            "equal": "up_to_date",
                            "behind": "available",
                            "ahead": "ahead",
                            "diverged": "diverged",
                        }.get(relation, "baseline_unknown"),
                    )
                    result["message"] = {
                        "up_to_date": "已是当前通道最新版本",
                        "available": "发现可用更新",
                        "ahead": "当前版本领先于此通道，未提供降级",
                        "diverged": "本地与官方源码已分叉",
                        "baseline_unknown": "无法确认安装基线，请使用带版本清单的源码发行包",
                    }[result["status"]]
                    if relation == "unknown" and current["installation"] == "git":
                        result["message"] = "当前 Git 提交缺少可比对的历史，请先保存本地修改并同步官方仓库"
                    if current["dirty"]:
                        result["blockers"] = current["dirty"]
                result["baseline"] = current["fingerprint"]
            except (UpdateError, KeyError, TypeError) as exc:
                result.update(
                    status="error",
                    error_code=getattr(exc, "code", "invalid_response"),
                    message=str(exc) if isinstance(exc, UpdateError) else "Gitee 响应格式不受支持",
                )
            write_json(self.control / "check.json", result)
            return result

    def _adopt_legacy(self, current: dict) -> dict:
        commit = self.client.baseline(current["version"])
        if not commit:
            return current
        work = self.control / "baselines" / uuid.uuid4().hex
        work.mkdir(parents=True)
        self.client.download(commit, work / "source.zip")
        receipt = extract(work / "source.zip", work / "source", commit)
        if inventory(self.root) != receipt["files"]:
            return current
        write_json(self.root / "omni-build.json", receipt)
        adopted = identify(self.root)
        if self._running and self._running["fingerprint"] == adopted["fingerprint"]:
            self._running = adopted
        return adopted

    def _launch_thread(self, name: str, action) -> None:
        thread = threading.Thread(target=action, name=name, daemon=True)
        self._threads.append(thread)
        thread.start()

    def operation(self, identifier: str) -> dict:
        if not OP_ID.fullmatch(identifier):
            raise UpdateError("not_found", "更新操作不存在")
        state = read_json(self.control / "operations" / identifier / "state.json")
        if state is None:
            raise UpdateError("not_found", "更新操作不存在")
        owner = state.get("owner")
        if state.get("phase") == "preparing" and owner:
            identity = inspect_process(owner["pid"])
            if identity is None or asdict(identity) != owner:
                state.update(
                    phase="failed", error_code="preparation_interrupted", message="上次准备进程已退出，可重新准备更新"
                )
                write_json(self.control / "operations" / identifier / "state.json", state)
        return state

    def _state(self, operation: Path, phase: str, **extra) -> dict:
        data = read_json(operation / "state.json", {})
        data.update(id=operation.name, phase=phase, updated_at=self.clock(), **extra)
        write_json(operation / "state.json", data)
        write_json(self.control / "last-operation.json", {"id": operation.name})
        return data

    def prepare(self, candidate_id: str) -> dict:
        with FileLock(self.control / "prepare.lock"):
            check = read_json(self.control / "check.json", {})
            candidate = check.get("candidate") or {}
            if (
                check.get("status") != "available"
                or candidate.get("id") != candidate_id
                or check.get("channel") != self.policy()["channel"]
            ):
                raise UpdateError("stale_candidate", "更新候选已失效，请重新检查")
            last = read_json(self.control / "last-operation.json", {})
            if last:
                state = self.operation(last["id"])
                if state.get("candidate_id") == candidate_id and state["phase"] not in TERMINAL:
                    return state
                if state["phase"] not in TERMINAL:
                    raise UpdateError("update_busy", "已有更新准备或安装操作")
            current = identify(self.root)
            if current["dirty"] or current["fingerprint"] != check.get("baseline"):
                raise UpdateError("local_changes", "存在本地修改或基线已变化，请先保存修改并重新检查")
            if current["installation"] not in {"git", "archive"}:
                raise UpdateError("baseline_unknown", "当前安装缺少可信版本基线")
            operation = self.control / "operations" / uuid.uuid4().hex
            identity = inspect_process(os.getpid())
            state = self._state(
                operation,
                "preparing",
                candidate_id=candidate_id,
                target=candidate,
                owner=asdict(identity) if identity else None,
                message="正在下载并检查源码",
            )
            self._launch_thread("update-prepare", lambda: self._prepare(operation, candidate, current))
            return state

    def _prepare(self, operation: Path, candidate: dict, current: dict) -> None:
        try:
            archive = operation / "source.zip"
            self.client.download(candidate["commit"], archive)
            stage = operation / "source"
            receipt = extract(archive, stage, candidate["commit"])
            if semver(receipt["version"]) < semver(current["version"]):
                raise UpdateError("downgrade", "目标产品版本较旧，不提供自动降级")
            if candidate["version"] and receipt["version"] != candidate["version"]:
                raise UpdateError("invalid_version", "发行标签与产品版本不一致")
            if current["installation"] == "git":
                git(self.root, "fetch", "--no-tags", "origin", candidate["commit"])
                git(self.root, "merge-base", "--is-ancestor", current["commit"], candidate["commit"])
                from .common import managed

                if any(
                    name and not managed(name)
                    for name in git(
                        self.root, "diff", "--name-only", "-z", current["commit"], candidate["commit"]
                    ).split("\0")
                ):
                    raise UpdateError("protected_changes", "目标提交修改了用户目录或非受管文件，请手动迁移该版本")
            baseline = inventory(self.root)
            for name in receipt["files"].keys() - baseline.keys():
                if (self.root / name).exists():
                    raise UpdateError("file_collision", "新版本文件与本地文件冲突：" + name)
            required = archive.stat().st_size * 4 + sum(
                p.stat().st_size for p in (self.root / ".venv").rglob("*") if p.is_file()
            )
            if self.paths is not None:
                for item in self._snapshot_sources():
                    path = Path(item["path"])
                    files = path.rglob("*") if path.is_dir() else [path]
                    required += sum(no_links(file).stat().st_size for file in files if file.is_file())
            if shutil.disk_usage(self.root).free < max(required, 512 * 1024 * 1024):
                raise UpdateError("disk_space", "磁盘空间不足以保留环境恢复点")
            uv = shutil.which("uv")
            if not uv:
                raise UpdateError("uv_unavailable", "未找到 uv，请先安装项目所需工具")
            git_binary = shutil.which("git")
            for binary in (uv, git_binary):
                if binary and Path(binary).resolve().is_relative_to(self.root / ".venv"):
                    raise UpdateError("tool_in_environment", "更新所需 uv/Git 必须安装在当前 .venv 外")
            plan = {
                "schema": 1,
                "root": str(self.root),
                "control": str(self.control),
                "stage": str(stage),
                "receipt": receipt,
                "archive_sha256": digest(archive),
                "installation": current["installation"],
                "baseline_commit": current["commit"],
                "baseline_files": baseline,
                "baseline_fingerprint": current["fingerprint"],
                "uv": uv,
                "git_binary": git_binary or "git",
                "probe_token": uuid.uuid4().hex,
            }
            self._freeze_runner(operation, plan)
            write_json(operation / "plan.json", plan)
            with FileLock(self.control / "prepare.lock"):
                if (
                    self.operation(operation.name)["phase"] == "cancelled"
                    or self.policy()["channel"] != candidate["channel"]
                ):
                    self._state(operation, "cancelled", message="更新通道已切换，请重新检查")
                else:
                    self._state(
                        operation,
                        "ready",
                        message="更新已准备，请确认安装",
                        target={**candidate, "version": receipt["version"]},
                    )
        except Exception as exc:  # noqa: BLE001 -- 安装事务边界必须记录失败并保留恢复入口
            if self.operation(operation.name)["phase"] == "cancelled":
                return
            self._state(
                operation,
                "failed",
                error_code=getattr(exc, "code", "prepare_failed"),
                error_message=error_message(exc),
                message="准备更新失败：" + error_message(exc),
            )

    def install(self, identifier: str, *, confirm: bool) -> dict:
        with FileLock(self.control / "install.lock"):
            return self._install(identifier, confirm=confirm)

    def _install(self, identifier: str, *, confirm: bool) -> dict:
        if not confirm:
            raise UpdateError("confirmation_required", "安装更新需要明确确认")
        if not self.enabled or os.name != "nt" or not self.shutdown or self.probe_mode:
            raise UpdateError(
                "installation_unavailable", "当前运行方式不支持自动重启，请通过 Windows 工作台启动入口升级"
            )
        operation = self.control / "operations" / identifier
        state = self.operation(identifier)
        if state["phase"] in {
            "waiting_idle",
            "stopping",
            "snapshotting",
            "applying",
            "environment",
            "rehearsing",
            "starting",
            "succeeded",
        }:
            return state
        if state["phase"] != "ready":
            raise UpdateError("not_ready", "更新尚未准备完成")
        plan = read_json(operation / "plan.json")
        current = identify(self.root)
        if current["dirty"] or current["fingerprint"] != plan["baseline_fingerprint"] or self.version()["disk_changed"]:
            raise UpdateError("source_changed", "运行源码或安装基线已变化，请保存修改并重启后重新检查")
        verify(Path(plan["stage"]), plan["receipt"])
        peers = self.gate.peers()
        if any(row["id"] != self.gate.id and row["kind"] not in {"qq", "heartbeat"} for row in peers):
            raise UpdateError("extra_runtime", "请先退出其他 CLI 或工作台入口，再安装更新")
        known = {row["pid"] for row in peers}
        service_pids = set()
        for name in ("omni-qq", "omni-heartbeat"):
            status = self.services.status(name)
            if status.get("running") and not status.get("identity_verified"):
                raise UpdateError("unverified_process", "存在未核验的常驻服务，请先从原启动入口退出")
            if status.get("running") and status.get("pid") not in known:
                raise UpdateError(
                    "legacy_runtime", "常驻服务尚未接入更新维护协议，请先从原入口退出旧服务并重新启动工作台"
                )
            if status.get("running"):
                service_pids.add(status["pid"])
        if any(row["id"] != self.gate.id and row["pid"] not in service_pids for row in peers):
            raise UpdateError("extra_runtime", "其他 Agent 或独立服务仍使用此安装，请先从原入口退出后重试")
        parents = process_parents()

        def belongs_to_runtime(pid):
            seen = set()
            while pid and pid not in seen:
                if pid in known:
                    return True
                seen.add(pid)
                pid = parents.get(pid, 0)
            return False

        for pid in parents:
            identity = inspect_process(pid)
            if (
                identity
                and Path(identity.executable).is_relative_to(self.root / ".venv")
                and not belongs_to_runtime(pid)
            ):
                raise UpdateError("unverified_process", "当前环境仍有未登记进程，请先从原启动入口退出")
        write_json(
            self.control / "recovery.json",
            {"python": plan["base_python"], "runner": plan["runner"], "operation": str(operation)},
        )
        self.gate.begin(
            {"operation": identifier, "owner": self.gate.id, "phase": "draining", "probe_token": plan["probe_token"]}
        )
        state = self._state(operation, "waiting_idle", message="暂停新任务，等待现有任务结束")
        self._launch_thread("update-install", lambda: self._drain(operation, plan))
        return state

    def _snapshot_sources(self) -> list[dict]:
        paths = [self.paths]
        if self.directories:
            from ..agent_spaces.catalog import read_catalog, validate_catalog

            data, _ = read_catalog(self.directories.path)
            _, layouts = validate_catalog(
                data, self.directories.legacy, catalog=self.directories.path, isolated=not self.enabled
            )
            paths.extend(layout.paths for layout in layouts)
        sources = {}
        self._agents = [
            {"state": str(item.state), "memory": str(item.memory), "resident": str(item.resident)} for item in paths
        ]
        for item in paths:
            for kind, names in [
                ("database", ("state", "memory")),
                ("file", ("config", "env", "resident", "cookies", "catalog")),
                ("tree", ("skills", "history", "file_history", "artifacts")),
            ]:
                for name in names:
                    path = getattr(item, name, None)
                    if path:
                        sources[str(no_links(path))] = kind
            sources[str(item.data / "plugins")] = "tree"
        # 外置路径和共享数据库去重；快照不能包含更新控制目录自身。
        sources[str(self.root / ".env")] = "file"
        if self.directories:
            sources[str(self.directories.legacy.data / "web_secret")] = "file"
        if any(self.control.is_relative_to(Path(path)) or Path(path).is_relative_to(self.control) for path in sources):
            raise UpdateError("snapshot_overlap", "业务快照路径与安装更新控制区重叠，请先调整目录")
        return [{"path": path, "kind": kind} for path, kind in sorted(sources.items())]

    def _drain(self, operation: Path, plan: dict) -> None:
        handed_over = False
        try:
            deadline = time.monotonic() + self.drain_timeout
            idle_rounds = 0
            while True:
                rows = self.gate.peers()
                if not any(row.get("busy") for row in rows) and not self.busy_count():
                    idle_rounds += 1
                    if idle_rounds >= 3:
                        break
                else:
                    idle_rounds = 0
                if time.monotonic() >= deadline or self._stop.wait(0.2):
                    raise UpdateError("idle_timeout", "任务尚未空闲，本次升级已取消并恢复运行")
            if self.terminal_busy():
                raise UpdateError("terminal_busy", "人工终端仍打开，请关闭终端后重试")
            processes = []
            for name in ("omni-qq", "omni-heartbeat"):
                status = self.services.status(name)
                if status.get("pid"):
                    identity = inspect_process(status["pid"])
                    if identity:
                        processes.append(asdict(identity))
            identity = inspect_process(os.getpid())
            if identity:
                processes.append(asdict(identity))
            plan.update(
                sources=self._snapshot_sources(),
                agents=self._agents,
                processes=processes,
                web_process=asdict(identity) if identity else None,
                environment={
                    key: os.environ[key]
                    for key in (
                        "OMNI_WEB_HOST",
                        "OMNI_WEB_PORT",
                        "OMNI_CONFIG_PATH",
                        "OMNI_STORAGE_BASE",
                        "OMNI_AGENT_ID",
                        "OMNI_AGENT_CATALOG",
                    )
                    if key in os.environ
                },
            )
            write_json(operation / "plan.json", plan)
            self._state(operation, "stopping", message="正在关闭服务并保存恢复点")
            marker = self.gate.state()
            if marker.get("operation") != operation.name:
                raise UpdateError("operation_changed", "维护事务已变化，未启动安装")
            marker["phase"] = "stopping"
            write_json(self.gate.path, marker)
            # 子进程不会继承旧 venv 的可执行文件句柄；所有路径通过 argv 传递。
            log = (operation / "runner.log").open("ab")
            try:
                subprocess.Popen(
                    [plan["base_python"], plan["runner"], str(operation)],
                    cwd=operation,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            finally:
                log.close()
            handed_over = True
            self.shutdown()
        except Exception as exc:  # noqa: BLE001 -- 安装事务边界必须记录失败并保留恢复入口
            self._state(
                operation,
                "cancelled",
                error_code=getattr(exc, "code", "install_failed"),
                message=str(exc) if isinstance(exc, UpdateError) else "升级未启动，当前安装未改变",
            )
        finally:
            if not handed_over:
                self.gate.clear(operation.name)

    def _freeze_runner(self, operation: Path, plan: dict) -> None:
        bundle = operation / "runner"
        updates = bundle / "system/updates"
        updates.mkdir(parents=True)
        (bundle / "system/__init__.py").write_text("", encoding="utf-8")
        for name in ("filelock.py", "process_identity.py"):
            shutil.copy2(Path(__file__).parents[1] / name, bundle / "system" / name)
        for name in ("__init__.py", "common.py", "version.py", "archive.py", "gate.py", "snapshot.py", "worker.py"):
            shutil.copy2(Path(__file__).parent / name, updates / name)
        launcher = bundle / "run.py"
        launcher.write_text("from system.updates.worker import main\nmain()\n", encoding="utf-8")
        # uv 的解释器目录可以是可信的版本别名链接；冻结实际二进制路径。
        python = Path(sys._base_executable).resolve(strict=True)
        if python.is_relative_to(self.root / ".venv"):
            raise UpdateError("base_python", "无法找到独立于当前环境的 Python 恢复执行器")
        plan.update(base_python=str(python), runner=str(launcher))

    def busy_count(self) -> int:
        return self.gate.busy + self._busy_probe()

    def start(self, *, paths, directories, services, busy_probe) -> None:
        self.paths, self.directories, self.services = paths, directories, services
        self._busy_probe = busy_probe
        self.gate.report(self.busy_count())
        if not self.enabled:
            return

        def report():
            while not self._stop.wait(0.2):
                try:
                    self.gate.report(self.busy_count())
                except Exception:  # noqa: BLE001 -- 安装事务边界必须记录失败并保留恢复入口
                    # 无法确认入口空闲时不能把最后一次 busy=0 留给升级线程。
                    self.gate.report(1)

        self._launch_thread("update-lease", report)

    def start_checks(self) -> None:
        if not self.enabled:
            return

        def background():
            delay = 30
            while not self._stop.wait(delay):
                try:
                    if self.policy()["auto_check"] and not self.gate.active():
                        self.check(manual=False)
                except (UpdateError, LockTimeout, OSError):
                    pass
                delay = 3600

        self._launch_thread("update-check", background)

    def close(self) -> None:
        self._stop.set()
        self.gate.leave()
