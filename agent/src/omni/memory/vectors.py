"""sqlite-vec 派生索引。与事实同库；遗忘/纠错触发器只用普通 SQL，无扩展依赖。"""

from __future__ import annotations

import hashlib
import sqlite3
import struct
import threading
from pathlib import Path


class VectorIndex:
    def __init__(self, path: Path, *, model: str, dimensions: int, scopes: list[str]):
        self.model, self.dimensions, self.scopes = model, dimensions, tuple(scopes)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=5)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        self.available, self.error = False, ""
        try:
            import sqlite_vec

            self._conn.enable_load_extension(True)
            sqlite_vec.load(self._conn)
            self.available = True
        except (ImportError, AttributeError, OSError, sqlite3.Error):
            self.error = "sqlite-vec 不可用，保留 FTS 检索"
        finally:
            if hasattr(self._conn, "enable_load_extension"):
                self._conn.enable_load_extension(False)
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS fact_vectors (
                fact_id INTEGER NOT NULL, model TEXT NOT NULL, dimensions INTEGER NOT NULL,
                source_hash TEXT NOT NULL, source_text TEXT NOT NULL, embedding BLOB NOT NULL,
                indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(fact_id, model)
            );
            CREATE TRIGGER IF NOT EXISTS fact_vectors_delete AFTER DELETE ON facts
            BEGIN DELETE FROM fact_vectors WHERE fact_id=old.id; END;
            CREATE TRIGGER IF NOT EXISTS fact_vectors_update AFTER UPDATE ON facts
            BEGIN DELETE FROM fact_vectors WHERE fact_id=old.id; END;
        """)
        self._conn.commit()

    def pending(self, limit: int) -> list[dict]:
        if not self.available or not self.scopes:
            return []
        placeholders = ",".join("?" for _ in self.scopes)
        with self._lock:
            rows = self._conn.execute(
                "SELECT f.id,f.content,f.scope,f.source FROM facts f LEFT JOIN fact_vectors v "
                "ON v.fact_id=f.id AND v.model=? "
                f"WHERE f.scope IN ({placeholders}) AND (v.fact_id IS NULL OR v.source_text!=f.content) "
                "ORDER BY f.id LIMIT ?",
                (self.model, *self.scopes, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def put(self, fact: dict, vector: list[float]) -> bool:
        if len(vector) != self.dimensions:
            raise ValueError("向量维度变化，必须使用新的模型版本重建")
        packed = struct.pack(f"<{len(vector)}f", *vector)
        with self._lock, self._conn:
            # 网络期间被删除/纠错/换 scope 的内容不能重新落回索引。
            cursor = self._conn.execute(
                "INSERT OR REPLACE INTO fact_vectors(fact_id,model,dimensions,source_hash,source_text,embedding) "
                "SELECT id,?,?,?,?,? FROM facts WHERE id=? AND content=? AND scope=?",
                (
                    self.model,
                    self.dimensions,
                    hashlib.sha256(fact["content"].encode()).hexdigest(),
                    fact["content"],
                    packed,
                    fact["id"],
                    fact["content"],
                    fact["scope"],
                ),
            )
            return cursor.rowcount == 1

    def search(self, vector: list[float], *, scope: str, limit: int = 8) -> list[dict]:
        if not self.available or scope not in self.scopes:
            return []
        if len(vector) != self.dimensions:
            raise ValueError("查询向量维度不匹配")
        packed = struct.pack(f"<{len(vector)}f", *vector)
        with self._lock:
            rows = self._conn.execute(
                "SELECT f.id,f.content,f.scope,f.source,v.source_hash,"
                "vec_distance_cosine(v.embedding,?) AS distance FROM fact_vectors v JOIN facts f ON f.id=v.fact_id "
                "WHERE v.model=? AND v.dimensions=? AND f.scope=? AND v.source_text=f.content "
                "ORDER BY distance,f.id DESC LIMIT ?",
                (packed, self.model, self.dimensions, scope, max(1, limit)),
            ).fetchall()
            return [dict(row) for row in rows]

    def status(self) -> dict:
        with self._lock:
            count = self._conn.execute("SELECT count(*) FROM fact_vectors WHERE model=?", (self.model,)).fetchone()[0]
        return {
            "available": self.available,
            "error": self.error,
            "indexed": count,
            "model_fingerprint": self.model,
            "dimensions": self.dimensions,
            "pending": bool(self.pending(1)),
        }

    def close(self):
        with self._lock:
            self._conn.close()
