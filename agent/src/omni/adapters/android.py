"""Managed Android workers and bounded PNG loading; no main-process device API or application database."""

from __future__ import annotations

import asyncio
import hashlib
import io
import sys
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from PIL import Image

from ..domain.android import METHODS
from ..domain.contracts import BusinessError, InvocationContext, Outcome
from ..domain.desktop import CapturedFrame
from .android_unicode import UnicodeFlow
from .object_store import checked_path
from .platform.desktop_capture import inventory
from .platform.permissions import assert_private, private_path
from .workers.client import WorkerClient


class AndroidBackend:
    def __init__(self, staging: Path, *, worker_factory=None, secrets=None) -> None:
        self._staging = staging
        self._factory = worker_factory or (
            lambda: WorkerClient(
                "android",
                tool_writes=METHODS,
                argv=[sys.executable, "-m", "omni.adapters.workers.android"],
                forbid_artifacts=True,
            )
        )
        self._pending: set[asyncio.Task] = set()
        self._lock = asyncio.Lock()
        self._closing = False
        self._staging_status: dict = {"checked": False, "residual_count": None}
        self._unicode = UnicodeFlow(self._factory, secrets)

    def health(self) -> dict:
        return {
            "closing": self._closing,
            "pending": len(self._pending),
            "transport": "local-adb-smart-socket",
            "installed": True,
            "permission": "explicit_device_grant",
            "available": not self._closing,
            "capture_staging": dict(self._staging_status),
            "limitations": [
                "Existing local ADB server required",
                "No pairing/root/install/remote shell",
                "Unicode append requires a manually enabled, pinned and paired input helper",
                "Generic taps require outcome review",
            ],
        }

    def quiescent(self) -> bool:
        return not self._pending

    async def execute(
        self,
        name: str,
        args: dict,
        context: InvocationContext,
        *,
        before_dispatch: Callable[[], Awaitable[None]],
        action: dict | None = None,
        environment: Callable[[str, dict], Awaitable[None]] | None = None,
    ):
        if self._closing or len(self._pending) >= 4:
            raise BusinessError("android_busy", "Android worker admission is closed or full", status=503)
        task = asyncio.create_task(
            self._execute(name, args, context, before_dispatch, action, environment), name="android-worker"
        )
        self._pending.add(task)

        def done(future):
            self._pending.discard(future)
            if not future.cancelled():
                future.exception()

        task.add_done_callback(done)
        return await asyncio.shield(task)

    async def _execute(self, name, args, context, guard, action, environment):
        async with self._lock:
            if self._closing:
                raise BusinessError("android_closing", "Android worker admission is closed")
            await guard()
            client = self._factory()
            destination = None
            try:
                if environment is not None:
                    if name == "android_unicode_restore":
                        return await self._unicode.recover(client, args, context, guard, environment)
                    if args.get("kind") == "append_unicode":
                        return await self._unicode.run(client, args, context, guard, environment)
                    raise ValueError("IME environment callback requires a Unicode operation")
                values = dict(args)
                if name == "android_capture":
                    checked_path(self._staging, ".check")
                    self._staging_status = {"checked": True, **await asyncio.to_thread(inventory, self._staging, set())}
                    if self._staging_status["truncated"]:
                        raise BusinessError(
                            "android_staging_full", "Review residual Android captures before continuing", status=503
                        )
                    self._staging.mkdir(parents=True, exist_ok=True)
                    private_path(self._staging, directory=True)
                    destination = self._staging / (str(uuid.uuid4()) + ".png")
                    values["destination"] = str(destination.resolve())
                async with asyncio.timeout(22):
                    result = await client.invoke(
                        name, values, operation_id=str(uuid.uuid4()), context=context, before_dispatch=guard
                    )
                    if action is not None and result.status == Outcome.OK:
                        token = result.data.get("token")
                        if not isinstance(token, str) or str(uuid.UUID(token)) != token:
                            raise ValueError("Invalid Android ready token")
                        result = await client.invoke(
                            "android_commit",
                            {**args, "token": token},
                            operation_id=str(uuid.uuid4()),
                            context=context,
                            before_dispatch=guard,
                        )
                    if destination is not None and result.status == Outcome.OK:
                        # The worker still owns no writes after its final response. File inspection has one bounded slot.
                        frame = await asyncio.to_thread(self._load, destination, result.data)
                        await guard()
                        return frame
                    return result
            finally:
                # Keep actual ownership, the slot and staging until the entire native process tree is reclaimed.
                while not await client.close(1):
                    await asyncio.sleep(0.1)
                if destination is not None and destination.exists():
                    destination.unlink()

    @staticmethod
    def _load(path: Path, receipt: dict) -> CapturedFrame:
        if (
            path.is_symlink()
            or path.is_junction()
            or not path.is_file()
            or path.stat().st_nlink != 1
            or not 1 <= path.stat().st_size <= 16 * 1024 * 1024
        ):
            raise ValueError("Invalid Android capture file")
        private_path(path)
        assert_private(path)
        data = path.read_bytes()
        with Image.open(io.BytesIO(data)) as image:
            if (
                image.format != "PNG"
                or not 1 <= image.width <= 8192
                or not 1 <= image.height <= 8192
                or image.width * image.height > 16000000
            ):
                raise ValueError("Invalid Android capture image")
            width, height = image.size
            image.verify()
        if receipt != {"bytes": len(data), "width": width, "height": height}:
            raise ValueError("Android capture receipt changed")
        frame = CapturedFrame(data, width, height, hashlib.sha256(data).hexdigest(), "adb_screencap")
        frame.validate()
        return frame

    def stop_accepting(self):
        self._closing = True

    async def close(self, timeout_s: float) -> bool:
        self.stop_accepting()
        if self._pending:
            await asyncio.wait(self._pending, timeout=max(0, timeout_s))
        return not self._pending
