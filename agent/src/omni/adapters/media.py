"""One bounded, supervised media processing subprocess; heavy libraries never load in the host."""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import math
import os
import re
import sys
import uuid
from pathlib import Path
from typing import NoReturn, cast

from omni.adapters.platform.runtime.processes import OwnedProcess

from ..domain.contracts import BusinessError
from ..domain.media import MAX_DOWNLOAD_BYTES, frames_plan

log = logging.getLogger("omni.v2.media")

FRAME_LIMIT = 8 * 1024 * 1024
STDERR_READ_BYTES = 4096
STDERR_COUNTER_LIMIT = (1 << 53) - 1
PROBE_BUDGET_S = 20
FRAMES_BUDGET_S = 180
TRANSCRIBE_BUDGET_S = 900


def _reject_json_constant(_: str) -> NoReturn:
    raise ValueError("Non-finite media response")


def _finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("Non-finite media response")
    return parsed


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate media response field")
        result[key] = value
    return result


def _response(line: bytes, identity: str) -> dict[str, object]:
    if len(line) > FRAME_LIMIT or not line.endswith(b"\n"):
        raise ValueError("Incomplete or oversized media response")
    value: object = json.loads(
        line,
        parse_constant=_reject_json_constant,
        parse_float=_finite_json_float,
        object_pairs_hook=_unique_json_object,
    )
    if not isinstance(value, dict):
        raise ValueError("Invalid media response")
    reply = cast(dict[str, object], value)
    result = reply.get("result")
    if (
        type(reply.get("version")) is not int
        or reply["version"] != 1
        or reply.get("id") != identity
        or not isinstance(result, dict)
    ):
        raise ValueError("Media response identity or version mismatch")
    outcome = cast(dict[str, object], result)
    if type(outcome.get("ok")) is not bool:
        raise ValueError("Invalid media response outcome")
    if outcome["ok"] is False:
        code = outcome.get("code")
        if not isinstance(code, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code) is None:
            raise ValueError("Invalid media response error code")
    return outcome


