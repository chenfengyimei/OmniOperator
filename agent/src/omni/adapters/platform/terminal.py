"""Owned terminal IPC. Native PTY libraries run only in the fixed helper process."""

from __future__ import annotations

import asyncio
import contextvars
import ctypes
import hashlib
import json
import os
import shutil
import sys
from collections import deque
from collections.abc import Awaitable, Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4

import psutil

from ...domain.contracts import BusinessError
from .processes import OwnedProcess, WindowsJob

Record = dict[str, Any]
T = TypeVar("T")
VERSION = "omni-terminal-ipc-1"
MAX_FRAME = 65536
MAX_INPUT = 16384
MAX_OUTPUT = 524288


def _profiles() -> dict[str, tuple[str, list[str]]]:
    if sys.platform == "win32":
        return {
            "powershell": ("powershell.exe", ["-NoLogo", "-NoProfile", "-NoExit"]),
            "pwsh": ("pwsh.exe", ["-NoLogo", "-NoProfile", "-NoExit"]),
            "cmd": ("cmd.exe", ["/D", "/Q"]),
        }
    return {"bash": ("bash", ["--noprofile", "--norc", "-i"]), "zsh": ("zsh", ["-f", "-i"]), "sh": ("sh", ["-i"])}


def _error(code: str, message: str) -> BusinessError:
    return BusinessError(code, message, status=503)


def _decode(raw: bytes) -> Record:
    def invalid_constant(value: str) -> None:
        raise ValueError("Non-finite terminal JSON")

    def pairs(values: list[tuple[str, Any]]) -> Record:
        result: Record = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate field")
            result[key] = value
        return result

    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)
    if not isinstance(value, dict) or value.get("version") != VERSION:
        raise ValueError("invalid terminal frame")
    return value


def _environment() -> dict[str, str]:
    names = (
        "PATH",
        "SystemRoot",
        "WINDIR",
        "HOME",
        "USERPROFILE",
        "TMP",
        "TEMP",
        "LANG",
        "LC_ALL",
        "COMSPEC",
        "PATHEXT",
        "LOCALAPPDATA",
        "APPDATA",
    )
    env = {name: os.environ[name] for name in names if name in os.environ}
    for name in ("OMNI_TEST_OFFLINE", "OMNI_TEST_VIOLATIONS", "LITELLM_LOCAL_MODEL_COST_MAP"):
        if name in os.environ:
            env[name] = os.environ[name]
    if os.environ.get("OMNI_TEST_OFFLINE") == "1":
        env["PYTHONPATH"] = os.pathsep.join(
            path for path in os.environ.get("PYTHONPATH", "").split(os.pathsep) if path and os.path.isabs(path)
        )
    env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", TERM="xterm-256color")
    return env


