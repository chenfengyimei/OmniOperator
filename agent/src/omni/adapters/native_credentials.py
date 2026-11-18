"""Serial owned OS-credential helpers; cancelled waiters never release native work or stale reads."""

from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import sys
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from ..application.ports import Record
from ..domain.contracts import BusinessError
from ..domain.credential_ipc import MAX_FRAME, VERSION, control, decode, encode, request, response
from ..domain.credentials import validate_reference, validate_secret
from .diagnostics import Redactor
from omni.adapters.platform.runtime.processes import OwnedProcess

log = logging.getLogger("omni.v2.credentials.native")
TIMEOUT_S = 30.0
Spawn = Callable[..., Awaitable[OwnedProcess]]


def failure(code: str) -> BusinessError:
    return BusinessError(
        code, "Native credential operation did not confirm completion; inspect credential health", status=503
    )


def environment() -> dict[str, str]:
    names = ("SystemRoot", "WINDIR", "HOME", "USERPROFILE", "TMP", "TEMP", "LANG", "LC_ALL", "LOCALAPPDATA", "APPDATA")
    result = {key: os.environ[key] for key in names if key in os.environ}
    result.update(PATH=os.defpath, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    if sys.platform == "linux":
        uid = getattr(os, "geteuid", lambda: None)()
        if uid is not None:
            # Ignore arbitrary inherited D-Bus addresses and keyring backend or
            # keychain overrides. A headless deployment explicitly uses FileVault.
            result["XDG_RUNTIME_DIR"] = f"/run/user/{uid}"
            result["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path=/run/user/{uid}/bus"
    if os.environ.get("OMNI_TEST_OFFLINE") == "1":
        for key in ("OMNI_TEST_OFFLINE", "OMNI_TEST_VIOLATIONS", "LITELLM_LOCAL_MODEL_COST_MAP"):
            if key in os.environ:
                result[key] = os.environ[key]
        result["PYTHONPATH"] = os.pathsep.join(
            p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p and os.path.isabs(p)
        )
    return result


@dataclass(eq=False, repr=False)
class Job:
    reference: str
    reading: bool
    invalidated: bool = False
    task: asyncio.Task[Any] | None = None


@dataclass(eq=False, repr=False)
class Host:
    expected: dict[str, object]
    starting: asyncio.Task[OwnedProcess] | None = None
    owned: OwnedProcess | None = None
    readers: list[asyncio.Task[None]] = field(default_factory=lambda: list[asyncio.Task[None]]())
    collected: asyncio.Task[int] | None = None
    cleanup: asyncio.Task[bool] | None = None
    output: bytearray = field(default_factory=bytearray)
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    overflow: asyncio.Event = field(default_factory=asyncio.Event)
    prepared: bool = False
    stdout_bytes: int = 0
    stderr_bytes: int = 0


class NativeCredentials:
    def __init__(
        self, namespace: str, redactor: Redactor, *, process_factory: Spawn | None = None, max_pending: int = 16
    ) -> None:
        # Validate without constructing any native store or subprocess.
        request(
            {
                "version": VERSION,
                "request_id": str(uuid.uuid4()),
                "operation": "get",
                "namespace": namespace,
                "reference": "probe",
                "value": None,
            }
        )
        if type(max_pending) is not int or not 1 <= max_pending <= 64:
            raise ValueError("Credential capacity must be 1 to 64")
        self._namespace, self._redactor, self._factory, self._limit = (
            namespace,
            redactor,
            process_factory or OwnedProcess.spawn,
            max_pending,
        )
        self._jobs: set[Job] = set()
        self._host: Host | None = None
        self._lock = asyncio.Lock()
        self._closing = self._closed = False
        self._failures, self._last_error = 0, ""

    def invalidate(self, reference: str) -> None:
        validate_reference(reference)
        for job in self._jobs:
            if job.reading and job.reference == reference:
                job.invalidated = True

    def stop_accepting(self) -> None:
        self._closing = True
        for job in self._jobs:
            if job.reading:
                job.invalidated = True

    def health(self) -> Record:
        return {
            "closing": self._closing,
            "closed": self._closed,
            "pending": len(self._jobs),
            "capacity": self._limit,
            "failures": self._failures,
            "error_code": self._last_error,
            "backend": "native_process",
            "resident": int(self._host is not None),
            "cleanup_pending": int(self._host is not None and self._host.cleanup is not None),
        }

    def _gate(self, job: Job) -> None:
        if self._closing:
            raise failure("credential_closing")
        if job.reading and job.invalidated:
            raise failure("credential_changed")

    async def _drain(self, host: Host, stream: asyncio.StreamReader, *, stdout: bool) -> None:
        try:
            while block := await stream.read(8192):
                if stdout:
                    host.stdout_bytes += len(block)
                    if len(host.output) + len(block) <= MAX_FRAME + 512:
                        host.output.extend(block)
                    if not host.prepared and b"\n" in host.output:
                        end = host.output.index(b"\n") + 1
                        if end <= 512 and decode(bytes(host.output[:end])) == host.expected:
                            del host.output[:end]
                            host.prepared = True
                            host.ready.set()
                        else:
                            host.overflow.set()
                    if host.stdout_bytes > MAX_FRAME + 512:
                        host.overflow.set()
                else:
                    host.stderr_bytes += len(block)  # discard provider text, never log or persist it
                    if host.stderr_bytes > 65536:
                        host.overflow.set()
        except Exception:  # noqa: BLE001 -- malformed/native pipe errors never export secret text
            host.overflow.set()

    async def _spawn(self, host: Host) -> OwnedProcess:
        host.owned = await self._factory(
            [
                sys.executable,
                "-P" if os.environ.get("OMNI_TEST_OFFLINE") == "1" else "-I",
                "-u",
                "-m",
                "omni.adapters.workers.credentials",
            ],
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=MAX_FRAME + 1,
            env=environment(),
            cwd=str(Path(__file__).resolve().parent),
        )
        process = host.owned.process
        assert process.stdout is not None and process.stderr is not None
        for stream, stdout in ((process.stdout, True), (process.stderr, False)):
            host.readers.append(
                asyncio.create_task(
                    self._drain(host, stream, stdout=stdout), context=contextvars.Context(), name="credential-pipe"
                )
            )

        async def collected() -> int:
            await asyncio.gather(*host.readers)
            return await process.wait()

        host.collected = asyncio.create_task(collected(), name="credential-result", context=contextvars.Context())
        host.collected.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        return host.owned

    async def _reclaim_actual(self, host: Host) -> bool:
        if host.starting is not None:
            try:
                await asyncio.shield(host.starting)
            except Exception:  # noqa: BLE001 -- preserve a retained owner even after a failed spawn
                if host.owned is not None:
                    return False
        if host.owned is not None:
            if host.owned.process.stdin is not None:
                host.owned.process.stdin.close()
            if not await host.owned.terminate(1):
                return False
        tasks: list[asyncio.Task[Any]] = [
            *host.readers,
            *([host.collected] if host.collected is not None else []),
        ]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        host.output.clear()
        if self._host is host:
            self._host = None
        return True

    async def _reclaim(self, host: Host, timeout_s: float) -> bool:
        if host.cleanup is None or (
            host.cleanup.done()
            and (host.cleanup.cancelled() or host.cleanup.exception() is not None or not host.cleanup.result())
        ):
            host.cleanup = asyncio.create_task(
                self._reclaim_actual(host), name="credential-reclaim", context=contextvars.Context()
            )
            host.cleanup.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        done, _ = await asyncio.wait((host.cleanup,), timeout=max(0, timeout_s))
        return bool(
            done and not host.cleanup.cancelled() and host.cleanup.exception() is None and host.cleanup.result()
        )

    async def _run(self, job: Job, query: dict[str, object]) -> str | None:
        async with self._lock:
            self._gate(job)
            if self._host is not None:
                raise failure("credential_cleanup_pending")
            host = Host(control(query, "ready"))
            self._host = host
            sent, value, error, watchers = False, None, "", []
            try:
                async with asyncio.timeout(TIMEOUT_S):
                    host.starting = asyncio.create_task(
                        self._spawn(host), name="credential-spawn", context=contextvars.Context()
                    )
                    host.starting.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
                    owned = await asyncio.shield(host.starting)
                    stdin = owned.process.stdin
                    assert stdin is not None and host.collected is not None
                    self._gate(job)
                    stdin.write(encode(query))
                    await stdin.drain()
                    ready, overflow = asyncio.create_task(host.ready.wait()), asyncio.create_task(host.overflow.wait())
                    watchers = [ready, overflow]
                    await asyncio.wait((ready, overflow, host.collected), return_when=asyncio.FIRST_COMPLETED)
                    if host.overflow.is_set() or not host.prepared:
                        raise failure("credential_protocol_invalid")
                    self._gate(job)
                    sent = True  # even a failed pipe write may have granted the helper permission
                    stdin.write(encode(control(query, "permit")))
                    await stdin.drain()
                    stdin.close()
                    await asyncio.wait((host.collected, overflow), return_when=asyncio.FIRST_COMPLETED)
                    if host.overflow.is_set() or not host.collected.done() or host.collected.result() != 0:
                        raise failure("credential_protocol_invalid")
                    reply = response(query, decode(bytes(host.output)))
                    if not reply["ok"]:
                        raise failure(cast(str, reply["error_code"]))
                    value = reply["value"]
                    if value is not None:
                        self._redactor.register(cast(str, value))
                    if job.reading and job.invalidated:
                        raise failure("credential_changed")
            except BusinessError as caught:
                if caught.code in {"credential_closing", "credential_changed", "redaction_capacity"}:
                    error = caught.code
                elif not sent:
                    error = "credential_not_dispatched"
                else:
                    error = "credential_store_unavailable" if job.reading else "credential_operation_unknown"
            except Exception:  # noqa: BLE001 -- transport/provider errors can include secrets
                error = (
                    "credential_not_dispatched"
                    if not sent
                    else "credential_store_unavailable"
                    if job.reading
                    else "credential_operation_unknown"
                )
            finally:
                for task in watchers:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*watchers, return_exceptions=True)
                if not await self._reclaim(host, 1):
                    error = "credential_cleanup_pending"
            if error:
                raise failure(error)
            return cast(str | None, value)

    async def _execute(self, reference: str, operation: str, value: str | None = None) -> str | None:
        validate_reference(reference)
        if operation == "set":
            validate_secret(value)
        if self._closing:
            raise failure("credential_closing")
        if len(self._jobs) >= self._limit:
            raise failure("credential_busy")
        if operation != "get":
            assert operation == "delete" or value is not None
            if value is not None:
                self._redactor.register(value)
            self.invalidate(reference)
        query = request(
            {
                "version": VERSION,
                "request_id": str(uuid.uuid4()),
                "operation": operation,
                "namespace": self._namespace,
                "reference": reference,
                "value": value,
            }
        )
        # Validate the actual encoded budget before accepting any OS operation.
        encode(query)
        job = Job(reference, operation == "get")
        job.task = asyncio.create_task(self._run(job, query), name="credential-native-operation")
        self._jobs.add(job)

        def finished(task: asyncio.Task[str | None]) -> None:
            self._jobs.discard(job)
            if not task.cancelled() and (error := task.exception()) is not None:
                self._failures += 1
                self._last_error = error.code if isinstance(error, BusinessError) else "credential_store_unavailable"
                log.warning(
                    "Native credential operation unconfirmed",
                    extra={"event": "credential.native.unconfirmed", "error_code": self._last_error},
                )
            else:
                self._last_error = ""

        job.task.add_done_callback(finished)
        return await asyncio.shield(job.task)

    async def get(self, reference: str) -> str | None:
        return await self._execute(reference, "get")

    async def set(self, reference: str, value: str) -> None:
        await self._execute(reference, "set", value)

    async def delete(self, reference: str) -> None:
        await self._execute(reference, "delete")

    async def close(self, timeout_s: float = 30) -> bool:
        self.stop_accepting()
        deadline = asyncio.get_running_loop().time() + max(0, timeout_s)
        tasks = [job.task for job in self._jobs if job.task is not None]
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=max(0, deadline - asyncio.get_running_loop().time()))
            if pending:
                return False
        if self._host is not None and not await self._reclaim(
            self._host, max(0, deadline - asyncio.get_running_loop().time())
        ):
            return False
        self._closed = True
        return True
