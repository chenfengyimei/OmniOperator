"""有界诊断日志。事件仅保留关联字段，排除对话、工具参数与结果正文。"""

from __future__ import annotations

import datetime as dt
import io
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

from .filelock import FileLock, LockTimeout


def redact(text: str) -> str:
    text = re.sub(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[^\s,;]+", r"\1[redacted]", text)
    text = re.sub(
        r'(?i)((?:api[_-]?key|access[_-]?token|password|sessdata|bili_jct|cookie)\s*["\x27]?\s*[:=]\s*["\x27]?)[^\s,;"\x27]+',
        r"\1[redacted]",
        text,
    )
    text = re.sub(r"(?i)\bsk-[A-Za-z0-9_-]{8,}", "[redacted]", text)
    text = re.sub(r"(https?://)[^\s/@]+:[^\s/@]+@", r"\1[redacted]@", text)
    text = re.sub(r"(https?://[^\s?#]+)[?#][^\s]*", r"\1?[redacted]", text)
    return text


class BoundedLog:
    def __init__(self, path: Path, *, max_bytes: int = 2_000_000, backups: int = 3, max_age_days: int = 14):
        self.path = Path(path)
        self.max_bytes, self.backups, self.max_age_days = max(1024, max_bytes), max(1, min(backups, 10)), max_age_days
        self.dropped = 0

    def append(self, text: str) -> bool:
        payload = (text[:8000].rstrip("\n") + "\n").encode("utf-8")[: self.max_bytes]
        try:
            with FileLock(self.path.with_suffix(self.path.suffix + ".lock"), timeout_s=0.2):
                cutoff = time.time() - self.max_age_days * 86400
                for index in range(1, self.backups + 1):
                    old = self.path.with_name(self.path.name + f".{index}")
                    if old.exists() and old.stat().st_mtime < cutoff:
                        old.unlink()
                if self.path.exists() and (
                    self.path.stat().st_size + len(payload) > self.max_bytes or self.path.stat().st_mtime < cutoff
                ):
                    for index in range(self.backups, 0, -1):
                        source = self.path if index == 1 else self.path.with_name(self.path.name + f".{index - 1}")
                        target = self.path.with_name(self.path.name + f".{index}")
                        if source.exists():
                            os.replace(source, target)
                with self.path.open("ab") as out:
                    out.write(payload)
            return True
        except (OSError, LockTimeout):
            self.dropped += 1
            return False


class EventLog:
    def __init__(self, log: BoundedLog):
        self.log = log

    def emit(self, event: dict) -> None:
        def label(value):
            value = str(value)
            return (
                value if re.fullmatch(r"[A-Za-z0-9_.:-]{0,100}", value) and not value.startswith("sk-") else "invalid"
            )

        clean = {
            key: label(event.get(key, "")) for key in ("run_id", "event_id", "kind", "tool", "status", "error_code")
        }
        clean["session_id"] = event.get("session_id") if isinstance(event.get("session_id"), int) else None
        clean["at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        self.log.append(json.dumps(clean, ensure_ascii=False, sort_keys=True))


class _ServiceStream(io.TextIOBase):
    def __init__(self, log):
        self.log, self.pending, self.lock = log, "", threading.RLock()

    @property
    def encoding(self):
        return "utf-8"

    def write(self, text):
        with self.lock:
            for part in str(text).splitlines(keepends=True):
                self.pending = (self.pending + part)[:8000]
                if part.endswith(("\n", "\r")):
                    self.log.append(redact(self.pending))
                    self.pending = ""
        return len(text)

    def flush(self):
        with self.lock:
            if self.pending:
                self.log.append(redact(self.pending))
                self.pending = ""


def configure_service_logging() -> None:
    """仅 ServiceManager 启动的 Python 服务；手动控制台不重定向。"""
    configured = os.environ.pop("OMNI_SERVICE_LOG", "")
    if not configured:
        return
    # ServiceManager 给本进程的是 NUL；实际日志文件由此处短开短关，允许 Windows 轮转。
    for stream in (sys.stdout, sys.stderr):
        stream.flush()
    stream = _ServiceStream(BoundedLog(Path(configured)))
    sys.stdout = sys.stderr = stream
