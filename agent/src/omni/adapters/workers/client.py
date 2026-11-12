"""Versioned, bounded JSON IPC to a supervised capability process."""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, replace
from types import MappingProxyType
from typing import cast

from ...domain.contracts import JSON, ActionResult, BusinessError, Effect, InvocationContext, Outcome
from ..platform.processes import OwnedProcess
from .protocol import MAX_FRAME, VERSION, response

log = logging.getLogger("omni.v2.worker")

MAX_HOST_CALLBACKS = 16
MAX_HOST_CALLBACK_BYTES = 8192
MAX_HOST_LOG_CHARS = 1000
_NO_HOST_CONTEXT: Mapping[str, JSON] = MappingProxyType({})


class WorkerClient:
    def __init__(
        self,
        kind: str,
        *,
        tool_writes: Mapping[str, bool],
        argv: list[str] | None = None,
        startup: Callable[[asyncio.subprocess.Process], Awaitable[None]] | None = None,
        cwd: str | None = None,
        forbid_artifacts: bool = False,
        extra_env: Mapping[str, str] | None = None,
    ) -> None:
        self.kind = kind
        if not tool_writes or len(tool_writes) > 200 or any(type(value) is not bool for value in tool_writes.values()):
            raise ValueError("Worker requires a bounded, explicit capability manifest")
        self._tool_writes = MappingProxyType(dict(tool_writes))
        if extra_env is not None and (
            len(extra_env) > 16
            or any(not key.startswith("OMNI_") or len(value) > 4000 for key, value in extra_env.items())
        ):
            raise ValueError("Worker extra environment entries are not bounded")
        self._extra_env = MappingProxyType(dict(extra_env or {}))
        self.argv = list(argv) if argv is not None else [sys.executable, "-m", "omni.adapters.workers.main", kind]
        self._startup, self._cwd, self._forbid_artifacts = startup, cwd, forbid_artifacts
        self._owned: OwnedProcess | None = None
        self._starting: asyncio.Task[OwnedProcess] | None = None
        self._cleanup: asyncio.Task[bool] | None = None
        self._lock = asyncio.Lock()
        self._stderr: asyncio.Task[None] | None = None
        self._closed = False
        self._ready = self._poisoned = False
        self._stderr_bytes = self._stderr_reports = 0

    async def _drain_errors(self, stream: asyncio.StreamReader) -> None:
        last_report = -1.0
        loop = asyncio.get_running_loop()
        while chunk := await stream.read(4096):
            self._stderr_bytes += len(chunk)
            # Always drain, but emit at most one metadata-only record per second.
            # Raw child text and the caller's trace/context never enter this task.
            if loop.time() - last_report >= 1:
                last_report = loop.time()
                self._stderr_reports += 1
                log.warning(
                    "Worker stderr bytes observed: %d",
                    self._stderr_bytes,
                    extra={"event": "worker.stderr", "status": self.kind},
                )

    async def _spawn(self) -> OwnedProcess:
        env = {
            name: os.environ[name]
            for name in (
                "PATH",
                "SystemRoot",
                "WINDIR",
                "HOME",
                "USERPROFILE",
                "TMP",
                "TEMP",
                "LANG",
                "DISPLAY",
                "WAYLAND_DISPLAY",
                "XDG_RUNTIME_DIR",
                "DBUS_SESSION_BUS_ADDRESS",
            )
            if name in os.environ
        }
        # Offline guards must also apply to subprocesses. No model/service credentials inherited.
        for name in ("OMNI_TEST_OFFLINE", "OMNI_TEST_VIOLATIONS", "LITELLM_LOCAL_MODEL_COST_MAP"):
            if name in os.environ:
                env[name] = os.environ[name]
        if os.environ.get("OMNI_TEST_OFFLINE") == "1":
            env["PYTHONPATH"] = os.pathsep.join(
                path for path in os.environ.get("PYTHONPATH", "").split(os.pathsep) if path and os.path.isabs(path)
            )
        env["PYTHONUTF8"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.update(self._extra_env)
        self._owned = await OwnedProcess.spawn(
            self.argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=MAX_FRAME + 1,
            env=env,
            cwd=self._cwd,
        )
        assert self._owned.process.stderr is not None
        self._stderr = asyncio.create_task(
            self._drain_errors(self._owned.process.stderr), name="stderr-" + self.kind, context=contextvars.Context()
        )
        return self._owned

    async def _start(self) -> None:
        if self._closed:
            raise RuntimeError("Worker is closed")
        if (
            self._poisoned or (self._owned is not None and self._owned.process.returncode is not None)
        ) and not await self._reclaim(5):
            raise BusinessError("worker_cleanup_pending", "Previous worker cleanup is still pending", status=503)
        if self._ready and self._owned is not None:
            return
        if self._starting is None:
            self._cleanup = None
            self._starting = asyncio.create_task(
                self._spawn(), name="spawn-" + self.kind, context=contextvars.Context()
            )
            self._starting.add_done_callback(lambda future: future.exception() if not future.cancelled() else None)
        # Cancelling a request must not cancel the actual OS spawn or lose its result.
        await asyncio.shield(self._starting)
        if self._closed:
            raise RuntimeError("Worker is closed")
        assert self._owned is not None
        if self._startup is not None:
            async with asyncio.timeout(5):
                await self._startup(self._owned.process)
        self._ready = True

    async def _reclaim_actual(self, timeout_s: float) -> bool:
        deadline = asyncio.get_running_loop().time() + max(0, timeout_s)
        if self._starting is not None:
            try:
                await asyncio.shield(self._starting)
            except Exception:  # noqa: BLE001 -- failed OS spawn still settles its retained ownership record
                if self._owned is None:
                    self._starting = None
                    self._poisoned = self._ready = False
                    return True
        if self._owned is not None:
            process = self._owned.process
            if process.stdin:
                process.stdin.close()
            if not await self._owned.terminate(max(0, deadline - asyncio.get_running_loop().time())):
                self._poisoned = True
                return False
        # Keep draining stderr while any process ownership remains unresolved.
        if self._stderr is not None:
            self._stderr.cancel()
            await asyncio.gather(self._stderr, return_exceptions=True)
        self._owned = self._starting = self._stderr = None
        self._poisoned = self._ready = False
        return True

    async def _reclaim(self, timeout_s: float) -> bool:
        if self._cleanup is None or (
            self._cleanup.done()
            and (self._cleanup.cancelled() or self._cleanup.exception() or not self._cleanup.result())
        ):
            self._cleanup = asyncio.create_task(
                self._reclaim_actual(timeout_s), name="reclaim-" + self.kind, context=contextvars.Context()
            )
            self._cleanup.add_done_callback(lambda future: future.exception() if not future.cancelled() else None)
        done, _ = await asyncio.wait((self._cleanup,), timeout=max(0, timeout_s))
        return bool(
            done and not self._cleanup.cancelled() and self._cleanup.exception() is None and self._cleanup.result()
        )

    @property
    def cleanup_pending(self) -> bool:
        return (
            self._poisoned
            or bool(self._cleanup and not self._cleanup.done())
            or bool(
                self._owned
                and not self._owned.reclaimed
                and (not self._ready or self._owned.process.returncode is not None)
            )
        )

    def health(self) -> dict[str, bool | int]:
        return {
            "starting": self._starting is not None and not self._ready,
            "running": self._owned is not None and self._owned.process.returncode is None,
            "cleanup_pending": self.cleanup_pending,
            "closed": self._closed,
            "stderr_bytes": self._stderr_bytes,
            "stderr_reports": self._stderr_reports,
        }

    def review_quiescent(self) -> bool:
        return (
            not self._lock.locked()
            and not self.cleanup_pending
            and not (self._starting is not None and not self._ready)
        )

    async def invoke(
        self,
        name: str,
        arguments: Mapping[str, JSON],
        *,
        operation_id: str,
        context: InvocationContext,
        before_dispatch: Callable[[], Awaitable[None]] | None = None,
        host_context: Mapping[str, JSON] | None = None,
    ) -> ActionResult:
        if name not in self._tool_writes:
            return ActionResult(
                Outcome.ERROR, "Capability is not registered for this worker", error_code="unsupported_capability"
            )
        writes = self._tool_writes[name]
        async with self._lock:
            sent = False
            try:
                await self._start()
                assert self._owned is not None
                process = self._owned.process
                assert process.stdin is not None and process.stdout is not None
                request = {
                    "version": VERSION,
                    "id": operation_id,
                    "method": name,
                    "writes": writes,
                    "arguments": dict(arguments),
                    "context": asdict(context),
                }
                frame = json.dumps(request, ensure_ascii=False, allow_nan=False).encode() + b"\n"
                if len(frame) > MAX_FRAME:
                    return ActionResult(Outcome.ERROR, "Request frame too large", error_code="request_too_large")
                if before_dispatch is not None:
                    await before_dispatch()
                # A write attempt may reach the child even if the local transport raises.
                sent = True
                process.stdin.write(frame)
                await process.stdin.drain()
                budget = {"calls": 0, "bytes": 0}
                logs: list[str] = []
                while True:
                    frame = await process.stdout.readline()
                    if not frame:
                        raise ValueError("Worker closed before returning a result")
                    try:
                        decoded = json.loads(frame)
                    except (ValueError, UnicodeError):
                        raise ValueError("Worker produced a malformed frame") from None
                    if isinstance(decoded, dict):
                        control = cast(dict[str, object], decoded)
                        if control.get("kind") == "callback":
                            await self._host_callback(process, control, host_context or _NO_HOST_CONTEXT, budget, logs)
                            continue
                    result = response(frame, operation_id, writes=writes)
                    break
                if budget["calls"]:
                    result = replace(
                        result,
                        data={
                            **dict(result.data),
                            "host_callbacks": {"calls": budget["calls"], "logs": logs},
                        },
                    )
                if self._forbid_artifacts and result.artifacts:
                    raise ValueError("This worker cannot publish artifacts")
                if result.effect == Effect.UNKNOWN:
                    # An idle protocol channel does not prove that background effects
                    # stopped. Keep the process owner until actual reclamation succeeds.
                    self._poisoned = True
                    await self._reclaim(5)
                return result
            except BaseException as exc:
                self._poisoned = True
                await self._reclaim(5)
                if isinstance(exc, asyncio.CancelledError):
                    raise
                if not sent and isinstance(exc, (PermissionError, BusinessError)):
                    return ActionResult(
                        Outcome.DENIED if isinstance(exc, PermissionError) else Outcome.ERROR,
                        "Capability was not dispatched; authorization or worker readiness changed",
                        error_code=exc.code if isinstance(exc, BusinessError) else "capability_authorization_denied",
                    )
                return ActionResult(
                    Outcome.UNKNOWN if sent and writes else Outcome.ERROR,
                    "Worker disconnected after dispatch" if sent else "Worker could not start",
                    Effect.UNKNOWN if sent and writes else Effect.NONE,
                    error_code="worker_disconnected" if sent else "worker_unavailable",
                )

    async def _host_callback(
        self,
        process: asyncio.subprocess.Process,
        frame: dict[str, object],
        host_context: Mapping[str, JSON],
        budget: dict[str, int],
        logs: list[str],
    ) -> None:
        """Allowlisted, budgeted host capabilities; malformed or over-budget traffic fails closed."""
        call_id, name, raw_arguments = frame.get("id"), frame.get("name"), frame.get("arguments")
        if (
            not isinstance(call_id, str)
            or len(call_id) != 36
            or not isinstance(name, str)
            or not isinstance(raw_arguments, dict)
        ):
            raise ValueError("Malformed host callback frame")
        arguments = cast(dict[str, object], raw_arguments)
        budget["calls"] += 1
        if budget["calls"] > MAX_HOST_CALLBACKS:
            raise ValueError("Host callback budget exhausted")
        if name == "context":
            if arguments:
                raise ValueError("Host context callback takes no arguments")
            reply = {"ok": True, "result": dict(host_context)}
        elif name == "log":
            text = arguments.get("text")
            if set(arguments) != {"text"} or not isinstance(text, str) or not 1 <= len(text) <= MAX_HOST_LOG_CHARS:
                raise ValueError("Host log callback is not bounded")
            budget["bytes"] += len(text.encode("utf-8"))
            if budget["bytes"] > MAX_HOST_CALLBACK_BYTES:
                raise ValueError("Host callback byte budget exhausted")
            logs.append(text)
            reply = {"ok": True, "result": {"accepted": True}}
        else:
            reply = {"ok": False, "error": "callback_denied"}
        payload = (
            json.dumps(
                {"version": VERSION, "kind": "callback_result", "id": call_id, **reply},
                ensure_ascii=False,
                allow_nan=False,
            ).encode()
            + b"\n"
        )
        if len(payload) > MAX_FRAME:
            raise ValueError("Host callback reply too large")
        assert process.stdin is not None
        process.stdin.write(payload)
        await process.stdin.drain()

    async def close(self, timeout_s: float = 5) -> bool:
        self._closed = True
        self._poisoned = self._owned is not None or self._starting is not None
        return await self._reclaim(timeout_s)
