"""SQLite 记忆存储：会话 / 消息 / 事实记忆。

设计取舍（P0）：
- 单文件 SQLite + WAL，零外部服务，Windows/macOS 通用；
- 事实记忆先用 LIKE 检索，P1 再升级 sqlite-vec 向量检索；
- 工具调用的中间过程不持久化，只落 user 与最终 assistant 文本（够 P0 用）。
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

from ..config import DATA_DIR

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '新会话'
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

DEFAULT_TITLE = "新会话"


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


class MemoryStore:
    def __init__(self, db_path: Path | None = None) -> None:
        path = db_path or (DATA_DIR / "memory.db")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ---------- 会话 ----------

    def new_session(self, title: str = DEFAULT_TITLE) -> int:
        cur = self._conn.execute(
            "INSERT INTO sessions(created_at, title) VALUES (?, ?)", (_now(), title)
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def latest_session_id(self) -> int | None:
        row = self._conn.execute(
            "SELECT id FROM sessions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return int(row[0]) if row else None

    def list_sessions(self, limit: int = 20) -> list[tuple[int, str, str]]:
        rows = self._conn.execute(
            "SELECT id, title, created_at FROM sessions ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [(int(r[0]), str(r[1]), str(r[2])) for r in rows]

    def session_exists(self, session_id: int) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return row is not None

    def rename_session(self, session_id: int, title: str) -> None:
        self._conn.execute(
            "UPDATE sessions SET title = ? WHERE id = ?", (title, session_id)
        )
        self._conn.commit()

    # ---------- 消息 ----------

    def append_message(
        self,
        session_id: int,
        role: str,
        content: str,
        *,
        auto_title: bool = True,
    ) -> None:
        self._conn.execute(
            "INSERT INTO messages(session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, role, content, _now()),
        )
        # 第一条用户消息自动成为会话标题（/list 里好认）
        if auto_title and role == "user":
            row = self._conn.execute(
                "SELECT title FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row and row[0] == DEFAULT_TITLE:
                self.rename_session(session_id, content[:24])
        self._conn.commit()

    def recent_messages(self, session_id: int, limit: int) -> list[tuple[str, str]]:
        rows = self._conn.execute(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [(str(r[0]), str(r[1])) for r in reversed(rows)]

    # ---------- 事实记忆 ----------

    def save_fact(self, content: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO facts(content, created_at) VALUES (?, ?)", (content, _now())
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def search_facts(self, query: str, limit: int = 5) -> list[str]:
        tokens = [t for t in query.split() if t]
        if not tokens:  # 空查询：返回最近的记忆
            rows = self._conn.execute(
                "SELECT content FROM facts ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [str(r[0]) for r in rows]
        where = " AND ".join("content LIKE ?" for _ in tokens)
        params = [f"%{t}%" for t in tokens]
        params.append(limit)
        rows = self._conn.execute(
            f"SELECT content FROM facts WHERE {where} ORDER BY id DESC LIMIT ?",  # noqa: S608 —— 模板由 token 数生成，参数全部绑定
            params,
        ).fetchall()
        return [str(r[0]) for r in rows]

    def close(self) -> None:
        self._conn.close()
