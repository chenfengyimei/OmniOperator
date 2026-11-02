"""Bounded, single-writer JSON diagnostics. Business payloads belong in repositories."""

from __future__ import annotations

import contextvars
import datetime as dt
import json
import logging
import queue
import re
import sys
import threading
import time
import traceback
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from opentelemetry import trace

_context: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar("omni_v2_context", default={})
CONTEXT_FIELDS = {"request_id", "agent_id", "session_id", "run_id", "operation_id", "boot_id", "build_id"}
SECRET_KEY = re.compile(
    r"(?i)(authorization|cookie|password|secret|api.?key|access.?token|refresh.?token|client.?secret|code_verifier)"
)


@contextmanager
def bind_context(**fields: str) -> Iterator[None]:
    token = _context.set({**_context.get(), **{k: v for k, v in fields.items() if k in CONTEXT_FIELDS}})
    try:
        yield
    finally:
        _context.reset(token)


class Redactor:
    def __init__(self) -> None:
        self._secrets: set[str] = set()
        self._lock = threading.Lock()

    def register(self, value: str) -> None:
        if value:
            with self._lock:
                self._secrets.add(value)

    def text(self, value: str) -> str:
        with self._lock:
            secrets = sorted(self._secrets, key=len, reverse=True)
        for secret in secrets:
            value = value.replace(secret, "[redacted]")
        value = re.sub(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9+/_.=-]+", "[redacted]", value)
        value = re.sub(r"(?i)\bsk-[A-Za-z0-9_-]{8,}", "[redacted]", value)
        value = re.sub(
            r"""(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|cookie|authorization|sessdata|bili_jct)\s*["']?\s*[:=]\s*)[^\r\n]+""",
            r"\1[redacted]",
            value,
        )
        value = re.sub(r"(https?://)[^\s/@]+:[^\s/@]+@", r"\1[redacted]@", value)
        value = re.sub(r"(https?://[^\s?#]+)[?#][^\s]*", r"\1?[redacted]", value)
        return value[:8192]

    def value(self, value: Any, depth: int = 0) -> Any:
        if depth > 6:
            return "[depth_limit]"
        if isinstance(value, Mapping):
            return {
                str(k)[:100]: "[redacted]" if SECRET_KEY.search(str(k)) else self.value(v, depth + 1)
                for k, v in list(value.items())[:100]
            }
        if isinstance(value, (tuple, list)):
            return [self.value(v, depth + 1) for v in value[:100]]
        if isinstance(value, str):
            return self.text(value)
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return self.text(str(value))


