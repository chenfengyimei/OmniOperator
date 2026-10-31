"""可恢复的人类终端：持续排空、有限输出环与单写者；不向模型暴露。"""
from __future__ import annotations

import threading
import time
import uuid
from collections import deque


class ManagedTerminal:
    def __init__(self, session, owner: str, shell: str, cwd: str, *, buffer_bytes: int, on_exit, clock=time.monotonic):
        self.id, self.owner = uuid.uuid4().hex, owner
        self.session, self.shell, self.cwd = session, shell, cwd
        self.label = shell
        self._chunks = deque()
        self._bytes, self._seq, self._limit = 0, 0, buffer_bytes
        self._lock = threading.RLock()
        self._clock, self.detached_at = clock, clock()
        self.connected = False
        self.status, self.exit_code = 'running', None
        self._on_exit = on_exit
        self.worker = threading.Thread(target=self._read, name=f'terminal-{self.id[:8]}', daemon=True)
        self.worker.start()

    def _read(self):
        try:
            while True:
                text = self.session.read()
                if not text:
                    break
                for start in range(0, len(text), 4096):
                    chunk = text[start:start+4096]
                    size = len(chunk.encode('utf-8'))
                    with self._lock:
                        self._seq += 1
                        self._chunks.append((self._seq, chunk, size))
                        self._bytes += size
                        while self._bytes > self._limit and self._chunks:
                            self._bytes -= self._chunks.popleft()[2]
        except Exception:
            pass  # EOF 和关闭均进入统一存活核验。
        finally:
            with self._lock:
                alive = self.session.isalive()
                self.status = 'unknown' if alive else 'exited'
                if not alive:
                    self.exit_code = getattr(self.session._pty, 'exitstatus', None)
            self._on_exit(self.session)

    def brief(self):
        with self._lock:
            return {'id':self.id, 'label':self.label, 'shell':self.shell, 'cwd':self.cwd,
                    'status':self.status, 'connected':self.connected, 'exit_code':self.exit_code,
                    'last_seq':self._seq}

    def attach(self):
        with self._lock:
            if self.connected:
                raise RuntimeError('终端已有写入连接')
            self.connected = True

    def detach(self):
        with self._lock:
            self.connected, self.detached_at = False, self._clock()

    def replay(self, after: int):
        with self._lock:
            earliest = self._chunks[0][0] if self._chunks else self._seq + 1
            gap = after < earliest - 1
            rows = [(seq, data) for seq, data, _ in self._chunks if seq > after][:16]
            return gap, rows

    def expired(self, ttl: float):
        with self._lock:
            return not self.connected and self._clock() - self.detached_at >= ttl
