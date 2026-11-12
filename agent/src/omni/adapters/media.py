"""One bounded, supervised media processing subprocess; heavy libraries never load in the host."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import cast

from ..domain.contracts import BusinessError
from ..domain.media import MAX_DOWNLOAD_BYTES, frames_plan
from .platform.processes import OwnedProcess

log = logging.getLogger("omni.v2.media")

FRAME_LIMIT = 8 * 1024 * 1024
PROBE_BUDGET_S = 20
FRAMES_BUDGET_S = 180
TRANSCRIBE_BUDGET_S = 900


class MediaProcessor:
    """Serial request/response over stdio to ``omni.adapters.workers.media``.

    One owned process handles one request at a time; cancellation terminates it
    for real (the in-flight request is destroyed with the process) and the next
    request respawns a fresh one.
    """

    def __init__(self, argv: list[str] | None = None) -> None:
        self._argv = argv or [sys.executable, "-m", "omni.adapters.workers.media"]
        self._owned: OwnedProcess | None = None
        self._lock = asyncio.Lock()
        self._probed: dict[str, object] | None = None
        self._closing = False
        self._cleanup_pending = False

    def status(self) -> dict[str, object]:
        return {
            "available": not self._closing,
            "probed": self._probed is not None,
            "components": self._probed or {},
            "running": self._owned is not None and self._owned.process.returncode is None,
            "cleanup_pending": self._cleanup_pending,
            "recovery": "Install the optional asr dependency for av and faster-whisper; frames need av and Pillow",
        }

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
        return owned

    async def _terminate(self, timeout_s: float = 5) -> bool:
        owned = self._owned
        self._owned = None
        if owned is None:
            return True
        if not await owned.terminate(timeout_s):
            # The unclaimed process stays reachable for the next close attempt.
            self._owned = owned
            self._cleanup_pending = True
            return False
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
            owned = self._owned
            if owned is None or owned.process.returncode is not None or owned.reclaimed:
                owned = await self._spawn()
            assert owned.process.stdin is not None and owned.process.stdout is not None
            try:
                owned.process.stdin.write(frame)
                await owned.process.stdin.drain()
                line = await asyncio.wait_for(owned.process.stdout.readline(), timeout=budget_s)
            except (BrokenPipeError, ConnectionResetError, TimeoutError) as exc:
                # The in-flight request is unknown; destroy the process and respawn later.
                await self._terminate()
                if isinstance(exc, TimeoutError):
                    raise BusinessError(
                        "media_processing_timeout", "Media processing exceeded its time budget", status=504
                    ) from exc
                raise BusinessError(
                    "media_processing_failed", "Media processing worker disconnected", status=503
                ) from exc
            except asyncio.CancelledError:
                await self._terminate()
                raise
            if not line:
                await self._terminate()
                raise BusinessError("media_processing_failed", "Media processing worker exited", status=503)
            try:
                response = json.loads(line)
            except ValueError as exc:
                await self._terminate()
                raise BusinessError("media_processing_failed", "Media worker response was invalid", status=503) from exc
            reply = cast(dict[str, object], response) if isinstance(response, dict) else None
            result = reply.get("result") if reply is not None else None
            if reply is None or reply.get("id") != identity or not isinstance(result, dict):
                await self._terminate()
                raise BusinessError("media_processing_failed", "Media worker response identity mismatch", status=503)
            outcome = cast(dict[str, object], result)
            if not outcome.get("ok"):
                code = str(outcome.get("code") or "media_processing_error")
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

    async def frames(self, video: Path, sheet_output: Path, count: int, width: int) -> dict[str, object]:
        plan = frames_plan(count, width)
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
        if self._lock.locked():
            try:
                async with asyncio.timeout(max(0, deadline - asyncio.get_running_loop().time())):
                    async with self._lock:
                        pass
            except TimeoutError:
                return False
        while not await self._terminate(max(0, deadline - asyncio.get_running_loop().time())):
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(0.05)
        return True


__all__ = ["MediaProcessor", "MAX_DOWNLOAD_BYTES", "FRAMES_BUDGET_S", "TRANSCRIBE_BUDGET_S"]
