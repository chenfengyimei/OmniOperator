"""FTS + 向量 RRF 融合；后台有界增量索引，网络请求不占数据库事务。"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable
from typing import Any


class HybridMemory:
    def __init__(self, store, index, provider, leases, *, run_ref=None):
        self.store, self.index, self.provider, self.leases = store, index, provider, leases
        self.run_ref = run_ref
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.last_error = ""
        self._closed = False
        self.admission_context: Callable[..., Any] = contextlib.nullcontext

    def start(self) -> None:
        with self._lock:
            if self._thread is not None or not self.index.available:
                return
            self._thread = threading.Thread(target=self._worker, name="omni-vector-index", daemon=True)
            self._thread.start()

    def _worker(self):
        while not self._stop.is_set():
            try:
                more = self.index_pending()
                self.last_error = ""
            except Exception as exc:  # noqa: BLE001, S110  （有意容错：边界转换/生命周期清理不阻断）
                more = 0
                self.last_error = f"索引暂停（{type(exc).__name__}），保留关键词检索"
            self._wake.wait(1 if more else 30)
            self._wake.clear()

    def index_pending(self) -> int:
        factory = self.admission_context
        with factory():
            return self._index_pending()

    def _index_pending(self) -> int:
        token = self.leases.acquire("memory:embedding", ttl_s=max(120, self.provider.config.timeout_s * 3))
        try:
            rows = self.index.pending(self.provider.config.batch_size)
            if not rows:
                return 0
            vectors = self.provider.embed([row["content"] for row in rows])
            if not self.leases.renew(token, ttl_s=120):
                raise RuntimeError("索引租约失效")
            return sum(self.index.put(row, vector) for row, vector in zip(rows, vectors))
        finally:
            self.leases.release(token)

    def search(self, query: str, limit: int = 8, *, scope: str = "private") -> list[str]:
        lexical = self.store.search_facts(query, limit=max(limit * 2, 16), scope=scope)
        if not query.strip() or scope not in self.index.scopes or not self.index.available:
            return self.store.filter_facts(lexical, scope=scope)[:limit]
        handle = self.run_ref.get() if self.run_ref else None
        try:
            vector = self.provider.embed(
                [query],
                run_id=getattr(handle, "run_id", None),
                session_id=getattr(getattr(handle, "identity", None), "session_id", None),
                run_budget=getattr(handle, "budget", None),
            )[0]
            semantic = self.index.search(vector, scope=scope, limit=max(limit * 2, 16))
        except Exception as exc:  # noqa: BLE001, S110  （有意容错：边界转换/生命周期清理不阻断）
            self.last_error = f"向量查询降级（{type(exc).__name__}）"
            return self.store.filter_facts(lexical, scope=scope)[:limit]
        scores: dict[str, float] = {}
        for ranked in (lexical, [r["content"] for r in semantic]):
            for rank, content in enumerate(dict.fromkeys(ranked), 1):
                scores[content] = scores.get(content, 0) + 1 / (60 + rank)
        # 网络查询期间可能发生遗忘；返回前再次以源库与 scope 为准。
        ranked = sorted(scores, key=lambda text: -scores[text])
        return self.store.filter_facts(ranked, scope=scope)[:limit]

    def status(self) -> dict[str, Any]:
        return {
            **self.index.status(),
            "provider": self.provider.config.provider,
            "model": self.provider.config.model,
            "last_error": self.last_error,
            "worker_alive": bool(self._thread and self._thread.is_alive()),
        }

    def close(self, wait_s=5) -> bool:
        if self._closed:
            return True
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(wait_s)
            if self._thread.is_alive():
                return False
        self.index.close()
        self._closed = True
        return True
