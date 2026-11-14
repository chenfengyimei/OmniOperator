"""One bounded, supervised local embedding process; no native ML imports in the application process."""

from __future__ import annotations

import asyncio
import contextvars
import importlib.metadata
import logging
import os
import sys
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from ..application.ports import Record
from ..domain.contracts import JSON, BusinessError
from ..domain.local_vectors import (
    MAX_FRAME,
    PROTOCOL,
    canonical,
    decode,
    invalid,
    texts_request,
    vectors,
    version,
)
from .platform.processes import OwnedProcess
from .vector_files import read_manifest, runtime_versions, selection

log = logging.getLogger("omni.v2.vectors")
Payload = dict[str, object]


class LocalVectors:
    def __init__(
        self, root: Path | None, sha256: str, spec: Payload | None, *, error: str = "", argv: list[str] | None = None
    ) -> None:
        self._root, self._sha256, self._spec = root, sha256, spec
        self.runtime = runtime_versions()
        self.version = version(spec, cast(Payload, self.runtime)) if spec is not None else "onnx-unavailable"
        self.dimension = cast(int, spec["dimension"]) if spec is not None else 1
        self._error = error
        self._argv = argv or [sys.executable, "-m", "omni.adapters.workers.local_vectors"]
        self._owned: OwnedProcess | None = None
        self._starting: asyncio.Task[OwnedProcess] | None = None
        self._cleanup: asyncio.Task[bool] | None = None
        self._stderr: asyncio.Task[None] | None = None
        self._pending: set[asyncio.Task[list[list[float]]]] = set()
        self._lock = asyncio.Lock()
        self._loaded = self._closing = False
        self._stderr_bytes = 0

    def status(self) -> Record:
        available = not self._error and not self._closing and self._spec is not None
        return {
            "available": available,
            "state": "closing"
            if self._closing
            else self._error or ("loaded" if self._loaded else "configured_unverified"),
            "reason": self._error,
            "version": self.version,
            "dimension": self.dimension,
            "model_id": cast(str, self._spec["model_id"]) if self._spec else "",
            "runtime": cast(JSON, self.runtime),
            "local_only": True,
            "fallback": "full_text",
            "pending": len(self._pending),
            "running": self._owned is not None and self._owned.process.returncode is None,
            "cleanup_pending": self._error == "vector_cleanup_pending"
            or bool(self._cleanup and not self._cleanup.done())
            or bool(self._owned and not self._loaded),
            "recovery": "Verify the selected model bundle and optional vectors dependencies; restart after configuration changes",
        }

    async def _drain(self, stream: asyncio.StreamReader) -> None:
        while data := await stream.read(4096):
            self._stderr_bytes += len(data)
        if self._stderr_bytes:
            log.warning(
                "Local vector worker stderr observed",
                extra={"event": "vector.worker_stderr", "bytes": self._stderr_bytes},
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
                "OMNI_TEST_OFFLINE",
                "OMNI_TEST_VIOLATIONS",
            )
            if name in os.environ
        }
        if os.environ.get("OMNI_TEST_OFFLINE") == "1":
            env["PYTHONPATH"] = os.pathsep.join(
                p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p and os.path.isabs(p)
            )
        env.update(
            PYTHONUTF8="1",
            PYTHONDONTWRITEBYTECODE="1",
            TOKENIZERS_PARALLELISM="false",
            HF_HUB_OFFLINE="1",
            OMP_NUM_THREADS="2",
            MKL_NUM_THREADS="1",
        )
        self._owned = await OwnedProcess.spawn(
            self._argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=MAX_FRAME + 1,
            env=env,
        )
        assert self._owned.process.stderr is not None
        self._stderr = asyncio.create_task(
            self._drain(self._owned.process.stderr), name="vector-stderr", context=contextvars.Context()
        )
        return self._owned

    async def _exchange(self, method: str, arguments: Payload) -> Payload:
        assert self._owned is not None
        process = self._owned.process
        assert process.stdin is not None and process.stdout is not None
        identity = str(uuid.uuid4())
        frame = canonical({"protocol": PROTOCOL, "id": identity, "method": method, "arguments": arguments}) + b"\n"
        if len(frame) > MAX_FRAME:
            raise invalid("vector_input_too_large")
        process.stdin.write(frame)
        await process.stdin.drain()
        row = decode(await process.stdout.readline())
        if (
            set(row) != {"protocol", "id", "error", "result"}
            or row["id"] != identity
            or not isinstance(row["result"], dict)
            or not isinstance(row["error"], str)
        ):
            raise invalid("vector_protocol_invalid")
        if row["error"]:
            allowed = {
                "vector_runtime_mismatch",
                "vector_bundle_changed",
                "vector_graph_unsupported",
                "vector_external_data_forbidden",
                "vector_tokenizer_invalid",
                "vector_runtime_failed",
                "vector_input_too_large",
                "vector_output_invalid",
                "vector_bundle_path_unsafe",
                "vector_bundle_invalid",
            }
            raise invalid(row["error"] if row["error"] in allowed else "vector_worker_failed")
        return cast(Payload, row["result"])

    async def _reclaim_actual(self, timeout_s: float) -> bool:
        if self._starting is not None:
            try:
                await asyncio.shield(self._starting)
            except Exception:  # noqa: BLE001 -- retain actual spawn result until ownership is known
                if self._owned is None:
                    self._starting = None
                    return True
        if self._owned is not None and not await self._owned.terminate(timeout_s):
            return False
        if self._stderr is not None:
            await asyncio.gather(self._stderr, return_exceptions=True)
        self._owned = self._starting = self._stderr = None
        self._loaded = False
        return True

    async def _reclaim(self, timeout_s: float) -> bool:
        if (
            self._cleanup is None
            or self._cleanup.done()
            and (self._cleanup.cancelled() or self._cleanup.exception() is not None or not self._cleanup.result())
        ):
            self._cleanup = asyncio.create_task(
                self._reclaim_actual(timeout_s), name="vector-cleanup", context=contextvars.Context()
            )
            self._cleanup.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        done, _ = await asyncio.wait((self._cleanup,), timeout=max(0, timeout_s))
        return bool(
            done and not self._cleanup.cancelled() and self._cleanup.exception() is None and self._cleanup.result()
        )

    async def _perform(self, texts: list[str], purpose: str) -> list[list[float]]:
        async with self._lock:
            if not self.status()["available"]:
                raise invalid(self._error or "vector_closing")
            try:
                async with asyncio.timeout(10):
                    if self._starting is None:
                        self._cleanup = None
                        self._starting = asyncio.create_task(
                            self._spawn(), name="vector-spawn", context=contextvars.Context()
                        )
                        self._starting.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
                    await asyncio.shield(self._starting)
                    if not self._loaded:
                        loaded = await self._exchange(
                            "load", {"bundle": str(self._root), "sha256": self._sha256, "version": self.version}
                        )
                        if loaded != {"version": self.version, "dimension": self.dimension, "state": "loaded"}:
                            raise invalid("vector_protocol_invalid")
                        self._loaded = True
                        log.info(
                            "Local vector model loaded",
                            extra={
                                "event": "vector.loaded",
                                "model_version": self.version,
                                "dimension": self.dimension,
                            },
                        )
                    row = await self._exchange("embed", {"texts": texts, "purpose": purpose, "version": self.version})
                    if set(row) != {"version", "vectors"} or row["version"] != self.version:
                        raise invalid("vector_protocol_invalid")
                    return vectors(row["vectors"], len(texts), self.dimension)
            except BaseException as exc:
                self._error = (
                    exc.code
                    if isinstance(exc, BusinessError)
                    else "vector_timeout"
                    if isinstance(exc, TimeoutError)
                    else "vector_worker_failed"
                )
                reclaimed = await self._reclaim(5)
                if not reclaimed:
                    self._error = "vector_cleanup_pending"
                elif isinstance(exc, asyncio.CancelledError):
                    self._error = ""
                log.warning(
                    "Local vector computation stopped",
                    extra={"event": "vector.failed", "error_code": self._error or "vector_cancelled"},
                )
                raise

    async def embed(self, texts: Sequence[str], *, purpose: str = "document") -> list[list[float]]:
        rows = texts_request(texts, purpose)
        if len(self._pending) >= 8:
            raise invalid("vector_busy")
        if not self.status()["available"]:
            raise invalid(self._error or "vector_closing")
        task = asyncio.create_task(self._perform(rows, purpose), name="local-vector")
        self._pending.add(task)
        try:
            return await task
        finally:
            self._pending.discard(task)

    async def close(self) -> None:
        self._closing = True
        for task in tuple(self._pending):
            task.cancel()
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)
        if not await self._reclaim(5):
            self._error = "vector_cleanup_pending"
            raise invalid("vector_cleanup_pending")
        self._error = ""


async def configured_vectors(home: Path) -> LocalVectors | None:
    def load() -> LocalVectors | None:
        try:
            selected = selection(home)
            if selected is None:
                return None
            root, digest = selected
            spec, _ = read_manifest(root, digest)
            error = ""
            try:
                if any(importlib.metadata.version(name) != wanted for name, wanted in runtime_versions().items()):
                    error = "vector_runtime_mismatch"
            except importlib.metadata.PackageNotFoundError:
                error = "vector_runtime_not_installed"
            return LocalVectors(root, digest, spec, error=error)
        except Exception:  # noqa: BLE001 -- optional local installation failures do not prevent core startup
            return LocalVectors(None, "", None, error="vector_bundle_invalid")

    return await asyncio.to_thread(load)