class MediaProcessor:
    """Serial request/response over stdio to ``omni.adapters.workers.media``.

    One owned process handles one request at a time; cancellation terminates it
    for real (the in-flight request is destroyed with the process) and the next
    request respawns a fresh one.
    """

    def __init__(self, argv: list[str] | None = None) -> None:
        self._argv = argv or [sys.executable, "-m", "omni.adapters.workers.media"]
        self._owned: OwnedProcess | None = None
        self._starting: asyncio.Task[OwnedProcess] | None = None
        self._stderr: asyncio.Task[None] | None = None
        self._stderr_bytes = 0
        self._stderr_failed = False
        self._lock = asyncio.Lock()
        self._probed: dict[str, object] | None = None
        self._closing = False
        self._cleanup_pending = False

    def status(self) -> dict[str, object]:
        self._refresh_stderr()
        return {
            "available": not self._closing and not self._cleanup_pending,
            "probed": self._probed is not None,
            "components": self._probed or {},
            "running": self._owned is not None and self._owned.process.returncode is None,
            "starting": self._starting is not None and not self._starting.done(),
            "stderr_draining": self._stderr is not None and not self._stderr.done(),
            "stderr_bytes": self._stderr_bytes,
            "stderr_failed": self._stderr_failed,
            "cleanup_pending": self._cleanup_pending,
            "recovery": "Install the optional asr dependency for av and faster-whisper; frames need av and Pillow",
        }

    async def _drain_stderr(self, stream: asyncio.StreamReader) -> None:
        total = 0
        while size := len(await stream.read(STDERR_READ_BYTES)):
            self._stderr_bytes = min(STDERR_COUNTER_LIMIT, self._stderr_bytes + size)
            total = min(STDERR_COUNTER_LIMIT, total + size)
        if total:
            log.warning("Media worker stderr bytes observed", extra={"event": "media.stderr", "bytes": total})

    def _stderr_finished(self, task: asyncio.Task[None]) -> None:
        failed = task.cancelled() or task.exception() is not None
        if task is self._stderr and failed:
            self._stderr_failed = True
            self._cleanup_pending = True

    def _refresh_stderr(self) -> None:
        if self._stderr is not None and self._stderr.done():
            self._stderr_finished(self._stderr)

    async def _spawn(self) -> OwnedProcess:
        env = {
            name: os.environ[name]
            for name in ("PATH", "SystemRoot", "WINDIR", "HOME", "USERPROFILE", "TMP", "TEMP", "LANG")
            if name in os.environ
        }
        env.update(
            PYTHONUTF8="1",
            PYTHONDONTWRITEBYTECODE="1",
            HF_HUB_OFFLINE="1",
            OMP_NUM_THREADS="2",
        )
        owned = await OwnedProcess.spawn(
            self._argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=FRAME_LIMIT + 1,
            env=env,
        )
        self._owned = owned
        assert owned.process.stderr is not None
        self._stderr_failed = False
        self._stderr = asyncio.create_task(
            self._drain_stderr(owned.process.stderr), name="media-stderr", context=contextvars.Context()
        )
        self._stderr.add_done_callback(self._stderr_finished, context=contextvars.Context())
        return owned

    async def _start(self) -> OwnedProcess:
        if self._starting is None:
            self._starting = asyncio.create_task(self._spawn(), name="media-spawn", context=contextvars.Context())
            self._starting.add_done_callback(
                lambda task: task.exception() if not task.cancelled() else None, context=contextvars.Context()
            )
        try:
            owned = await asyncio.shield(self._starting)
        except asyncio.CancelledError:
            # Native creation can finish after the caller leaves; retain its actual result.
            self._cleanup_pending = True
            raise
        except Exception:
            self._starting = None
            self._cleanup_pending = self._owned is not None
            raise
        self._starting = None
        return owned

    async def _terminate(self, timeout_s: float = 5) -> bool:
        deadline = asyncio.get_running_loop().time() + max(0, timeout_s)
        if self._starting is not None:
            self._cleanup_pending = True
            starting = self._starting
            done, _ = await asyncio.wait((starting,), timeout=max(0, deadline - asyncio.get_running_loop().time()))
            if not done or starting.cancelled():
                return False
            if starting.exception() is None:
                self._owned = starting.result()
            else:
                # Keep any owner published before failure; do not log native error text.
                log.warning("Media worker creation failed", extra={"event": "media.spawn_failed"})
            self._starting = None
        owned = self._owned
        # Keep the actual owner reachable even if reclamation is cancelled or raises.
        if owned is not None:
            self._cleanup_pending = True
            if not await owned.terminate(max(0, deadline - asyncio.get_running_loop().time())):
                return False
        stderr = self._stderr
        if stderr is not None:
            self._cleanup_pending = True
            done, _ = await asyncio.wait((stderr,), timeout=max(0, deadline - asyncio.get_running_loop().time()))
            if not done:
                return False
            self._stderr_finished(stderr)
        self._owned = None
        self._stderr = None
        self._stderr_failed = False
        self._cleanup_pending = False
        return True

    async def _request(self, operation: str, payload: dict[str, object], budget_s: float) -> dict[str, object]:
        if self._closing:
            raise BusinessError("media_processor_closing", "Media processing is closing", status=503)
        identity = str(uuid.uuid4())
        frame = (
            json.dumps({"version": 1, "id": identity, "operation": operation, **payload}, ensure_ascii=False) + "\n"
        ).encode("utf-8")
        if len(frame) > FRAME_LIMIT:
            raise BusinessError("media_request_invalid", "Media request exceeds its transport bound", status=422)
        async with self._lock:
            if self._closing:
                raise BusinessError("media_processor_closing", "Media processing is closing", status=503)
            self._refresh_stderr()
            owned = self._owned
            if self._cleanup_pending or (
                owned is not None and (owned.process.returncode is not None or owned.reclaimed)
            ):
                if not await self._terminate():
                    raise BusinessError(
                        "media_cleanup_pending", "Previous media worker cleanup is still pending", status=503
                    )
                owned = None
                if self._closing:
                    raise BusinessError("media_processor_closing", "Media processing is closing", status=503)
            if owned is None:
                owned = await self._start()
            if self._closing:
                raise BusinessError("media_processor_closing", "Media processing is closing", status=503)
            self._refresh_stderr()
            if self._cleanup_pending:
                raise BusinessError("media_cleanup_pending", "Media worker cleanup is still pending", status=503)
            assert owned.process.stdin is not None and owned.process.stdout is not None
            try:
                async with asyncio.timeout(budget_s):
                    owned.process.stdin.write(frame)
                    await owned.process.stdin.drain()
                    line = await owned.process.stdout.readline()
            except (OSError, ValueError) as exc:
                # The in-flight request is unknown; destroy the process and respawn later.
                await self._terminate()
                if isinstance(exc, TimeoutError):
                    raise BusinessError(
                        "media_processing_timeout", "Media processing exceeded its time budget", status=504
                    ) from exc
                raise BusinessError(
                    "media_processing_failed", "Media processing worker communication failed", status=503
                ) from exc
            except asyncio.CancelledError:
                await self._terminate()
                raise
            if not line:
                await self._terminate()
                raise BusinessError("media_processing_failed", "Media processing worker exited", status=503)
            try:
                outcome = _response(line, identity)
            except (ValueError, RecursionError) as exc:
                await self._terminate()
                raise BusinessError("media_processing_failed", "Media worker response was invalid", status=503) from exc
            if not outcome.get("ok"):
                code = cast(str, outcome["code"])
                if code == "media_component_missing":
                    raise BusinessError(code, "A media processing component is not installed", status=503)
                if code == "media_asr_model_missing":
                    raise BusinessError(
                        code, "The selected ASR model is not in the offline cache; prepare it explicitly", status=503
                    )
                if code == "media_processing_timeout":
                    raise BusinessError(code, "Media processing exceeded its time budget", status=504)
                raise BusinessError(code, "Media processing failed", status=503)
            return outcome

    async def probe(self) -> dict[str, object]:
        if self._probed is None:
            self._probed = dict(await self._request("probe", {}, PROBE_BUDGET_S))
        return dict(self._probed)

    async def transcribe(self, audio: Path, output: Path, plan: dict[str, object]) -> dict[str, object]:
        return await self._request(
            "transcribe",
            {
                "audio": str(audio),
                "output": str(output),
                "model": plan["model"],
                "device": plan["device"],
                "compute_type": plan["compute_type"],
            },
            TRANSCRIBE_BUDGET_S,
        )

    async def frames(
        self,
        video: Path,
        sheet_output: Path,
        count: int,
        width: int,
        timestamps: list[float] | None = None,
        select: str = "equidistant",
    ) -> dict[str, object]:
        plan = frames_plan(count, width, timestamps, select)
        return await self._request(
            "frames",
            {"video": str(video), "sheet_output": str(sheet_output), **plan},
            FRAMES_BUDGET_S,
        )

    def stop_accepting(self) -> None:
        self._closing = True

    async def close(self, timeout_s: float = 30) -> bool:
        self.stop_accepting()
        deadline = asyncio.get_running_loop().time() + max(0, timeout_s)
        try:
            async with asyncio.timeout(max(0, deadline - asyncio.get_running_loop().time())):
                async with self._lock:
                    while not await self._terminate(max(0, deadline - asyncio.get_running_loop().time())):
                        if asyncio.get_running_loop().time() >= deadline:
                            return False
                        await asyncio.sleep(0.05)
                    return True
        except TimeoutError:
            return False


__all__ = ["MediaProcessor", "MAX_DOWNLOAD_BYTES", "FRAMES_BUDGET_S", "TRANSCRIBE_BUDGET_S"]
