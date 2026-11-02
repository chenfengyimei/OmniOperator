"""Versioned, bounded JSON IPC to a supervised capability process."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections.abc import Mapping
from dataclasses import asdict

from ...domain.contracts import JSON, ActionResult, Effect, InvocationContext, Outcome
from ..platform.processes import OwnedProcess

log = logging.getLogger("omni.v2.worker")
MAX_FRAME = 1_048_576


class WorkerClient:
    def __init__(self, kind: str, *, argv: list[str] | None = None) -> None:
        self.kind = kind
        self.argv = argv or [sys.executable, "-m", "omni.adapters.workers.main", kind]
        self._owned: OwnedProcess | None = None
        self._lock = asyncio.Lock()
        self._stderr: asyncio.Task | None = None
        self._closed = False

    async def _drain_errors(self, stream: asyncio.StreamReader) -> None:
        while await stream.read(4096):
            # Child stderr is untrusted. Content is intentionally excluded from default diagnostics.
            log.warning("Worker wrote stderr", extra={"event": "worker.stderr", "status": self.kind})

    async def _start(self) -> None:
        if self._closed:
            raise RuntimeError("Worker is closed")
        if self._owned is not None and self._owned.process.returncode is None:
            return
        if self._owned is not None and not await self._owned.terminate():
            raise RuntimeError("Previous worker ownership could not be reclaimed")
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
        for name in ("PYTHONPATH", "OMNI_TEST_OFFLINE", "OMNI_TEST_VIOLATIONS", "LITELLM_LOCAL_MODEL_COST_MAP"):
            if name in os.environ:
                env[name] = os.environ[name]
        env["PYTHONUTF8"] = "1"
        self._owned = await OwnedProcess.spawn(
            self.argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=MAX_FRAME + 1,
            env=env,
        )
        assert self._owned.process.stderr is not None
        self._stderr = asyncio.create_task(self._drain_errors(self._owned.process.stderr), name="stderr-" + self.kind)

    async def invoke(
        self, name: str, arguments: Mapping[str, JSON], *, operation_id: str, context: InvocationContext
    ) -> ActionResult:
        async with self._lock:
            sent = False
            try:
                await self._start()
                assert self._owned is not None
                process = self._owned.process
                assert process.stdin is not None and process.stdout is not None
                request = {
                    "version": 1,
                    "id": operation_id,
                    "method": name,
                    "arguments": dict(arguments),
                    "context": asdict(context),
                }
                frame = json.dumps(request, ensure_ascii=False).encode() + b"\n"
                if len(frame) > MAX_FRAME:
                    return ActionResult(Outcome.ERROR, "Request frame too large", error_code="request_too_large")
                process.stdin.write(frame)
                sent = True
                await process.stdin.drain()
                response = await process.stdout.readline()
                if not response or len(response) > MAX_FRAME:
                    raise ValueError("Missing or oversized worker response")
                data = json.loads(response)
                if data.get("version") != 1 or data.get("id") != operation_id:
                    raise ValueError("Worker protocol identity mismatch")
                result = data["result"]
                return ActionResult(
                    Outcome(result["status"]),
                    result["text"],
                    Effect(result.get("effect", "none")),
                    error_code=result.get("error_code", ""),
                    retryable=False,
                    verification=result.get("verification", ""),
                    artifacts=tuple(result.get("artifacts", [])),
                    data=result.get("data", {}),
                )
            except BaseException:
                if self._owned:
                    await self._owned.terminate()
                if isinstance(sys.exc_info()[1], asyncio.CancelledError):
                    raise
                return ActionResult(
                    Outcome.UNKNOWN if sent else Outcome.ERROR,
                    "Worker disconnected after dispatch" if sent else "Worker could not start",
                    Effect.UNKNOWN if sent else Effect.NONE,
                    error_code="worker_disconnected" if sent else "worker_unavailable",
                )

    async def close(self, timeout_s: float = 5) -> bool:
        self._closed = True
        if self._owned is None:
            return True
        process = self._owned.process
        if process.stdin:
            process.stdin.close()
        try:
            async with asyncio.timeout(min(1, timeout_s)):
                await process.wait()
        except TimeoutError:
            pass
        complete = await self._owned.terminate(timeout_s)
        if self._stderr:
            self._stderr.cancel()
            await asyncio.gather(self._stderr, return_exceptions=True)
        return complete
