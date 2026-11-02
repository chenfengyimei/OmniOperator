"""冻结后以基础 Python 独立运行的升级/恢复执行器；只依赖标准库和冻结的平台原语。"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import time
from pathlib import Path

from ..filelock import FileLock
from ..process_identity import (
    ProcessIdentity,
    confirmed_exited,
    inspect_process,
    process_parents,
    terminate_verified_tree,
)
from .archive import verify
from .common import TERMINAL, UpdateError, digest, error_message, managed, no_links, read_json, run, write_json
from .gate import MaintenanceGate
from .snapshot import capture, restore

PROTOCOL_VERSION = 1


class Transaction:
    def __init__(self, operation: Path, *, execute=run, clock=time.monotonic, sleep=time.sleep):
        self.operation = no_links(operation)
        self.plan = read_json(operation / "plan.json")
        self.root = no_links(Path(self.plan["root"]))
        if self.root == Path(self.root.anchor):
            raise UpdateError("invalid_operation", "升级目标不是有效的产品源码安装目录")
        if (
            not isinstance(self.plan.get("baseline_files"), dict)
            or not self.plan["baseline_files"]
            or any(not managed(name) for name in self.plan["baseline_files"])
        ):
            raise UpdateError("invalid_operation", "升级基线包含非法文件路径")
        self.control = no_links(Path(self.plan["control"]))
        if operation.parent != self.control / "operations":
            raise UpdateError("invalid_operation", "升级事务不在声明的控制目录")
        self.gate = MaintenanceGate(self.control)
        self.execute, self.clock, self.sleep = execute, clock, sleep
        self.state = read_json(operation / "state.json", {})

    def checkpoint(self, phase: str, **extra) -> None:
        self.state.update(phase=phase, updated_at=time.time(), **extra)
        write_json(self.operation / "state.json", self.state)

    def command(self, argv: list[str], **kwargs) -> str:
        def spawned(identity):
            from dataclasses import asdict

            self.checkpoint(self.state["phase"], command=asdict(identity) if identity else None)

        result = self.execute(argv, on_spawn=spawned, **kwargs)
        self.checkpoint(self.state["phase"], command=None)
        return result

    def repo_git(self, *args: str) -> str:
        return self.command(
            [self.plan.get("git_binary", "git"), "-c", "core.hooksPath=", "-C", str(self.root), *args], cwd=self.root
        )

    def wait_for_exit(self) -> None:
        deadline = self.clock() + 60
        while (
            self.gate.peers()
            or any(not confirmed_exited(ProcessIdentity(**record)) for record in self.plan.get("processes", []))
            or self.installation_in_use()
        ):
            if self.clock() >= deadline:
                raise UpdateError("process_residual", "仍有运行入口未完整退出，未修改安装")
            self.sleep(0.1)

    def installation_in_use(self) -> bool:
        for pid in process_parents():
            identity = inspect_process(pid)
            if identity and Path(identity.executable).is_relative_to(self.root / ".venv"):
                return True
        return False

    def validate_baseline(self) -> None:
        for name, expected in self.plan["baseline_files"].items():
            path = no_links(self.root / name)
            if not path.is_file() or digest(path) != expected:
                raise UpdateError("source_changed", "安装源码在准备后发生变化：" + name)
        if self.plan["installation"] == "git" and (
            self.repo_git("rev-parse", "HEAD") != self.plan["baseline_commit"] or self.repo_git("status", "--porcelain")
        ):
            raise UpdateError("source_changed", "Git 工作区已变化，未应用更新")

    def apply_source(self) -> None:
        receipt = self.plan["receipt"]
        stage = Path(self.plan["stage"])
        verify(stage, receipt)
        if self.plan["installation"] == "git":
            self.repo_git("fetch", "--no-tags", "origin", receipt["commit"])
            self.repo_git("merge-base", "--is-ancestor", self.plan["baseline_commit"], receipt["commit"])
            changed = self.repo_git("diff", "--name-only", "-z", self.plan["baseline_commit"], receipt["commit"]).split(
                "\0"
            )
            if any(name and not managed(name) for name in changed):
                raise UpdateError("protected_changes", "目标提交包含用户目录修改，未应用更新")
            self.repo_git("merge", "--ff-only", "--no-edit", receipt["commit"])
            # Windows checkout 可能自动转为 CRLF；写入已验证的发行字节使清单跨安装方式一致。
            for name in receipt["files"]:
                path = no_links(self.root / name)
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(stage / name, path)
        else:
            for name in self.plan["baseline_files"].keys() - receipt["files"].keys():
                no_links(self.root / name).unlink(missing_ok=True)
            for name in receipt["files"]:
                path = no_links(self.root / name)
                if path.exists() and name not in self.plan["baseline_files"]:
                    raise UpdateError("file_collision", "目标文件与本地未受管文件冲突：" + name)
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(stage / name, path)
        write_json(self.root / "omni-build.json", receipt)
        verify(self.root, receipt)

    def start_web(self, *, probe: bool) -> dict:
        environment = os.environ.copy()
        environment.update(self.plan.get("environment", {}))
        environment.pop("PYTHONHOME", None)
        environment.pop("PYTHONPATH", None)
        if probe:
            environment["OMNI_UPDATE_PROBE"] = self.plan["probe_token"]
        else:
            environment.pop("OMNI_UPDATE_PROBE", None)
        log = (self.operation / ("startup.log" if probe else "recovery.log")).open("ab")
        try:
            child = subprocess.Popen(
                [str(self.root / ".venv/Scripts/python.exe"), "-m", "omni.channels.web"],
                cwd=self.root,
                env=environment,
                stdout=log,
                stderr=log,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        finally:
            log.close()
        deadline = self.clock() + 5
        identity = inspect_process(child.pid)
        while identity is None and self.clock() < deadline and child.poll() is None:
            self.sleep(0.05)
            identity = inspect_process(child.pid)
        if identity is None:
            try:
                child.kill()
                child.wait(timeout=5)
            except Exception as exc:  # noqa: BLE001 -- 安装事务边界必须记录失败并保留恢复入口
                raise UpdateError("command_residual", "新工作台进程不可核验且未确认退出，保留恢复点") from exc
            raise UpdateError("startup_failed", "新工作台进程身份不可核验")
        from dataclasses import asdict

        result = asdict(identity)
        self.checkpoint(self.state["phase"], child=result)
        return result

    def wait_ready(self, child: dict) -> None:
        deadline = self.clock() + 60
        while self.clock() < deadline:
            ready = read_json(self.operation / "ready.json", {})
            if ready.get("token") == self.plan["probe_token"] and ready.get("commit") == self.plan["receipt"]["commit"]:
                return
            if confirmed_exited(ProcessIdentity(**child)):
                break
            self.sleep(0.2)
        raise UpdateError("startup_failed", "新版本未在时限内通过启动与数据库检查")

    def apply(self) -> None:
        self.checkpoint("stopping")
        self.wait_for_exit()
        self.validate_baseline()
        self.checkpoint("snapshotting")
        capture(self.operation / "data-snapshot", self.plan["sources"])
        names = sorted(set(self.plan["baseline_files"]) | set(self.plan["receipt"]["files"]) | {"omni-build.json"})
        self.plan["code_sources"] = [{"path": str(self.root / name), "kind": "file"} for name in names]
        write_json(self.operation / "plan.json", self.plan)
        capture(self.operation / "code-snapshot", self.plan["code_sources"])
        self.validate_baseline()
        # 写前日志：崩在一次文件替换中间时也必须恢复整份受管源码。
        self.checkpoint("applying", code_changed=True)
        self.apply_source()
        environment = self.root / ".venv"
        if environment.exists():
            self.checkpoint("environment", environment_saved=True)
            no_links(environment).rename(self.operation / "previous-venv")
        clean_environment = os.environ.copy()
        clean_environment.pop("PYTHONHOME", None)
        clean_environment.pop("PYTHONPATH", None)
        clean_environment["UV_PROJECT_ENVIRONMENT"] = str(self.root / ".venv")
        arguments = [self.plan["uv"], "sync", "--frozen", "--project", str(self.root)]
        if self.plan.get("base_python"):
            arguments.extend(["--python", self.plan["base_python"]])
        self.command(arguments, cwd=self.root, timeout=900, env=clean_environment)
        self.checkpoint("rehearsing")
        self.command(
            [
                str(self.root / ".venv/Scripts/python.exe"),
                "-m",
                "omni.channels.update_probe",
                str(self.operation / "plan.json"),
            ],
            cwd=self.root,
            timeout=180,
            env=clean_environment,
        )
        self.checkpoint("starting")
        child = self.start_web(probe=True)
        self.wait_ready(child)
        # 提交记录先于解除闸门；此后即使执行器退出也不回滚已恢复写入的数据。
        self.checkpoint("succeeded", committed=True, message="版本验证通过，正在恢复普通工作台")
        self.gate.clear(self.operation.name)

    def rollback(self) -> None:
        if self.state.get("committed"):
            return
        if not self.state.get("code_changed") and not (self.operation / "data-snapshot/snapshot.json").exists():
            self.checkpoint("rolled_back", message="安装尚未修改源码或数据，已解除维护状态")
            self.gate.clear(self.operation.name)
            previous_web = self.plan.get("web_process")
            if self.plan.get("restart", True) and previous_web and confirmed_exited(ProcessIdentity(**previous_web)):
                self.start_web(probe=False)
            return
        self.checkpoint("rolling_back")
        command = self.state.get("command")
        if command and not confirmed_exited(ProcessIdentity(**command)):
            terminate_verified_tree(ProcessIdentity(**command))
        child = self.state.get("child")
        if child and not confirmed_exited(ProcessIdentity(**child)):
            terminate_verified_tree(ProcessIdentity(**child))
        self.wait_for_exit()
        if self.state.get("code_changed"):
            if self.plan["installation"] == "git":
                # 仅恢复本事务已核对且升级前干净的仓库，不清理任何未受管文件。
                self.repo_git("reset", "--hard", self.plan["baseline_commit"])
            restore(self.operation / "code-snapshot", self.plan["code_sources"])
        previous = self.operation / "previous-venv"
        if previous.exists():
            current = no_links(self.root / ".venv")
            if current.exists():
                failed = self.operation / "failed-venv"
                if failed.exists():
                    raise UpdateError("recovery_failed", "失败环境已存在，请核验恢复点")
                current.rename(failed)
            previous.rename(current)
        if (self.operation / "data-snapshot/snapshot.json").exists():
            restore(self.operation / "data-snapshot", self.plan["sources"])
        self.checkpoint("rolled_back", message="更新未完成，已恢复升级前的源码、环境与数据")
        self.gate.clear(self.operation.name)
        if self.plan.get("restart", True):
            self.start_web(probe=False)

    def run(self, *, recover: bool = False, confirm_processes_stopped: bool = False) -> None:
        with FileLock(self.control / "execution.lock", timeout_s=0):
            marker = self.gate.state()
            if marker and marker.get("operation") != self.operation.name:
                raise UpdateError("operation_changed", "当前维护状态属于另一个升级事务，未修改任何文件")
            if self.state.get("committed") or self.state.get("phase") in TERMINAL:
                if self.state.get("committed"):
                    self.gate.clear(self.operation.name)
                return
            if recover:
                if self.state.get("error_code") == "command_residual":
                    if not confirm_processes_stopped:
                        raise UpdateError(
                            "command_residual", "先核验并处理记录中的残留命令，再通过 -ConfirmProcessesStopped 确认恢复"
                        )
                    self.wait_for_exit()
                    command = self.state.get("command")
                    if command and not confirmed_exited(ProcessIdentity(**command)):
                        raise UpdateError("command_residual", "记录中的命令仍未确认退出，未执行恢复")
                owner = self.gate.state().get("owner")
                if any(row["id"] == owner for row in self.gate.peers()):
                    raise UpdateError("update_busy", "工作台仍在协调升级，请等待其结束")
                self.rollback()
                return
            try:
                self.apply()
            except Exception as exc:  # noqa: BLE001 -- 安装事务边界必须记录失败并保留恢复入口
                self.checkpoint(
                    self.state["phase"],
                    error_code=getattr(exc, "code", "update_failed"),
                    failed_phase=self.state["phase"],
                    error_message=error_message(exc),
                    message="更新失败，正在恢复",
                )
                if getattr(exc, "code", "") == "command_residual":
                    self.checkpoint("recovery_required", message="更新命令进程树未确认回收，保留维护状态和恢复点")
                    return
                try:
                    self.rollback()
                except Exception:  # noqa: BLE001 -- 安装事务边界必须记录失败并保留恢复入口
                    self.checkpoint("recovery_required", message="恢复未完成，保留维护状态；请重新运行恢复入口")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", type=Path)
    parser.add_argument("--recover", action="store_true")
    parser.add_argument("--confirm-processes-stopped", action="store_true")
    args = parser.parse_args()
    transaction = Transaction(args.operation)
    transaction.run(recover=args.recover, confirm_processes_stopped=args.confirm_processes_stopped)
    raise SystemExit(0 if transaction.state.get("phase") in {"succeeded", "rolled_back"} else 1)


if __name__ == "__main__":
    main()
