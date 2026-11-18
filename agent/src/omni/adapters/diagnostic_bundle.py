"""Bounded metadata and structured correlations; never opens business bodies or credential stores."""

from __future__ import annotations

import asyncio
import contextvars
import datetime as dt
import io
import json
import math
import os
import platform
import re
import shutil
import tempfile
import threading
import zipfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TypeVar, cast

from omni.adapters.platform.runtime.paths import InstancePaths
from omni.adapters.platform.runtime.permissions import private_path

from ..application.ports import Record
from ..domain.contracts import BusinessError
from .diagnostics import Redactor
from .log_query import CaptureLogReader, LogQuery, LogReader, LogSource
from .object_store import sync_directory

T = TypeVar("T")
Payload = dict[str, object]
UUID_PATTERN = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
KEY_PATTERN = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){0,10}")
MAX_BUNDLE = 2 * 1024 * 1024


def validate_bundle(content: bytes) -> None:
    """A local HTTP response is still untrusted input to the export writer."""
    if len(content) > MAX_BUNDLE:
        raise BusinessError("diagnostics_too_large", "Diagnostic bundle exceeds 2 MiB", status=413)
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if (
                len(entries) != 3
                or {entry.filename for entry in entries} != {"summary.json", "correlations.jsonl", "README.txt"}
                or sum(entry.file_size for entry in entries) > MAX_BUNDLE
                or any(entry.flag_bits & 1 or entry.is_dir() for entry in entries)
                or archive.testzip() is not None
            ):
                raise ValueError
    except (zipfile.BadZipFile, RuntimeError, OSError, ValueError, NotImplementedError) as exc:
        raise BusinessError(
            "invalid_diagnostic_response", "Diagnostic package failed bounded integrity checks"
        ) from exc


def correlation(record: Payload) -> Record:
    """Export no free text; reject malformed correlation values independently of log redaction."""
    result: Record = {}
    for name in ("record_id", "boot_id", "request_id", "agent_id", "session_id", "run_id", "operation_id"):
        value = record.get(name)
        if isinstance(value, str) and UUID_PATTERN.fullmatch(value):
            result[name] = value
    for name, length in (("build_id", 64), ("trace_id", 32), ("span_id", 16)):
        value = record.get(name)
        if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{" + str(length) + "}", value):
            result[name] = value
    level = record.get("level")
    if level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        result["level"] = cast(str, level)
    for name in ("event", "error_code"):
        value = record.get(name)
        if isinstance(value, str) and len(value) <= 100 and KEY_PATTERN.fullmatch(value):
            result[name] = value
    component = record.get("component")
    result["component"] = (
        component
        if isinstance(component, str)
        and component.startswith("omni.v2.")
        and len(component) <= 100
        and KEY_PATTERN.fullmatch(component)
        else "external"
    )
    for name in ("duration_ms", "attempt"):
        value = record.get(name)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and 0 <= value <= 1e12
        ):
            result[name] = value
    if type(record.get("retryable")) is bool:
        result["retryable"] = cast(bool, record["retryable"])
    value = record.get("timestamp")
    if isinstance(value, str) and len(value) <= 40:
        try:
            parsed = dt.datetime.fromisoformat(value)
            if parsed.tzinfo is not None and parsed.utcoffset() == dt.timedelta():
                result["timestamp"] = parsed.isoformat(timespec="microseconds")
        except ValueError:
            pass
    return result


class DiagnosticSource(LogSource, Protocol):
    @property
    def debug(self) -> DebugCapture | None: ...
    @property
    def build_id(self) -> str: ...
    @property
    def boot_id(self) -> str: ...
    def snapshot(self) -> Record: ...
    def maintenance(self) -> Record: ...


class DebugCapture(Protocol):
    def snapshot(self) -> Record: ...
    def start(self, agent_id: str, operation_id: str, duration_s: int, byte_limit: int = 134217728) -> Record: ...
    def stop(self, reason: str = "owner_stopped") -> Record: ...
    def maintenance(self) -> Record: ...