class TerminalBackend:
    """Four owned hosts and 32 actual operations, including cancelled callers."""

    def __init__(
        self,
        workspace_validator: Callable[[str], Any] | None = None,
        *,
        shell_profiles: dict[str, tuple[str, list[str]]] | None = None,
    ) -> None:
        self._validator = workspace_validator
        # Only composition/test code can inject a profile. HTTP never supplies
        # argv; every open is re-derived from the selected fixed profile here.
        self._profiles = shell_profiles if shell_profiles is not None else _profiles()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="omni-terminal")
        self._futures: set[Future[Any]] = set()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._hosts: set[TerminalProcess] = set()
        self._closing = False
        self._executor_closed = False
        self._supported = sys.platform in {"win32", "darwin", "linux"}
        self._available = self._supported
        if sys.platform == "win32":
            self._available = hasattr(ctypes.WinDLL("kernel32"), "CreatePseudoConsole")
        self._shells = []
        for identity, (executable, _) in self._profiles.items():
            found = shutil.which(executable)
            if found is not None:
                self._shells.append({"id": identity, "label": identity, "executable": os.path.abspath(found)})

    def capabilities(self) -> Record:
        return {
            "supported": self._supported,
            "installed": self._available,
            "available": self._available and not self._closing,
            "backend": "conpty" if sys.platform == "win32" else "posix_pty",
            "shells": [dict(value) for value in self._shells],
            "error_code": "" if self._available else "terminal_dependency_missing",
            "limitations": [
                "Trusted owner shell; no operating-system sandbox.",
                "Native behavior requires separate platform acceptance.",
                "POSIX detached sessions are outside the interactive session; unresolved cleanup retains ownership.",
            ],
        }

    def health(self) -> Record:
        return {
            "resident": len(self._hosts),
            "pending": len(self._tasks) + len(self._futures),
            "cleanup_pending": sum(host._closing and not host._reclaimed for host in self._hosts),
            "closing": self._closing,
        }

    def _admit(self) -> None:
        if self._closing:
            raise _error("terminal_closing", "Terminal backend is closing")
        if len(self._tasks) + len(self._futures) >= 32:
            raise _error("terminal_busy", "Terminal operation capacity is exhausted")

    def _track(self, coroutine: Awaitable[T]) -> asyncio.Task[T]:
        task = asyncio.ensure_future(coroutine)
        self._tasks.add(task)

        def done(completed: asyncio.Task[Any]) -> None:
            self._tasks.discard(completed)
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(done)
        return task

    async def _io(self, function: Callable[[], T], *, closing: bool = False) -> T:
        if not closing:
            self._admit()
        loop = asyncio.get_running_loop()
        context = contextvars.copy_context()
        future = self._executor.submit(context.run, function)
        self._futures.add(future)
        future.add_done_callback(lambda value: loop.call_soon_threadsafe(self._futures.discard, value))
        return await asyncio.shield(asyncio.wrap_future(future))

    def _prepare(self, shell: str, cwd: str) -> Record:
        if not cwd or not Path(cwd).is_absolute():
            raise _error("terminal_workspace_required", "An explicit absolute workspace is required")
        if self._validator is not None:
            self._validator(cwd)
        directory = Path(cwd).resolve(strict=True)
        if not directory.is_dir():
            raise _error("terminal_workspace_invalid", "Terminal workspace is not a directory")
        profiles = self._profiles
        if shell not in profiles:
            raise _error("terminal_shell_unsupported", "Shell is not registered on this platform")
        executable, options = profiles[shell]
        found = shutil.which(executable)
        if not found:
            raise _error("terminal_shell_missing", "The selected shell is not installed")
        executable_path = str(Path(found).resolve(strict=True))
        stat = directory.stat()
        identity = json.dumps([str(directory), stat.st_dev, stat.st_ino], ensure_ascii=False, separators=(",", ":"))
        return {
            "argv": [executable_path, *options],
            "executable": executable_path,
            "cwd": str(directory),
            "workspace_sha256": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
            "backend": "conpty" if sys.platform == "win32" else "posix_pty",
        }

    async def prepare(self, shell: str, cwd: str) -> Record:
        if not self._available:
            raise _error("terminal_dependency_missing", "Install the optional terminal dependency")
        return await self._io(lambda: self._prepare(shell, cwd))

    async def open(
        self, spec: Record, *, before_dispatch: Callable[[], Awaitable[None]] | None = None
    ) -> TerminalProcess:
        self._admit()
        if len(self._hosts) >= 4:
            raise _error("terminal_capacity", "Four terminal hosts are already owned")
        host = TerminalProcess(self, dict(spec))
        self._hosts.add(host)
        task = self._track(host._start(before_dispatch))
        host._starting = task
        try:
            return await asyncio.shield(task)
        except BaseException:
            host._closing = True
            host._ensure_cleanup(5)
            raise

    async def close(self, timeout_s: float) -> bool:
        self._closing = True
        deadline = asyncio.get_running_loop().time() + max(0, timeout_s)
        for host in tuple(self._hosts):
            host._closing = True
            host._ensure_cleanup(max(0, deadline - asyncio.get_running_loop().time()))
        while self._hosts or self._tasks or self._futures:
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(0.01)
        if not self._executor_closed:
            self._executor.shutdown(wait=False, cancel_futures=False)
            self._executor_closed = True
        return True


