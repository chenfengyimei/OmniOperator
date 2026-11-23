"""Read-only OS service inspection with bounded output and retained process ownership."""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import os
import stat
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any, TypeVar, cast

from omni.adapters.platform.runtime.processes import OwnedProcess
from omni.adapters.platform.services.service_templates import render
from omni.adapters.platform.services.systemd_state import PROPERTIES, systemd_state
from omni.adapters.platform.services.systemd_state import manager_report as _report
from omni.application.ports import Record
from omni.domain.contracts import BusinessError
from omni.domain.service_deployment import manager_identity, validate_plan

MAX_OUTPUT = 1_048_576
QUERY_SECONDS = 5.0
MAX_PENDING = 8
T = TypeVar("T")


def _xml(payload: bytes) -> ET.Element:
    flattened = payload.replace(b"\0", b"").upper()
    if len(payload) > MAX_OUTPUT or b"<!DOCTYPE" in flattened or b"<!ENTITY" in flattened:
        raise ValueError("Unsupported task XML")
    body = payload[payload.find(b"?>") + 2 :] if payload.lstrip().startswith(b"<?xml") and b"?>" in payload else payload
    result = ET.fromstring(body)
    if result.tag != "{http://schemas.microsoft.com/windows/2004/02/mit/task}Task":
        raise ValueError("Unexpected task definition")
    return result


def _task_identity(payload: bytes) -> tuple[Any, ...]:
    root = _xml(payload)
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    actions, principals = root.findall("t:Actions", ns), root.findall("t:Principals/t:Principal", ns)
    if len(actions) != 1 or len(principals) != 1:
        raise ValueError("Task action or principal is ambiguous")
    executions = list(actions[0])
    if len(executions) != 1 or executions[0].tag != "{" + ns["t"] + "}Exec":
        raise ValueError("Only one expected executable action is inspectable")
    execution = executions[0]
    if {node.tag for node in execution} != {
        "{" + ns["t"] + "}" + key for key in ("Command", "Arguments", "WorkingDirectory")
    }:
        raise ValueError("Executable action fields are not exact")
    if len(list(execution)) != 3:
        raise ValueError("Repeated executable action fields")
    principal = principals[0]
    allowed = {"{" + ns["t"] + "}" + key for key in ("UserId", "LogonType", "RunLevel")}
    if not {node.tag for node in principal} <= allowed or len(list(principal)) != len({node.tag for node in principal}):
        raise ValueError("Principal fields are not exact")
    if actions[0].attrib.get("Context") != principal.attrib.get("id"):
        raise ValueError("Action principal is not bound")
    return (
        actions[0].attrib.get("Context"),
        principal.attrib.get("id"),
        tuple(
            (key, execution.findtext("t:" + key, namespaces=ns)) for key in ("Command", "Arguments", "WorkingDirectory")
        ),
        tuple(
            (
                key,
                "LeastPrivilege"
                if key == "RunLevel" and principal.findtext("t:" + key, namespaces=ns) is None
                else principal.findtext("t:" + key, namespaces=ns),
            )
            for key in ("UserId", "LogonType", "RunLevel")
        ),
    )


def _action_identity(expected: bytes) -> tuple[str, str] | None:
    """Return the expected executable action (command, arguments) from the task XML."""
    root = _xml(expected)
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    execution = root.find("t:Actions/t:Exec", ns)
    if execution is None:
        return None
    return execution.findtext("t:Command", namespaces=ns) or "", execution.findtext("t:Arguments", namespaces=ns) or ""


