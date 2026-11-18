"""Publish private boot identity while the instance lock is held; never controls another process."""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any, cast

import psutil

from omni.application.ports import Record
from omni.domain.contracts import BusinessError
from omni.domain.service_control import drain_receipt, verify_drain
from omni.adapters.object_store import sync_directory
from omni.adapters.platform.runtime.permissions import assert_private, private_path

READINESS_FIELDS = (
    "instance_id",
    "boot_id",
    "pid",
    "process_created",
    "build_id",
    "api_version",
    "schema_version",
    "service_plan_sha256",
)


def canonical_proof(report: Record, challenge: str, key: bytes) -> Record:
    if re.fullmatch(r"[a-f0-9]{64}", challenge) is None or len(key) != 32:
        raise BusinessError("invalid_service_challenge", "Readiness challenge is invalid", status=422)
    content = {
        "identity": {**{field: report[field] for field in READINESS_FIELDS}, "ready": report.get("ready") is True},
        "challenge": challenge,
    }
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode(
        "utf-8"
    )
    return {**content, "signature": hmac.new(key, encoded, hashlib.sha256).hexdigest()}


def read_private(path: Path, limit: int = 16384) -> bytes:
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise BusinessError("unsafe_runtime_identity", "Runtime identity cannot use linked paths")
    assert_private(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            raise BusinessError("unsafe_runtime_identity", "Runtime identity must be a bounded private file")
        if os.name != "nt" and (before.st_mode & 0o077 or before.st_uid != os.getuid()):
            raise BusinessError("unsafe_runtime_identity", "Runtime identity belongs to another account or is public")
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    current = path.stat(follow_symlinks=False)

    def fields(info: os.stat_result) -> tuple[int, int, int, int, int]:
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_nlink

    if len(data) != before.st_size or fields(before) != fields(after) or fields(after) != fields(current):
        raise BusinessError("runtime_identity_changed", "Runtime identity changed during inspection")
    return data


def process_identity() -> Record:
    process = psutil.Process()
    return {"pid": process.pid, "process_created": process.create_time()}


class RuntimeIdentityFiles:
    def __init__(self, control: Path, identity: Record) -> None:
        self.path = control / "runtime.json"
        self.identity = dict(identity)
        self.key = secrets.token_bytes(32)
        self._key_published = False
        self._writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="omni-identity")
        self._pending: set[asyncio.Future[Any]] = set()
        self._drain: asyncio.Task[None] | None = None
        self._stopped: asyncio.Task[None] | None = None
        self._closing = self._closed = False
        self._control: asyncio.Task[Record] | None = None
        self._control_binding: tuple[str, str] | None = None

    def _write(self, state: str) -> None:
        # One executor orders running, draining and stopped, including cancelled callers.
        value = {"format": "omni-runtime-identity-1", **self.identity, "state": state}
        if not self._key_published:
            self._replace(self.path.with_name("readiness.key"), self.key)
            self._key_published = True
        self._replace(self.path, json.dumps(value, sort_keys=True, allow_nan=False).encode("utf-8"))

    @staticmethod
    def _replace(path: Path, content: bytes) -> None:
        if any(part.is_symlink() or part.is_junction() for part in (path, *path.parents)):
            raise BusinessError("unsafe_runtime_identity", "Runtime publication cannot use linked paths")
        if path.exists():
            read_private(path)
        temporary = path.with_name(".runtime-" + uuid.uuid4().hex)
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                private_path(temporary)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            sync_directory(path.parent)
        finally:
            temporary.unlink(missing_ok=True)

    async def _publish(self, state: str) -> None:
        if len(self._pending) >= 4 or self._closed:
            raise BusinessError("runtime_identity_busy", "Runtime identity publisher is unavailable", status=503)
        future = asyncio.get_running_loop().run_in_executor(
            self._writer, contextvars.copy_context().run, partial(self._write, state)
        )
        self._pending.add(future)

        def finished(done: asyncio.Future[None]) -> None:
            self._pending.discard(done)
            if not done.cancelled():
                done.exception()

        future.add_done_callback(finished)
        await asyncio.shield(future)

    async def start(self) -> None:
        if self._closing:
            raise BusinessError("runtime_identity_closing", "Instance is draining")
        await self._publish("running")

    async def prepare_drain(self, request: Record, now: str) -> Record:
        """Persist a single authorized boot control receipt before admission is closed."""
        checked = verify_drain(request, self.identity, self.key, now)
        expected = cast(Record, drain_receipt(checked, self.key))
        if self._closed or len(self._pending) >= 4:
            raise BusinessError("service_drain_unavailable", "Boot control writer is unavailable")

        def persist() -> Record:
            from omni.adapters.platform.services.deployment_files import CurrentDeploymentFiles, json_bytes, no_links, publish_json

            folder = self.path.parent / "service-drains"
            no_links(folder)
            if not folder.exists():
                folder.mkdir(mode=0o700)
                private_path(folder, directory=True)
                sync_directory(folder.parent)
            assert_private(folder)
            path = folder / (str(self.identity["boot_id"]) + ".json")
            publisher = CurrentDeploymentFiles()
            if path.exists():
                assert_private(path)
                if publisher.read(path) != expected:
                    raise BusinessError("service_already_draining", "Another operation already owns this boot's drain")
            else:
                publish_json(publisher, path, json_bytes(expected))
            return expected

        future = asyncio.get_running_loop().run_in_executor(self._writer, contextvars.copy_context().run, persist)
        self._pending.add(future)
        future.add_done_callback(
            lambda done: (self._pending.discard(done), done.exception() if not done.cancelled() else None)
        )
        return await asyncio.shield(future)

    async def request_drain(self, request: Record, now: str, accepted: Callable[[], None]) -> Record:
        if self._closed or (self._closing and self._control is None):
            raise BusinessError("service_drain_unavailable", "This boot is already closing")
        checked = verify_drain(request, self.identity, self.key, now)
        binding = (cast(str, checked["operation_id"]), cast(str, checked["operation_sha256"]))
        if self._control_binding is not None and self._control_binding != binding:
            raise BusinessError("service_already_draining", "Another operation owns this boot's drain")
        if self._control is None:
            self._control_binding = binding

            async def perform() -> Record:
                result = await self.prepare_drain(request, now)
                accepted()
                return result

            self._control = asyncio.create_task(perform(), name="owned-service-drain")
            self._control.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        return await asyncio.shield(self._control)

    def stop_accepting(self) -> None:
        if not self._closing:
            self._closing = True
            self._drain = asyncio.create_task(self._publish("draining"), name="runtime-identity-drain")
            self._drain.add_done_callback(lambda task: None if task.cancelled() else task.exception())

    async def close(self, timeout_s: float) -> bool:
        if self._closed:
            return True
        self.stop_accepting()
        deadline = asyncio.get_running_loop().time() + max(0, timeout_s)
        if self._control is not None and not self._control.done():
            _, pending = await asyncio.wait(
                (self._control,), timeout=max(0, deadline - asyncio.get_running_loop().time())
            )
            if pending:
                return False
        if self._drain is not None:
            _, pending = await asyncio.wait((self._drain,), timeout=max(0, timeout_s))
            if pending:
                return False
            if self._drain.cancelled() or self._drain.exception() is not None:
                # A failed draining publication can be retried while ownership is retained.
                self._drain = asyncio.create_task(self._publish("draining"))
                self._drain.add_done_callback(lambda task: None if task.cancelled() else task.exception())
                return False
        if self._pending:
            _, pending = await asyncio.wait(self._pending, timeout=max(0, deadline - asyncio.get_running_loop().time()))
            if pending:
                return False
        if self._stopped is None or (self._stopped.done() and self._stopped.exception() is not None):
            self._stopped = asyncio.create_task(self._publish("stopped"), name="runtime-identity-stopped")
            self._stopped.add_done_callback(lambda task: None if task.cancelled() else task.exception())
        _, pending = await asyncio.wait((self._stopped,), timeout=max(0, deadline - asyncio.get_running_loop().time()))
        if pending:
            return False
        self._stopped.result()
        self._writer.shutdown(wait=False)
        self._closed = True
        return True