class TerminalProcess:
    def __init__(self, backend: TerminalBackend, spec: Record) -> None:
        self._backend, self._spec = backend, spec
        self._owned: OwnedProcess | None = None
        self._native_job: WindowsJob | None = None
        self._starting: asyncio.Task[Any] | None = None
        self._cleanup: asyncio.Task[bool] | None = None
        self._reader: asyncio.Task[None] | None = None
        self._stderr: asyncio.Task[None] | None = None
        self._responses: dict[str, asyncio.Future[Record]] = {}
        self._request_lock = asyncio.Lock()
        self._chunks: deque[str] = deque()
        self._output_bytes = 0
        self._output_dropped = 0
        self._changed = asyncio.Event()
        self._closing = self._reclaimed = self._ended = self._ready = False
        self._pid: int | None = None
        self._created: float | None = None
        self._gate_pid: int | None = None
        self._gate_created: float | None = None
        self._exit_code: int | None = None
        self._protocol_failed = False
        self._protocol_error = ""
        self._native_possible = False

    async def _start(self, before_dispatch: Callable[[], Awaitable[None]] | None) -> TerminalProcess:
        try:
            configured_workspace = self._spec.get("configured_workspace", self._spec["cwd"])
            profile = await self._backend._io(lambda: self._backend._prepare(self._spec["shell"], configured_workspace))
            if any(profile[key] != self._spec.get(key) for key in ("argv", "cwd", "workspace_sha256", "backend")):
                raise _error("terminal_workspace_changed", "Shell or workspace identity changed before startup")
            if self._closing or self._backend._closing:
                raise _error("terminal_closing", "Terminal startup was cancelled")
            self._owned = await OwnedProcess.spawn(
                [sys.executable, "-P", "-m", "omni.adapters.workers.terminal"],
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_environment(),
                cwd=profile["cwd"],
                limit=MAX_FRAME + 1,
            )
            self._reader = asyncio.create_task(self._read_frames(), context=contextvars.Context())
            self._stderr = asyncio.create_task(self._drain_stderr(), context=contextvars.Context())
            self._native_possible = True
            opened = await self._exchange("open", {**profile, "cols": self._spec["cols"], "rows": self._spec["rows"]})
            gate_pid, gate_created = int(opened["pid"]), float(opened["created"])
            self._gate_pid, self._gate_created = gate_pid, gate_created

            def register() -> None:
                if psutil.Process(gate_pid).create_time() != gate_created:
                    raise OSError("Terminal identity changed")
                if sys.platform == "win32":
                    self._native_job = WindowsJob(gate_pid)

            await self._backend._io(register)
            # Re-check filesystem identity and application authorization after native startup.
            current = await self._backend._io(lambda: self._backend._prepare(self._spec["shell"], configured_workspace))
            if current != profile:
                raise _error("terminal_workspace_changed", "Workspace identity changed while starting")
            if before_dispatch is not None:
                await before_dispatch()
            if self._closing or self._backend._closing:
                raise _error("terminal_closing", "Terminal startup was cancelled before execution")
            started = await self._exchange("go", {})
            self._pid, self._created = int(started["pid"]), float(started["created"])
            self._ready = True
            return self
        except BaseException:
            self._closing = True
            raise

    async def _drain_stderr(self) -> None:
        assert self._owned is not None and self._owned.process.stderr is not None
        while await self._owned.process.stderr.read(4096):
            pass  # Child text can contain terminal contents; never log it.

    async def _read_frames(self) -> None:
        assert self._owned is not None and self._owned.process.stdout is not None
        try:
            while raw := await self._owned.process.stdout.readline():
                if len(raw) > MAX_FRAME or not raw.endswith(b"\n"):
                    raise ValueError("Terminal frame exceeds limit")
                frame = _decode(raw)
                kind = frame.get("kind")
                if kind == "output":
                    text = frame.get("data")
                    if not isinstance(text, str) or not text or len(text) > 4096:
                        raise ValueError("Invalid terminal output")
                    size = len(text.encode("utf-8"))
                    while self._chunks and (self._output_bytes + size > MAX_OUTPUT or len(self._chunks) >= 256):
                        self._output_dropped += len(self._chunks[0].encode("utf-8"))
                        self._output_bytes -= len(self._chunks.popleft().encode("utf-8"))
                    self._chunks.append(text)
                    self._output_bytes += size
                    self._output_dropped += max(0, int(frame.get("dropped", 0)))
                    self._changed.set()
                elif kind == "exit":
                    self._ended = True
                    self._exit_code = frame.get("exit_code")
                    self._changed.set()
                elif kind == "gap":
                    dropped = frame.get("dropped")
                    if type(dropped) is not int or dropped < 0:
                        raise ValueError("Invalid terminal output gap")
                    self._output_dropped += dropped
                    self._changed.set()
                elif kind == "reply":
                    future = self._responses.get(str(frame.get("id", "")))
                    if future is None or future.done():
                        raise ValueError("Unexpected terminal acknowledgement")
                    future.set_result(frame)
                elif kind == "fault":
                    self._protocol_error = str(frame.get("error_type", "")) + ":" + str(frame.get("native_error", ""))
                    raise ValueError("Native output stream failed")
                else:
                    raise ValueError("Unknown terminal frame")
        except (OSError, ValueError, asyncio.LimitOverrunError) as exc:
            self._protocol_error = "invalid_frame:" + type(exc).__name__
            self._protocol_failed = True
        finally:
            self._protocol_failed = True
            self._changed.set()
            for future in self._responses.values():
                if not future.done():
                    future.set_exception(_error("terminal_result_unknown", "Terminal acknowledgement was lost"))
            if not self._reclaimed:
                self._closing = True
                self._ensure_cleanup(5)

    async def _exchange(self, kind: str, payload: Record, *, timeout: float = 15) -> Record:
        async with self._request_lock:
            if self._owned is None or self._protocol_failed:
                raise _error("terminal_not_sent", "Terminal helper is unavailable before dispatch")
            request_id = str(uuid4())
            data = (
                json.dumps(
                    {"version": VERSION, "id": request_id, "kind": kind, **payload}, ensure_ascii=False, allow_nan=False
                ).encode("utf-8")
                + b"\n"
            )
            if len(data) > MAX_FRAME:
                raise _error("terminal_not_sent", "Terminal request exceeds the frame limit")
            future: asyncio.Future[Record] = asyncio.get_running_loop().create_future()
            self._responses[request_id] = future
            try:
                stdin = self._owned.process.stdin
                if stdin is None or stdin.is_closing():
                    raise _error("terminal_not_sent", "Terminal input is closed before dispatch")
                stdin.write(data)
                async with asyncio.timeout(max(0.01, timeout)):
                    await stdin.drain()
                    reply = await asyncio.shield(future)
                if not reply.get("ok"):
                    self._closing = True
                    self._ensure_cleanup(5)
                    raise _error(
                        str(reply.get("error_code", "terminal_result_unknown")), "Native terminal operation failed"
                    )
                return reply
            except (TimeoutError, OSError, asyncio.CancelledError) as exc:
                self._closing = True
                self._ensure_cleanup(5)
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise _error("terminal_result_unknown", "Terminal operation has no confirmed result") from None
            finally:
                self._responses.pop(request_id, None)
                if future.done() and not future.cancelled():
                    future.exception()

    async def read(self) -> str:
        while True:
            if self._chunks:
                value = self._chunks.popleft()
                self._output_bytes -= len(value.encode("utf-8"))
                return value
            if self._ended or self._reclaimed:
                return ""
            if self._protocol_failed:
                raise _error(
                    "terminal_output_lost",
                    "Terminal output stream was interrupted; cleanup is required (" + self._protocol_error + ")",
                )
            self._changed.clear()
            await self._changed.wait()

    async def _command(self, kind: str, payload: Record) -> Record:
        self._backend._admit()
        if self._closing or not self._ready:
            raise _error("terminal_not_sent", "Terminal is not accepting operations")

        async def run() -> Record:
            if self._closing:
                raise _error("terminal_not_sent", "Terminal closed before dispatch")
            return await self._exchange(kind, payload)

        return await asyncio.shield(self._backend._track(run()))

    async def write(self, data: str, operation_id: str) -> int:
        if not isinstance(data, str) or not data or len(data.encode("utf-8")) > MAX_INPUT:
            raise _error("terminal_not_sent", "Terminal input must be between one byte and 16 KiB")
        reply = await self._command("write", {"data": data, "operation_id": operation_id})
        accepted = reply.get("accepted")
        if type(accepted) is not int or not 0 <= accepted <= len(data.encode("utf-8")):
            self._closing = True
            self._ensure_cleanup(5)
            raise _error("terminal_result_unknown", "Native terminal returned an invalid input receipt")
        return accepted

    async def resize(self, cols: int, rows: int, operation_id: str) -> None:
        if type(cols) is not int or type(rows) is not int or not 20 <= cols <= 500 or not 5 <= rows <= 200:
            raise _error("terminal_not_sent", "Terminal dimensions are outside the supported limits")
        await self._command("resize", {"cols": cols, "rows": rows, "operation_id": operation_id})

    def snapshot(self) -> Record:
        return {
            "pid": self._pid,
            "created": self._created,
            "alive": self._ready and not self._ended and not self._reclaimed,
            "exit_code": self._exit_code,
            "cleanup_pending": self._closing and not self._reclaimed,
            "output_dropped": self._output_dropped,
        }

    def _ensure_cleanup(self, timeout_s: float) -> asyncio.Task[bool]:
        if self._cleanup is None or self._cleanup.done() and not self._reclaimed:
            self._cleanup = asyncio.create_task(self._close_actual(timeout_s), context=contextvars.Context())
            self._cleanup.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        return self._cleanup

    async def close(self, timeout_s: float) -> bool:
        self._closing = True
        task = self._ensure_cleanup(timeout_s)
        try:
            async with asyncio.timeout(max(0, timeout_s)):
                return await asyncio.shield(task)
        except TimeoutError:
            return False

    async def _close_actual(self, timeout_s: float) -> bool:
        self._closing = True
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0, timeout_s)
        try:
            current = asyncio.current_task()
            if self._starting is not None and self._starting is not current and not self._starting.done():
                async with asyncio.timeout(max(0.01, deadline - loop.time())):
                    await asyncio.shield(self._starting)
        except (Exception, asyncio.CancelledError):  # noqa: BLE001 - cleanup owns every failed startup result
            if self._starting is not None and not self._starting.done():
                return False
        native_clean = not self._native_possible
        if self._owned is not None and not self._protocol_failed:
            try:
                reply = await self._exchange("close", {}, timeout=min(2, max(0.01, deadline - loop.time())))
                native_clean = reply.get("clean") is True
            except (BusinessError, OSError, ValueError):
                native_clean = False
        if self._native_job is not None:
            native_clean = await self._backend._io(
                lambda: self._native_job is not None and self._native_job.terminate(), closing=True
            )
            while native_clean and self._native_job.active_count() != 0:
                if self._native_job.active_count() is None or loop.time() >= deadline:
                    native_clean = False
                    break
                await asyncio.sleep(0.01)
        if not native_clean and sys.platform != "win32" and self._gate_pid is not None:
            from ..workers.terminal import reclaim_posix

            gate_pid, gate_created = self._gate_pid, self._gate_created
            native_clean = await self._backend._io(
                lambda: reclaim_posix(gate_pid, gate_created, max(0, deadline - loop.time())), closing=True
            )
        if self._owned is None:
            helper_clean = self._starting is None or self._starting.done()
        else:
            helper_clean = await self._owned.terminate(max(0, deadline - loop.time()))
            if sys.platform == "win32" and self._native_job is None and helper_clean:
                # The helper's Job was assigned before its open command. It
                # therefore owns every native gate even if registration failed.
                native_clean = True
        self._reclaimed = bool(native_clean and helper_clean)
        if self._reclaimed:
            if self._native_job is not None:
                self._native_job.close()
            self._ended = True
            self._changed.set()
            for task in (self._reader, self._stderr):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in (self._reader, self._stderr) if task is not None), return_exceptions=True
            )
            self._backend._hosts.discard(self)
        return self._reclaimed
