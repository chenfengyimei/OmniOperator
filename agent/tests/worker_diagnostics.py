"""Opt-in local pytest worker diagnostics; preserves scheduling, timeouts and outcomes."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from xdist.workermanage import WorkerController

_state = pytest.StashKey["Diagnostics"]()
_limit = 16 * 1024 * 1024


@cache
def _performance_api():
    import ctypes
    from ctypes import wintypes

    class Performance(ctypes.Structure):
        _fields_ = (
            [("cb", wintypes.DWORD)]
            + [
                (name, ctypes.c_size_t)
                for name in (
                    "CommitTotal",
                    "CommitLimit",
                    "CommitPeak",
                    "PhysicalTotal",
                    "PhysicalAvailable",
                    "SystemCache",
                    "KernelTotal",
                    "KernelPaged",
                    "KernelNonpaged",
                    "PageSize",
                )
            ]
            + [(name, wintypes.DWORD) for name in ("HandleCount", "ProcessCount", "ThreadCount")]
        )

    api = ctypes.WinDLL("psapi", use_last_error=True).GetPerformanceInfo
    api.argtypes = [ctypes.POINTER(Performance), wintypes.DWORD]
    api.restype = wintypes.BOOL
    return Performance, api


def system_commit() -> dict[str, int] | None:
    if sys.platform != "win32":
        return None
    import ctypes

    Performance, api = _performance_api()
    value = Performance()
    value.cb = ctypes.sizeof(value)
    if not api(ctypes.byref(value), value.cb):
        raise OSError("GetPerformanceInfo unavailable")
    return {
        name: getattr(value, name) * value.PageSize
        for name in (
            "CommitTotal",
            "CommitLimit",
            "CommitPeak",
            "PhysicalTotal",
            "PhysicalAvailable",
        )
    }


class Diagnostics:
    def __init__(self, directory: Path, worker: str) -> None:
        self.directory, self.worker = directory, worker
        self.errors = 0
        self.written = 0
        self.capped = False
        self.nodes: list[WorkerController] = []
        self.stream = (directory / f"{worker}-{os.getpid()}.jsonl").open("x", encoding="utf-8")

    def record(self, event: str, **fields: object) -> None:
        if self.capped:
            return
        try:
            value = {"time_ns": time.time_ns(), "pid": os.getpid(), "worker": self.worker, "event": event, **fields}
            try:
                value["system_commit"] = system_commit()
            except (OSError, MemoryError):
                self.errors += 1
                value["system_commit_unavailable"] = True
            line = json.dumps(value, ensure_ascii=True) + "\n"
            if self.written + len(line) > _limit - 256:
                self.capped = True
                self.errors += 1
                self.stream.write('{"event":"journal_limit","diagnostics_incomplete":true}\n')
                self.stream.flush()
                return
            self.stream.write(line)
            self.stream.flush()
            self.written += len(line)
        except (OSError, MemoryError):
            self.errors += 1

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        self.record(
            "report",
            nodeid=report.nodeid[:2048],
            when=report.when,
            outcome=report.outcome,
            report_memory_fallback=getattr(report, "report_memory_fallback", False),
        )

    def pytest_internalerror(self, excrepr, excinfo) -> None:
        self.record("internal_error", exception_type=excinfo.type.__name__)

    def pytest_terminal_summary(self, terminalreporter) -> None:
        child_errors = sum(
            getattr(node, "workeroutput", {}).get("omni_worker_diagnostic_errors", 0) for node in self.nodes
        )
        if self.errors or child_errors:
            terminalreporter.write_line(
                f"Worker diagnostics incomplete: local errors={self.errors}, worker errors={child_errors}"
            )

    def node_exit(self, node: WorkerController, stage: str, error: object = None) -> None:
        gateway = node.gateway
        process = getattr(getattr(gateway, "_io", None), "popen", None)
        try:
            code = process.poll() if process is not None else None
        except OSError:
            self.errors += 1
            code = None
        self.record(
            "worker_exit",
            worker_id=gateway.id,
            stage=stage,
            launcher_pid=process.pid if process is not None else None,
            returncode=code,
            windows_status_hex=f"0x{code & 0xFFFFFFFF:08x}" if code is not None and sys.platform == "win32" else None,
            error_type=type(error).__name__ if error is not None else None,
            error=error[:512] if type(error) is str else None,
            worker_diagnostic_errors=getattr(node, "workeroutput", {}).get("omni_worker_diagnostic_errors"),
        )


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--worker-diagnostics",
        metavar="NEW_DIRECTORY",
        help="Persist local worker phase/exit/memory/fault diagnostics in a fresh directory",
    )


@pytest.hookimpl(trylast=True)
def pytest_configure(config: pytest.Config) -> None:
    raw = config.getoption("worker_diagnostics")
    if raw is None:
        return
    from _pytest.faulthandler import fault_handler_stderr_fd_key

    if fault_handler_stderr_fd_key not in config.stash:
        raise pytest.UsageError("--worker-diagnostics requires pytest's faulthandler plugin")
    worker_input = getattr(config, "workerinput", None)
    directory = Path(worker_input["omni_worker_diagnostics"] if worker_input else raw).resolve()
    worker = worker_input["workerid"] if worker_input else "controller"
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", worker):
        raise pytest.UsageError("Invalid diagnostic worker identity")
    try:
        if worker_input is None:
            directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        state = Diagnostics(directory, worker)
        config.stash[_state] = state
        # Pytest already owns this duplicated fd and closes it at unconfigure.
        # Its native handler AND existing timeout dumps now survive worker death.
        with (directory / f"{worker}-{os.getpid()}.fault.txt").open("xb") as fault:
            os.dup2(fault.fileno(), config.stash[fault_handler_stderr_fd_key])
        import faulthandler

        faulthandler.enable(file=config.stash[fault_handler_stderr_fd_key])
    except OSError as error:
        raise pytest.UsageError("Worker diagnostics requires a writable fresh directory") from error
    config.pluginmanager.register(state, "omni-worker-diagnostics")
    state.record("configured", journal_limit_bytes=_limit)


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node: WorkerController) -> None:
    state = node.config.stash.get(_state, None)
    if state is not None:
        if not node.gateway.spec.popen:
            raise pytest.UsageError("--worker-diagnostics supports local popen workers only")
        node.workerinput["omni_worker_diagnostics"] = str(state.directory)
        state.nodes.append(node)
        process = getattr(getattr(node.gateway, "_io", None), "popen", None)
        state.record(
            "worker_spawn", worker_id=node.gateway.id, launcher_pid=process.pid if process is not None else None
        )


def phase(item: pytest.Item, when: str) -> None:
    state = item.config.stash.get(_state, None)
    if state is not None:
        state.record("phase_start", nodeid=item.nodeid[:2048], when=when)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_setup(item: pytest.Item):
    phase(item, "setup")
    return (yield)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_call(item: pytest.Item):
    phase(item, "call")
    return (yield)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_teardown(item: pytest.Item):
    phase(item, "teardown")
    return (yield)


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node: WorkerController, error: object) -> None:
    state = node.config.stash.get(_state, None)
    if state is not None:
        if node not in state.nodes:
            state.nodes.append(node)
        state.node_exit(node, "channel_down_poll", error)


@pytest.hookimpl(wrapper=True, trylast=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int):
    state = session.config.stash.get(_state, None)
    try:
        return (yield)
    finally:
        if state is not None:
            for node in state.nodes:
                state.node_exit(node, "after_xdist_teardown")
            state.record(
                "session_finish",
                exitstatus=int(exitstatus),
                diagnostic_errors=state.errors,
                journal_capped=state.capped,
            )
            if hasattr(session.config, "workeroutput"):
                session.config.workeroutput["omni_worker_diagnostic_errors"] = state.errors


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config: pytest.Config) -> None:
    state = config.stash.get(_state, None)
    if state is not None:
        try:
            state.stream.close()
        except OSError:
            state.errors += 1
