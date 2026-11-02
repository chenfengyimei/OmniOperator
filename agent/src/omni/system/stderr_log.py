"""子进程 stderr 的有限管道泵：持续排空、跨块脱敏与文件轮转。"""

from __future__ import annotations

import codecs
import os
import threading
from pathlib import Path


class StderrLog:
    def __init__(self, path: Path, *, secrets=(), max_bytes=5242880, segments=3):
        self.path, self.max_bytes, self.segments = Path(path), max_bytes, segments
        self.secrets = sorted({str(value) for value in secrets if value}, key=len, reverse=True)
        self.failed = False
        self._suppress = any(len(s) > 8192 or "\n" in s or "\r" in s for s in self.secrets)
        read_fd, write_fd = os.pipe()
        self._read_fd = read_fd
        self.file = os.fdopen(write_fd, "w", encoding="utf-8")
        self.worker = threading.Thread(target=self._pump, name="stderr-log", daemon=True)
        self.worker.start()

    def _emit(self, text):
        if self._suppress or self.failed:
            return
        for secret in self.secrets:
            text = text.replace(secret, "[已脱敏]")
        data = text.encode("utf-8")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            paths = [self.path] + [self.path.with_name(self.path.name + f".{i}") for i in range(1, self.segments)]
            if any(p.is_symlink() for p in paths):
                raise OSError("日志路径非法")
            if self.path.exists() and self.path.stat().st_size + len(data) > self.max_bytes:
                paths[-1].unlink(missing_ok=True)
                for i in range(len(paths) - 1, 0, -1):
                    if paths[i - 1].exists():
                        paths[i - 1].replace(paths[i])
            with self.path.open("ab") as output:
                output.write(data[: self.max_bytes])
        except OSError:
            self.failed = True  # 不再落盘，但管道仍排空。

    def _pump(self):
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        pending = ""
        keep = max(map(len, self.secrets), default=1) - 1
        try:
            while data := os.read(self._read_fd, 4096):
                pending += decoder.decode(data)
                cut = max(0, len(pending) - keep)
                for secret in self.secrets:
                    start = pending.rfind(secret, 0, min(len(pending), cut + len(secret)))
                    if 0 <= start < cut < start + len(secret):
                        cut = start
                if cut:
                    self._emit(pending[:cut])
                    pending = pending[cut:]
            pending += decoder.decode(b"", final=True)
            if pending:
                self._emit(pending)
        except OSError:
            self.failed = True
        finally:
            os.close(self._read_fd)

    def close(self, wait_s=2):
        if not self.file.closed:
            self.file.close()
        self.worker.join(max(0, wait_s))
        return not self.worker.is_alive()