class JsonLogSink(logging.Handler):
    def __init__(
        self,
        directory: Path,
        *,
        boot_id: str,
        build_id: str,
        capacity: int = 10_000,
        segment_bytes: int = 16 * 1024 * 1024,
        segments: int = 16,
        age_days: int = 14,
        stderr_only: bool = False,
    ) -> None:
        super().__init__()
        self.directory, self.boot_id, self.build_id = directory, boot_id, build_id
        self.redactor = Redactor()
        self.queue: queue.Queue[dict] = queue.Queue(maxsize=capacity)
        self.segment_bytes, self.segments, self.age_days = segment_bytes, segments, age_days
        self.stderr_only = stderr_only
        self.dropped = self.written = self.failures = 0
        self.last_error = ""
        self._stderr = sys.stderr
        self._emergency_at = 0.0
        self._closing = threading.Event()
        self._thread = threading.Thread(target=self._pump, name="omni-log-writer", daemon=True)
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            span = trace.get_current_span().get_span_context()
            entry = {
                "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds"),
                "level": record.levelname,
                "event": getattr(record, "event", "diagnostic"),
                "component": record.name,
                "boot_id": self.boot_id,
                "build_id": self.build_id,
                **_context.get(),
                "message": self.redactor.text(record.getMessage()),
            }
            if span.is_valid:
                entry.update(trace_id=f"{span.trace_id:032x}", span_id=f"{span.span_id:016x}")
            for name in ("duration_ms", "error_code", "status", "attempt", "retryable"):
                if hasattr(record, name):
                    entry[name] = self.redactor.value(getattr(record, name))
            if record.exc_info:
                entry["exception"] = self.redactor.text("".join(traceback.format_exception(*record.exc_info)))
            entry = self.redactor.value(entry)
            if self._closing.is_set():
                self.dropped += 1
                return
            self.queue.put_nowait(entry)
        except queue.Full:
            self.dropped += 1
            self._emergency("log_queue_full")
        except Exception:  # noqa: BLE001 -- logging failures must never escape into a use case
            self.dropped += 1
            self._emergency("log_encode_failed")

    def _emergency(self, code: str) -> None:
        now = time.monotonic()
        if now - self._emergency_at < 5:
            return
        self._emergency_at = now
        try:
            self._stderr.write(
                json.dumps({"level": "ERROR", "event": code, "dropped": self.dropped, "boot_id": self.boot_id}) + "\n"
            )
            self._stderr.flush()
        except (OSError, ValueError):
            pass

    def _write(self, payload: bytes) -> None:
        if self.stderr_only:
            self._stderr.write(payload.decode("utf-8"))
            self._stderr.flush()
            return
        if self.directory.is_symlink() or self.directory.is_junction():
            raise OSError("Log directory is a link")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        paths = [self.directory / ("runtime.jsonl" + (f".{n}" if n else "")) for n in range(self.segments)]
        if any(path.is_symlink() or path.is_junction() for path in paths):
            raise OSError("Log file is a link")
        cutoff = time.time() - self.age_days * 86400
        for path in paths[1:]:
            if path.exists() and path.stat().st_mtime < cutoff:
                path.unlink()
        if paths[0].exists() and (
            paths[0].stat().st_size + len(payload) > self.segment_bytes or paths[0].stat().st_mtime < cutoff
        ):
            paths[-1].unlink(missing_ok=True)
            for n in range(len(paths) - 1, 0, -1):
                if paths[n - 1].exists():
                    paths[n - 1].replace(paths[n])
        with paths[0].open("ab") as stream:
            stream.write(payload)

    def _pump(self) -> None:
        while not self._closing.is_set() or not self.queue.empty():
            try:
                entry = self.queue.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                payload = (json.dumps(entry, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode(
                    "utf-8"
                )
                if len(payload) > min(self.segment_bytes, 32768):
                    entry = {k: v for k, v in entry.items() if k not in {"message", "exception"}}
                    entry["truncated"] = True
                    payload = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
                self._write(payload)
                self.written += 1
            except (OSError, ValueError, TypeError):
                self.failures += 1
                self.dropped += 1
                self.last_error = "log_write_failed"
                self._emergency(self.last_error)
            finally:
                self.queue.task_done()

    def snapshot(self) -> dict:
        return {
            "queued": self.queue.qsize(),
            "dropped": self.dropped,
            "written": self.written,
            "failures": self.failures,
            "error_code": self.last_error,
            "writer_alive": self._thread.is_alive(),
        }

    def shutdown(self, timeout_s: float = 5) -> bool:
        self._closing.set()
        self._thread.join(max(0, timeout_s))
        return not self._thread.is_alive()

    def close(self) -> None:
        self.shutdown()
        super().close()


class LoggingSession:
    def __init__(self, sink: JsonLogSink, *, level: int = logging.INFO) -> None:
        self.sink = sink
        self._logger = logging.getLogger("omni.v2")
        self._level, self._propagate = self._logger.level, self._logger.propagate
        self._logger.setLevel(level)
        self._logger.propagate = False
        self._logger.addHandler(sink)

    def close(self, timeout_s: float = 5) -> bool:
        self._logger.removeHandler(self.sink)
        self._logger.setLevel(self._level)
        self._logger.propagate = self._propagate
        return self.sink.shutdown(timeout_s)
