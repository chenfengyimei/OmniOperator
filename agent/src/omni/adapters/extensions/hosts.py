"""Bounded trusted Python worker ownership; no database, model, credential or arbitrary command access."""

from __future__ import annotations

import asyncio
import shutil
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from ...application.ports import Record
from ...domain.contracts import JSON, ActionResult, BusinessError, InvocationContext
from ...domain.extensions import catalog
from ..workers.client import WorkerClient
from .worker import ARTIFACTS_ENV, handshake

MAX_HOSTS = 4
MAX_PENDING = 64


@dataclass
class Host:
    key: tuple[str, str, int]
    client: WorkerClient
    busy: bool = False
    pid: int | None = None


class ExtensionHosts:
    def __init__(self, artifact_root: str = "") -> None:
        self._hosts: dict[tuple[str, str, int], Host] = {}
        self._calls: dict[asyncio.Task[ActionResult], tuple[str, str, int]] = {}
        self._lock = asyncio.Lock()
        self._closing = False
        self._artifact_root = str(Path(artifact_root).resolve()) if artifact_root else ""

    def artifact_path(self, agent_id: str, extension_id: str) -> str:
        """The child's only writable artifact directory; absent roots make artifact results unknown."""
        if not self._artifact_root:
            return ""
        return str(Path(self._artifact_root) / agent_id / extension_id)

    async def _acquire(self, revision: Record, location: str) -> Host:
        key = str(revision["agent_id"]), str(revision["extension_id"]), cast(int, revision["authority_epoch"])
        async with self._lock:
            if self._closing:
                raise BusinessError("extension_hosts_closing", "Extension hosts are draining", status=503)
            host = self._hosts.get(key)
            if host is not None:
                if host.busy:
                    raise BusinessError("extension_busy", "This extension host is busy", retryable=True, status=503)
                host.busy = True
                return host
            if len(self._hosts) >= MAX_HOSTS:
                for old_key, old in list(self._hosts.items()):
                    if not old.busy and await old.client.close(1):
                        del self._hosts[old_key]
                        break
                if len(self._hosts) >= MAX_HOSTS:
                    raise BusinessError(
                        "extension_busy", "No reclaimed extension host is available", retryable=True, status=503
                    )

            async def startup(process: asyncio.subprocess.Process) -> None:
                assert host is not None  # Published in the pool before the client may spawn.
                host.pid = process.pid
                async with asyncio.timeout(10):
                    await handshake(process, str(revision["package_sha256"]), str(revision["catalog_sha256"]))

            client = WorkerClient(
                "extension",
                tool_writes={
                    str(tool["name"]): True for tool in catalog(cast(dict[str, object], revision["manifest"]))
                },
                argv=[
                    sys.executable,
                    "-P",
                    "-m",
                    "omni.adapters.extensions.worker",
                    location,
                    str(revision["package_sha256"]),
                    str(revision["catalog_sha256"]),
                ],
                startup=startup,
                cwd=str(Path(location).parent),
                forbid_artifacts=False,
                extra_env=(
                    {ARTIFACTS_ENV: self.artifact_path(str(revision["agent_id"]), str(revision["extension_id"]))}
                    if self._artifact_root
                    else None
                ),
            )
            host = Host(key, client, True)
            self._hosts[key] = host
            return host

    async def invoke(
        self,
        revision: Record,
        location: str,
        name: str,
        arguments: Mapping[str, JSON],
        *,
        operation_id: str,
        context: InvocationContext,
        before_dispatch: Callable[[], Awaitable[None]],
        host_context: Mapping[str, JSON] | None = None,
    ) -> ActionResult:
        if self._closing:
            raise BusinessError("extension_hosts_closing", "Extension hosts are draining", status=503)
        if len(self._calls) >= MAX_PENDING:
            raise BusinessError("extension_busy", "Extension invocation queue is full", retryable=True, status=503)
        key = context.agent_id, str(revision["extension_id"]), cast(int, revision["authority_epoch"])

        async def perform() -> ActionResult:
            host = await self._acquire(revision, location)
            try:
                return await host.client.invoke(
                    name,
                    arguments,
                    operation_id=operation_id,
                    context=context,
                    before_dispatch=before_dispatch,
                    host_context=host_context,
                )
            finally:
                # A poisoned client remains resident and owns its process until close
                # actually succeeds; clearing busy never releases that capacity.
                host.busy = False

        task = asyncio.create_task(perform(), name="extension-call-" + operation_id)
        self._calls[task] = key

        def complete(future: asyncio.Task[ActionResult]) -> None:
            self._calls.pop(future, None)
            if not future.cancelled():
                future.exception()

        task.add_done_callback(complete)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if not task.done() and not task.cancelling():
                task.cancel()
            raise

    async def revoke(self, agent_id: str, extension_id: str, authority_epoch: int, timeout_s: float = 1) -> bool:
        def matches(key: tuple[str, str, int]) -> bool:
            return key[:2] == (agent_id, extension_id) and key[2] <= authority_epoch

        for task, key in list(self._calls.items()):
            if matches(key) and not task.done() and not task.cancelling():
                task.cancel()
        deadline = asyncio.get_running_loop().time() + max(0, timeout_s)
        complete = True
        async with self._lock:
            for key, host in list(self._hosts.items()):
                if matches(key):
                    if await host.client.close(max(0, deadline - asyncio.get_running_loop().time())):
                        # The invoke may still be settling its exception. No new call
                        # can use this old epoch because the database revoked it first.
                        self._hosts.pop(key, None)
                    else:
                        complete = False
        return complete

    def health(self, agent_id: str = "", extension_id: str = "") -> Record:
        selected = [host for key, host in self._hosts.items() if not agent_id or key[:2] == (agent_id, extension_id)]
        result: Record = {
            "closing": self._closing,
            "resident": len(selected),
            "active": len(self._calls),
            "cleanup_pending": sum(bool(host.client.health()["cleanup_pending"]) for host in selected),
            "max_hosts": MAX_HOSTS,
            "max_pending": MAX_PENDING,
        }
        if agent_id:
            result["workers"] = [
                {"authority_epoch": host.key[2], "pid": host.pid, "busy": host.busy, **host.client.health()}
                for host in selected
            ]
        return result

    def stop_accepting(self) -> None:
        self._closing = True

    async def close(self, timeout_s: float = 30) -> bool:
        self.stop_accepting()
        deadline = asyncio.get_running_loop().time() + max(0, timeout_s)
        for task in list(self._calls):
            if not task.done() and not task.cancelling():
                task.cancel()
        complete = True
        async with self._lock:
            for key, host in list(self._hosts.items()):
                if await host.client.close(max(0, deadline - asyncio.get_running_loop().time())):
                    self._hosts.pop(key, None)
                else:
                    complete = False
        pending = [task for task in self._calls if not task.done()]
        if pending:
            _, remaining = await asyncio.wait(pending, timeout=max(0, deadline - asyncio.get_running_loop().time()))
            complete = complete and not remaining
        if complete and self._artifact_root:
            shutil.rmtree(self._artifact_root, ignore_errors=True)
        return complete and not self._hosts
