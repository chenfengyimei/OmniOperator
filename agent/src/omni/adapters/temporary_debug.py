"""Boot-bound, owner-enabled metadata capture; never opens business storage or records free text."""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import stat
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from itertools import islice
from pathlib import Path

from ..domain.contracts import JSON, BusinessError, Outcome
from .metrics import OPERATIONS
from .platform.permissions import private_path

MAX_BYTES = 128 * 1024 * 1024
MAX_FILES = 1000
MAX_SECONDS = 3600
UUID_PATTERN = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
OWNED_NAME = re.compile(r"debug-(" + UUID_PATTERN + r")\.jsonl")
CORRELATIONS = ("request_id", "agent_id", "session_id", "run_id", "operation_id")


@dataclass(frozen=True)
class DebugEnvelope:
    capture_id: str
    agent_id: str
    boot_id: str
    payload: bytes
    generation: int = 0


class TemporaryDebug:
    def __init__(
        self, directory: Path, boot_id: str, monotonic: Callable[[], float] = time.monotonic, *, enabled: bool = True
    ) -> None:
        self.directory, self.boot_id = directory, boot_id
        self._clock, self._enabled = monotonic, enabled
        self._lock = threading.RLock()
        self._io_lock = threading.RLock()
        self._closed = False
        self._active = False
        self._agent = self._operation = ""
        self._until = 0.0
        self._bytes = self._retained = self._reserved = 0
        self._limit = MAX_BYTES
        self._identity: tuple[int, int] | None = None
        self._reason, self._error = "not_started", ""
        self._revision = self._dropped = self._written = 0
        self._generation = 0
        self._activation_epoch = 0

    def _finish(self, reason: str, error: str = "") -> None:
        if self._active or reason != self._reason or error != self._error:
            self._revision += 1
        self._active, self._reason, self._error = False, reason, error

    def _expire(self) -> None:
        if self._active and self._clock() >= self._until:
            self._finish("expired")

    def _snapshot(self) -> dict:
        self._expire()
        return {
            "active": self._active,
            "agent_id": self._agent or None,
            "operation_id": self._operation or None,
            "boot_id": self.boot_id,
            "remaining_s": max(0.0, self._until - self._clock()) if self._active else 0,
            "bytes": self._bytes,
            "retained_bytes": self._retained,
            "queued_bytes": self._reserved,
            "byte_limit": self._limit,
            "error_code": self._error,
            "reason": self._reason,
            "revision": self._revision,
            "dropped": self._dropped,
            "written": self._written,
        }

    def snapshot(self) -> dict:
        with self._lock:
            return self._snapshot()

    def stop(self, reason: str = "owner_stopped") -> dict:
        with self._lock:
            self._activation_epoch += 1
            if reason == "runtime_shutdown":
                self._closed = True
            self._finish(reason)
            return self._snapshot()

    def _inventory(self, cleanup: bool) -> tuple[int, int]:
        if any(path.is_symlink() or path.is_junction() for path in (self.directory, self.directory.parent)):
            raise BusinessError("unsafe_debug_path", "Debug paths cannot contain links", status=503)
        if not self.directory.exists():
            return 0, 0
        owned = []
        with os.scandir(self.directory) as entries:
            for index, entry in enumerate(islice(entries, MAX_FILES + 1)):
                if index == MAX_FILES:
                    raise BusinessError("debug_directory_limit", "Debug directory exceeds 1000 entries", status=503)
                path = Path(entry.path)
                if entry.is_symlink() or path.is_junction():
                    raise BusinessError("unsafe_debug_path", "Debug entries cannot be links", status=503)
                if OWNED_NAME.fullmatch(entry.name):
                    # Windows DirEntry stat omits link counts on its fast path.
                    # Path.stat obtains the actual ownership metadata.
                    info = path.stat(follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise BusinessError(
                            "unsafe_debug_path", "Owned debug entries must be single-link regular files", status=503
                        )
                    owned.append((path, info))
        total = removed = 0
        cutoff = time.time() - 86400
        with self._lock:
            current = f"debug-{self._operation}.jsonl" if self._active else ""
        for path, info in owned:
            if cleanup and info.st_mtime < cutoff and path.name != current:
                path.unlink()
                removed += 1
            else:
                total += info.st_size
        return total, removed

    def maintenance(self) -> dict:
        with self._io_lock:
            with self._lock:
                self._expire()
            try:
                retained, removed = self._inventory(True)
            except (BusinessError, OSError):
                with self._lock:
                    self._finish("storage_unavailable", "debug_storage_unavailable")
                raise
            with self._lock:
                self._retained = retained
                return {**self._snapshot(), "removed_files": removed}

    def start(self, agent_id: str, operation_id: str, duration_s: int, byte_limit: int = MAX_BYTES) -> dict:
        if (
            not isinstance(agent_id, str)
            or not isinstance(operation_id, str)
            or not re.fullmatch(UUID_PATTERN, agent_id)
            or not re.fullmatch(UUID_PATTERN, operation_id)
            or type(duration_s) is not int
            or not 1 <= duration_s <= MAX_SECONDS
            or type(byte_limit) is not int
            or not 1 <= byte_limit <= MAX_BYTES
        ):
            raise BusinessError(
                "invalid_debug_capture", "Debug identity, duration or byte limit is invalid", status=422
            )
        with self._io_lock:
            with self._lock:
                if not self._enabled:
                    raise BusinessError(
                        "debug_external_logs",
                        "Temporary debug capture is unavailable with stderr-only logs",
                        status=409,
                    )
                if self._closed:
                    raise BusinessError("debug_closing", "Temporary debug capture is draining", status=503)
                self._expire()
                if self._active:
                    raise BusinessError(
                        "debug_already_active", "Stop the current debug capture before starting another", status=409
                    )
                revision, activation_epoch = self._revision, self._activation_epoch
            retained, _ = self._inventory(True)
            with self._lock:
                self._retained = retained
                if retained >= byte_limit:
                    self._finish("quota_exhausted", "debug_quota_exhausted")
                    raise BusinessError(
                        "debug_quota_exhausted", "Retained debug files have reached the byte limit", status=409
                    )
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            private_path(self.directory, directory=True)
            if (self.directory / f"debug-{operation_id}.jsonl").exists():
                raise BusinessError("debug_capture_exists", "Debug capture identities cannot be reused", status=409)
            with self._lock:
                if self._closed or self._revision != revision or self._activation_epoch != activation_epoch:
                    raise BusinessError("debug_activation_cancelled", "Debug activation was cancelled", status=409)
                self._agent, self._operation = agent_id, operation_id
                self._generation += 1
                self._active, self._until = True, self._clock() + duration_s
                self._bytes = self._reserved = 0
                self._limit, self._identity = byte_limit, None
                self._reason, self._error = "capturing", ""
                self._revision += 1
                return self._snapshot()

    def admit(self, metadata: dict) -> DebugEnvelope | None:
        with self._lock:
            self._expire()
            if not self._active or metadata.get("agent_id") != self._agent:
                return None
            entry: dict[str, JSON] = {
                "record_id": str(uuid.uuid4()),
                "capture_id": self._operation,
                "boot_id": self.boot_id,
                "level": "DEBUG",
                "event": "span.completed" if metadata.get("event") == "span.completed" else "debug.sample",
                "component": "omni.v2.debug",
                "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds"),
            }
            for name in CORRELATIONS:
                value = metadata.get(name)
                if isinstance(value, str) and re.fullmatch(UUID_PATTERN, value):
                    entry[name] = value
            for name, length in (("trace_id", 32), ("span_id", 16)):
                value = metadata.get(name)
                if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{" + str(length) + "}", value):
                    entry[name] = value
            value = metadata.get("duration_ms")
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
                and 0 <= value <= 1e12
            ):
                entry["duration_ms"] = value
            value = metadata.get("outcome")
            if isinstance(value, str) and value in {outcome.value for outcome in Outcome}:
                entry["outcome"] = value
            value = metadata.get("operation")
            if isinstance(value, str) and value in OPERATIONS:
                entry["operation"] = value
            payload = (json.dumps(entry, separators=(",", ":")) + "\n").encode("utf-8")
            if self._retained + self._reserved + len(payload) > self._limit:
                self._finish("quota_exhausted", "debug_quota_exhausted")
                self._dropped += 1
                return None
            self._reserved += len(payload)
            return DebugEnvelope(self._operation, self._agent, self.boot_id, payload, self._generation)

    def discard(self, envelope: DebugEnvelope) -> None:
        with self._lock:
            if envelope.capture_id == self._operation and envelope.generation == self._generation:
                self._reserved = max(0, self._reserved - len(envelope.payload))
            self._dropped += 1

    def write(self, envelope: DebugEnvelope) -> bool:
        with self._io_lock:
            with self._lock:
                self._expire()
                if not self._active or (
                    envelope.capture_id != self._operation
                    or envelope.agent_id != self._agent
                    or envelope.boot_id != self.boot_id
                    or envelope.generation != self._generation
                ):
                    self.discard(envelope)
                    return False
                path = self.directory / f"debug-{self._operation}.jsonl"
                previous_bytes, previous_identity = self._bytes, self._identity
            try:
                # Recheck bounded inventory at the writer boundary. Another
                # program must not be able to bypass the global retained cap.
                retained, _ = self._inventory(False)
                with self._lock:
                    self._retained = retained
                    self._expire()
                    if not self._active or retained + len(envelope.payload) > self._limit:
                        self.discard(envelope)
                        if self._active:
                            self._finish("quota_exhausted", "debug_quota_exhausted")
                        return False
                # An already accepted writer may finish after stop. New starts
                # share _io_lock, so it cannot append into a later capture.
                identity = self._write_file(path, envelope.payload, previous_bytes, previous_identity)
                with self._lock:
                    self._identity = identity
                    self._bytes += len(envelope.payload)
                    self._retained += len(envelope.payload)
                    self._reserved = max(0, self._reserved - len(envelope.payload))
                    self._written += 1
                return True
            except (OSError, BusinessError) as exc:
                with self._lock:
                    self.discard(envelope)
                    self._finish("storage_unavailable", "debug_write_failed")
                if isinstance(exc, BusinessError):
                    raise OSError("Debug storage validation failed") from exc
                raise

    def _write_file(
        self, path: Path, payload: bytes, previous_bytes: int, previous_identity: tuple[int, int] | None
    ) -> tuple[int, int]:
        if any(
            current.is_symlink() or current.is_junction() for current in (self.directory, self.directory.parent, path)
        ):
            raise OSError("Debug storage path became a link")
        flags = os.O_WRONLY | os.O_APPEND | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        if previous_identity is None:
            flags |= os.O_CREAT | os.O_EXCL
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "ab") as stream:
            info = os.fstat(stream.fileno())
            identity = (info.st_dev, info.st_ino)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_size != previous_bytes
                or (previous_identity is not None and identity != previous_identity)
            ):
                raise OSError("Debug storage identity changed")
            if previous_identity is None:
                private_path(path)
            stream.write(payload)
        return identity