class StoredDiagnostics:
    """Read existing diagnostic segments without creating a log writer or host hooks."""

    def __init__(self, paths: InstancePaths) -> None:
        self.directory = paths.logs
        self.io_lock = threading.RLock()
        self.stderr_only = False
        self.segments = 16
        self.redactor = Redactor()
        self.build_id = self.boot_id = "offline"
        self.debug = None

    def snapshot(self) -> Record:
        return {"mode": "offline", "writer_alive": False, "dropped": None, "written": None, "failures": None}

    def maintenance(self) -> Record:
        return {"available": False, "reason": "offline"}


@dataclass
class CaptureSource:
    directory: Path
    io_lock: AbstractContextManager[object, bool | None]
    redactor: Redactor
    stderr_only: bool = False
    segments: int = 1


def publish_diagnostic(paths: InstancePaths, destination: Path, content: bytes) -> dict[str, object]:
    target = Path(destination).expanduser().absolute()
    if target.resolve().is_relative_to(paths.home.resolve()):
        raise BusinessError(
            "unsafe_diagnostic_destination", "Save the diagnostic export outside the instance home", status=422
        )
    validate_bundle(content)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".omni-diagnostic-", suffix=".partial", dir=target.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as stream:
            private_path(temporary)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # The destination is outside the instance lock. POSIX rename would
        # overwrite a competing writer; a same-filesystem hard link never does.
        if os.name == "nt":
            os.rename(temporary, target)
        else:
            os.link(temporary, target, follow_symlinks=False)
        sync_directory(target.parent)
    except FileExistsError as exc:
        raise BusinessError(
            "diagnostic_destination_exists", "Use a new destination; existing files are never overwritten"
        ) from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {"destination": str(target), "bytes": len(content), "includes_business_bodies": False}