def _windows_tokens(text: str) -> list[str]:
    """Split a Task Scheduler argument string with Windows quoting rules."""
    tokens: list[str] = []
    current: list[str] = []
    quoted, backslashes = False, 0
    for char in text:
        if char == "\\":
            backslashes += 1
            continue
        if char == '"':
            current.append("\\" * (backslashes // 2))
            if backslashes % 2 == 0:
                quoted = not quoted
            else:
                current.append('"')
            backslashes = 0
            continue
        current.append("\\" * backslashes)
        backslashes = 0
        if char in " \t" and not quoted:
            if current:
                tokens.append("".join(current))
                current = []
            continue
        current.append(char)
    current.append("\\" * backslashes)
    if current:
        tokens.append("".join(current))
    return tokens


def _action_images(command: str, plan: Record) -> set[str]:
    """Images the plan itself pinned: the task command, interpreter and real process."""
    images = {command}
    runtime = plan.get("runtime")
    if isinstance(runtime, Mapping):
        for key in ("python", "python_realpath", "process_python"):
            value = runtime.get(key)
            if isinstance(value, str) and value:
                images.add(value)
    return {os.path.normcase(os.path.realpath(image)) for image in images if image}


def action_pid(engine_pid: int | None, expected: bytes, plan: Record) -> int | None:
    """Prove the running action by its own process, not by the task engine alone."""
    if not engine_pid or type(engine_pid) is not int:
        return None
    identity = _action_identity(expected)
    if identity is None:
        return None
    command, arguments = identity
    images = _action_images(command, plan)
    tokens = _windows_tokens(arguments)
    try:
        import psutil
    except ImportError:
        return None
    try:
        children = psutil.Process(engine_pid).children(recursive=True)
    except (psutil.Error, OSError):
        return None
    for child in children:
        try:
            cmdline = child.cmdline()
        except (psutil.Error, OSError):
            continue
        if not cmdline or os.path.normcase(os.path.realpath(cmdline[0])) not in images:
            continue
        rest = [os.path.normcase(token) for token in cmdline[1:]]
        position, matched = 0, True
        for token in tokens:
            needle = os.path.normcase(token)
            while position < len(rest) and rest[position] != needle:
                position += 1
            if position == len(rest):
                matched = False
                break
            position += 1
        if matched:
            return int(child.pid)
    return None


def _read_definition(path: Path) -> bytes:
    if not path.is_absolute() or ".." in path.parts:
        raise OSError("Definition path is not an absolute managed installation")
    for item in (path, *path.parents):
        if item.is_symlink() or item.is_junction():
            raise OSError("Linked service definition is not inspectable")
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
    )
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_OUTPUT:
            raise OSError("Definition is not one bounded regular file")
        result = stream.read(MAX_OUTPUT + 1)
        after = os.fstat(stream.fileno())
    current = path.stat(follow_symlinks=False)

    def identity(info: os.stat_result) -> tuple[int, int, int, int]:
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns

    if len(result) != before.st_size or identity(before) != identity(after) or identity(before) != identity(current):
        raise OSError("Definition changed during inspection")
    return result


@dataclass(eq=False)
class _Query:
    started: asyncio.Task[OwnedProcess] | None = None
    owned: OwnedProcess | None = None
    output: bytearray = field(default_factory=bytearray)
    total: int = 0
    overflow: asyncio.Event = field(default_factory=asyncio.Event)
    readers: list[asyncio.Task[None]] = field(default_factory=lambda: list[asyncio.Task[None]]())
    collected: asyncio.Task[int] | None = None
    cleanup: asyncio.Task[bool] | None = None
    released: bool = False
    cleaning: bool = False


class ServiceQuery:
    """Two resident manager commands and eight actual requests, including cancelled waiters."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Record]] = set()
        self._owners: set[_Query] = set()
        self._slots = asyncio.Semaphore(2)
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="omni-service-query")
        self._files: set[Future[Any]] = set()
        self._closing = self._closed = False

    def health(self) -> Record:
        return {
            "closing": self._closing,
            "pending": len(self._tasks),
            "resident": len(self._owners),
            "cleanup_pending": sum(job.cleaning for job in self._owners),
            "file_reads": len(self._files),
            "max_pending": MAX_PENDING,
            "max_resident": 2,
        }

    def stop_accepting(self) -> None:
        self._closing = True

    async def query(self, plan: Record) -> Record:
        checked = validate_plan(plan)
        target = str(cast(Record, checked["request"])["target"])
        platform = {
            "windows-user": "win32",
            "macos-user": "darwin",
            "macos-system": "darwin",
            "linux-user": "linux",
            "linux-system": "linux",
        }[target]
        if sys.platform != platform:
            return _report(
                available=False,
                code="service_target_unsupported",
                limitations=("Only the native operating-system manager is queried.",),
            )
        if self._closing:
            return _report(available=False, code="service_query_closing")
        if len(self._tasks) + len(self._files) >= MAX_PENDING:
            return _report(code="service_query_busy")
        task = asyncio.create_task(self._query(cast(Record, checked)), name="service-manager-query")
        self._tasks.add(task)

        def complete(future: asyncio.Task[Record]) -> None:
            self._tasks.discard(future)
            if not future.cancelled():
                future.exception()

        task.add_done_callback(complete)
        return await asyncio.shield(task)

    async def _file(self, path: Path) -> bytes:
        return await self._blocking(partial(_read_definition, path))

    async def _native_helper(self) -> Path | None:
        from omni.adapters.platform.desktop.macos_helper import resolve

        return await self._blocking(resolve)

    async def _blocking(self, function: Callable[[], T]) -> T:
        if len(self._files) >= MAX_PENDING:
            raise BusinessError("service_query_busy", "Definition reads are bounded", status=503)
        actual = self._executor.submit(contextvars.copy_context().run, function)
        self._files.add(actual)
        loop = asyncio.get_running_loop()
        actual.add_done_callback(lambda future: loop.call_soon_threadsafe(self._files.discard, future))
        waiter = asyncio.wrap_future(actual)
        waiter.add_done_callback(lambda future: future.exception() if not future.cancelled() else None)
        async with asyncio.timeout(QUERY_SECONDS):
            return await asyncio.shield(waiter)

    @staticmethod
    def _program(target: str) -> str | None:
        if target == "windows-user":
            if sys.platform != "win32":
                return None
            import ctypes
            from ctypes import wintypes

            buffer = ctypes.create_unicode_buffer(32768)
            directory = ctypes.WinDLL("kernel32").GetSystemDirectoryW
            directory.argtypes, directory.restype = [wintypes.LPWSTR, wintypes.UINT], wintypes.UINT
            length = directory(buffer, len(buffer))
            candidate = Path(buffer.value) / "schtasks.exe" if 0 < length < len(buffer) else None
        elif target.startswith("macos-"):
            candidate = Path("/bin/launchctl")
        else:
            candidate = next(
                (path for path in (Path("/usr/bin/systemctl"), Path("/bin/systemctl")) if path.is_file()), None
            )
        return str(candidate) if candidate is not None and candidate.is_file() else None

    async def _drain(self, job: _Query, stream: asyncio.StreamReader, retain: bool) -> None:
        while block := await stream.read(65536):
            previous = job.total
            job.total += len(block)
            if retain and previous < MAX_OUTPUT:
                job.output.extend(block[: MAX_OUTPUT - previous])
            if job.total > MAX_OUTPUT:
                job.overflow.set()
            # Keep discarding/draining after overflow until actual reclamation.

    async def _spawn(self, job: _Query, argv: list[str]) -> OwnedProcess:
        env = {
            name: os.environ[name]
            for name in (
                "SystemRoot",
                "WINDIR",
                "PATH",
                "HOME",
                "USERPROFILE",
                "TMP",
                "TEMP",
                "LANG",
                "LC_ALL",
                "XDG_RUNTIME_DIR",
            )
            if name in os.environ
        }
        if sys.platform == "linux":
            # Never inherit a user-supplied remote bus address for a local query.
            effective_uid = getattr(os, "geteuid", lambda: None)()
            if effective_uid is not None:
                env["XDG_RUNTIME_DIR"] = "/run/user/" + str(effective_uid)
        job.owned = await OwnedProcess.spawn(
            argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            limit=65536,
        )
        process = job.owned.process
        assert process.stdout is not None and process.stderr is not None
        for stream, retain in ((process.stdout, True), (process.stderr, False)):
            task = asyncio.create_task(
                self._drain(job, stream, retain), context=contextvars.Context(), name="service-query-drain"
            )
            task.add_done_callback(lambda future: future.exception() if not future.cancelled() else None)
            job.readers.append(task)

        async def collected() -> int:
            await asyncio.gather(*job.readers)
            return await process.wait()

        job.collected = asyncio.create_task(collected(), context=contextvars.Context(), name="service-query-result")
        job.collected.add_done_callback(lambda future: future.exception() if not future.cancelled() else None)
        return job.owned

    async def _reclaim_actual(self, job: _Query, timeout_s: float) -> bool:
        if job.started is not None:
            try:
                await asyncio.shield(job.started)
            except Exception:  # noqa: BLE001 -- failed spawn still settles its owned task
                if job.owned is not None:
                    return False
        if job.owned is not None and not await job.owned.terminate(timeout_s):
            return False
        for task in (*job.readers, *((job.collected,) if job.collected is not None else ())):
            if not task.done():
                task.cancel()
        await asyncio.gather(
            *job.readers, *((job.collected,) if job.collected is not None else ()), return_exceptions=True
        )
        if not job.released:
            job.released = True
            self._owners.discard(job)
            self._slots.release()
        return True

    async def _reclaim(self, job: _Query, timeout_s: float) -> bool:
        job.cleaning = True
        if job.cleanup is None or (
            job.cleanup.done()
            and (job.cleanup.cancelled() or job.cleanup.exception() is not None or not job.cleanup.result())
        ):
            job.cleanup = asyncio.create_task(
                self._reclaim_actual(job, timeout_s), context=contextvars.Context(), name="service-query-reclaim"
            )
            job.cleanup.add_done_callback(lambda future: future.exception() if not future.cancelled() else None)
        done, _ = await asyncio.wait((job.cleanup,), timeout=max(0, timeout_s))
        return bool(done and not job.cleanup.cancelled() and job.cleanup.exception() is None and job.cleanup.result())

    async def _command(self, argv: list[str]) -> tuple[int, bytes]:
        try:
            async with asyncio.timeout(QUERY_SECONDS):
                await self._slots.acquire()
        except TimeoutError as exc:
            raise BusinessError("service_query_busy", "Manager query capacity is retained", status=503) from exc
        if self._closing:
            self._slots.release()
            raise BusinessError("service_query_closing", "Manager query is draining", status=503)
        job = _Query()
        self._owners.add(job)
        job.started = asyncio.create_task(
            self._spawn(job, argv), context=contextvars.Context(), name="service-query-spawn"
        )
        job.started.add_done_callback(lambda future: future.exception() if not future.cancelled() else None)
        deadline = asyncio.get_running_loop().time() + QUERY_SECONDS
        overflow = asyncio.create_task(job.overflow.wait(), context=contextvars.Context())
        try:
            done, _ = await asyncio.wait((job.started,), timeout=max(0, deadline - asyncio.get_running_loop().time()))
            if not done:
                raise BusinessError("service_query_timeout", "Manager query timed out", status=503)
            await asyncio.shield(job.started)
            assert job.collected is not None
            done, _ = await asyncio.wait(
                (job.collected, overflow),
                timeout=max(0, deadline - asyncio.get_running_loop().time()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if job.overflow.is_set():
                raise BusinessError("service_query_output_limit", "Manager query exceeded its output limit", status=503)
            if job.collected not in done:
                raise BusinessError("service_query_timeout", "Manager query timed out", status=503)
            return job.collected.result(), bytes(job.output)
        finally:
            overflow.cancel()
            await asyncio.gather(overflow, return_exceptions=True)
            if not await self._reclaim(job, 1):
                raise BusinessError(
                    "service_query_cleanup_pending", "Manager process reclamation is not confirmed", status=503
                )

    async def _query(self, plan: Record) -> Record:
        request = cast(Record, plan["request"])
        target = str(request["target"])
        if target in {"macos-user", "macos-system"}:
            try:
                return await self._macos_native(plan)
            except (BusinessError, OSError, ValueError, UnicodeError, TimeoutError):
                return _report(available=False, code="service_loaded_definition_unverified")
        if (
            target == "linux-user"
            and str(getattr(os, "geteuid", lambda: None)()) != cast(Record, request["principal"])["id"]
        ):
            return _report(
                available=False,
                code="service_principal_unverified",
                limitations=("systemctl --user can inspect only the current effective user manager.",),
            )
        identity = manager_identity(plan)
        program = self._program(target)
        if program is None:
            return _report(available=False, code="service_manager_missing")
        name = str(identity["name"])
        if target == "windows-user":
            argv = [program, "/Query", "/TN", name, "/XML"]
        else:
            argv = [
                program,
                *(["--user"] if target == "linux-user" else []),
                "show",
                name,
                "--no-pager",
                "--property=" + ",".join(PROPERTIES),
            ]
        try:
            code, output = await self._command(argv)
            if code != 0:
                return _report(
                    code="service_manager_query_failed",
                    limitations=("A nonzero manager exit does not prove the service is absent.",),
                )
            expected = render(plan)[str(identity["artifact"])]
            if target == "windows-user":
                try:
                    matches = _task_identity(output) == _task_identity(expected)
                except (ValueError, ET.ParseError):
                    return _report(installed=True, code="service_definition_unverified")
                # D11e: task XML proves the definition only. Running comes from the
                # Task Scheduler COM state plus the action's own process under the
                # engine PID; a lone engine or a queued task is never reported ready.
                from omni.adapters.platform.services.service_registration import windows_receipt

                receipt = await windows_receipt(self._command, plan, "inspect")
                state, pid, code = "unknown", None, ""
                if not receipt.get("available"):
                    code = "service_manager_state_unavailable"
                else:
                    state = str(receipt.get("state") or "unknown")
                    pid = cast(int | None, receipt.get("pid"))
                    if state == "running" and pid is None:
                        code = "service_action_unverified"
                return _report(
                    installed=True,
                    matches=matches,
                    state=state,
                    pid=pid,
                    code="service_definition_mismatch" if not matches else code,
                    limitations=(
                        "Task XML alone does not prove current process health or PID.",
                        "Running requires the Task Scheduler state and a verified action process under its engine.",
                        "Definition check covers the executable action and its bound principal.",
                    ),
                )
            return await self._linux(output, identity, expected)
        except BusinessError as exc:
            return _report(code=exc.code)
        except (OSError, ValueError, UnicodeError, TimeoutError):
            return _report(code="service_manager_query_unavailable")

    async def _macos_native(self, plan: Record) -> Record:
        from omni.adapters.platform.services.macos_service import MacOSServiceInspection

        request = cast(Record, plan["request"])
        if str(getattr(os, "geteuid", lambda: None)()) != cast(Record, request["principal"])["id"]:
            return _report(available=False, code="service_principal_unverified")
        native = await MacOSServiceInspection(self._command, self._native_helper).probe(plan)
        identity = manager_identity(plan)
        try:
            actual = await self._file(Path(str(identity["installed_path"])))
        except FileNotFoundError:
            present, matches = False, False
        else:
            present = True
            matches = actual == render(plan)[str(identity["artifact"])]
        loaded = native.get("loaded")
        result = _report(
            available=type(loaded) is bool,
            installed=True if present or loaded is True else False if loaded is False else None,
            state=str(native["state"]),
            pid=cast(int | None, native["pid"]),
            matches=False
            if not matches and (present or loaded is True)
            else cast(bool | None, native.get("loaded_definition_matches")),
            code="service_definition_mismatch" if loaded is True and not matches else str(native["error_code"]),
        )
        result.update(
            {
                "loaded": loaded,
                "definition_present": present,
                "activation": ("explicit_start_or_next_boot" if request["target"] == "macos-system" else "next_login")
                if present and loaded is False
                else "loaded"
                if loaded is True
                else "unknown",
            }
        )
        return result

    async def _linux(self, output: bytes, identity: Mapping[str, object], expected: bytes) -> Record:
        parsed = systemd_state(output, identity)
        result = parsed.report
        if parsed.definition_path is None:
            return result
        try:
            actual = await self._file(Path(parsed.definition_path))
        except OSError:
            return {**result, "error_code": "service_definition_unreadable"}
        matches = hashlib.sha256(actual).digest() == hashlib.sha256(expected).digest()
        return {**result, "definition_matches": matches, "error_code": "" if matches else "service_definition_mismatch"}

    async def close(self, timeout_s: float = 30) -> bool:
        self.stop_accepting()
        deadline = asyncio.get_running_loop().time() + max(0, timeout_s)
        for job in list(self._owners):
            await self._reclaim(job, max(0, deadline - asyncio.get_running_loop().time()))
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=max(0, deadline - asyncio.get_running_loop().time()))
        pending = [asyncio.wrap_future(future) for future in self._files]
        for future in pending:
            future.add_done_callback(lambda result: result.exception() if not result.cancelled() else None)
        if pending:
            await asyncio.wait(pending, timeout=max(0, deadline - asyncio.get_running_loop().time()))
        if self._owners or self._tasks or any(not future.done() for future in self._files):
            return False
        if not self._closed:
            self._executor.shutdown(wait=False)
            self._closed = True
        return True