class DiagnosticFiles:
    def __init__(self, paths: InstancePaths, sink: DiagnosticSource) -> None:
        self._paths, self._sink = paths, sink
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="omni-diagnostic-io")
        # Four public operations plus the single owned maintenance worker.
        self._slots = asyncio.Semaphore(5)
        self._jobs: set[asyncio.Future[Any]] = set()
        self._closing = False

    async def _run(self, operation: Callable[[], T]) -> T:
        if self._closing:
            raise BusinessError("diagnostics_closing", "Diagnostic files are draining", status=503)
        if self._slots.locked():
            raise BusinessError("diagnostics_busy", "Diagnostic I/O is busy", status=503, retryable=True)
        await self._slots.acquire()
        job = asyncio.get_running_loop().run_in_executor(self._executor, contextvars.copy_context().run, operation)
        self._jobs.add(job)

        def completed(future: asyncio.Future[T]) -> None:
            self._jobs.discard(future)
            self._slots.release()
            if not future.cancelled():
                future.exception()

        job.add_done_callback(completed)
        return await asyncio.shield(job)

    def _describe(self) -> Record:
        sizes: Record = {}
        for name in ("omni.db", "omni.db-wal", "omni.db-shm"):
            path = self._paths.database.with_name(name)
            if path.is_symlink() or path.is_junction():
                raise BusinessError("unsafe_diagnostic_path", "Storage metadata path cannot be a link", status=503)
            try:
                sizes[name] = path.stat().st_size
            except FileNotFoundError:
                sizes[name] = 0
            except OSError:
                sizes[name] = None
        try:
            space = shutil.disk_usage(self._paths.home)
            disk: Record = {"total_bytes": space.total, "free_bytes": space.free}
        except OSError:
            disk = {"total_bytes": None, "free_bytes": None}
        machine = platform.machine().lower()
        return {
            "platform": platform.system(),
            "architecture": machine if machine in {"amd64", "x86_64", "arm64", "aarch64"} else "other",
            "python": platform.python_version(),
            "build_id": self._sink.build_id,
            "boot_id": self._sink.boot_id,
            "cpu_count": os.cpu_count(),
            "python_threads": threading.active_count(),
            "storage_bytes": sizes,
            "disk": disk,
            "logs": self._sink.snapshot(),
            "platform_acceptance": "pending",
            "process_rss_bytes": None,
            "capability_child_count": None,
            "debug": self._debug_metadata(),
        }

    def _debug_metadata(self) -> Record:
        if self._sink.debug is None:
            return {"available": False, "reason": "offline"}
        report = self._sink.debug.snapshot()
        return {name: report.get(name) for name in ("active", "remaining_s", "bytes", "error_code", "reason")}

    def _capture(self) -> DebugCapture:
        if self._sink.debug is None or self._sink.stderr_only:
            raise BusinessError("debug_unavailable", "Local debug capture is unavailable for this log deployment")
        return self._sink.debug

    async def debug_snapshot(self) -> Record:
        return await self._run(lambda: self._sink.debug.snapshot() if self._sink.debug else {"active": False})

    async def debug_start(self, agent_id: str, operation_id: str, duration_s: int) -> Record:
        return await self._run(lambda: self._capture().start(agent_id, operation_id, duration_s))

    async def debug_stop(self, reason: str) -> Record:
        return await self._run(lambda: self._capture().stop(reason))

    async def debug_maintenance(self) -> Record:
        def maintain() -> Record:
            logs = self._sink.maintenance()
            debug: Record = self._sink.debug.maintenance() if self._sink.debug else {"available": False}
            return {"logs": logs, "debug": debug}

        return await self._run(maintain)

    async def debug_logs(self, agent_id: str, operation_id: str, filters: Record) -> Record:
        def page() -> Record:
            snapshot = self._capture().snapshot()
            if snapshot.get("agent_id") != agent_id or snapshot.get("operation_id") != operation_id:
                raise BusinessError("debug_capture_changed", "Debug capture has changed; reload its state", status=409)
            source = CaptureSource(self._paths.logs / "debug", self._sink.io_lock, self._sink.redactor)
            return CaptureLogReader(source, operation_id).page(LogQuery.from_record(filters))

        return await self._run(page)

    async def log_page(self, filters: Record) -> Record:
        def page() -> Record:
            # Endpoint validation and the adapter query model share the same bounds.
            return LogReader(self._sink).page(LogQuery.from_record(filters))

        return await self._run(page)

    async def describe(self) -> Record:
        return await self._run(self._describe)

    def _bundle(self, information: Record) -> bytes:
        try:
            tail: Record = LogReader(self._sink).tail()
        except (BusinessError, OSError, ValueError, UnicodeError) as exc:
            tail = {
                "available": False,
                "error_code": exc.code if isinstance(exc, BusinessError) else "diagnostic_logs_unavailable",
                "items": [],
            }
        entries = [correlation(row) for row in cast(list[Payload], tail.pop("items"))]
        payload = json.dumps(
            {**information, "diagnostic_log_excerpt": tail}, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
        if len(payload) > 262144:
            raise BusinessError("diagnostics_too_large", "Diagnostic metadata exceeds its size limit", status=503)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("summary.json", payload)
            archive.writestr(
                "correlations.jsonl",
                "".join(json.dumps(entry, ensure_ascii=False, allow_nan=False) + "\n" for entry in entries),
            )
            archive.writestr(
                "README.txt",
                "OmniOperator V2 diagnostic excerpt. Instance totals and fixed-cardinality measurements only. Chat, business payloads, database files, credentials, browser state, filesystem paths and free-form log/exception text are excluded. Log excerpts are bounded; truncation and unavailable data are explicit. This is not a backup or platform acceptance report.\n",
            )
        value = buffer.getvalue()
        if len(value) > 2 * 1024 * 1024:
            raise BusinessError("diagnostics_too_large", "Diagnostic bundle exceeds 2 MiB", status=503)
        return value

    async def bundle(self, information: Record) -> bytes:
        return await self._run(lambda: self._bundle(information))

    async def close(self, timeout_s: float = 30) -> bool:
        self._closing = True
        if self._jobs:
            _, pending = await asyncio.wait(self._jobs, timeout=max(0, timeout_s))
            if pending:
                return False
        self._executor.shutdown(wait=False)
        return True
