"""会话全量记忆（M2）：对话 + 工具过程的完整流水（比 M1 多工具与系统边界事件）。

职责：
- 在对话全量之外，逐条记录工具调用（名称/参数/结果/状态）与回合边界事件
  （取消、步数收敛、中断）——供"那天到底执行了什么"的查询与排查；
- 参数与结果按上限截断（超长是常态：文件内容、屏幕树、命令输出都可能出现）；
- 与 DialogueLog 同构的按天/会话/run_id 索引、分页精确读取与检索。

明确不做：不进上下文（排查/整理才按需读取）；不替代 state.db 的 run_events
（那是运行观测账本，本模块是长期记忆存档，保留口径与生命周期都不同）。
"""
from __future__ import annotations

import datetime as dt
import functools
import json
import sqlite3
import threading
from pathlib import Path

from ..config import DATA_DIR

_SCHEMA = """
CREATE TABLE IF NOT EXISTS session_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    run_id TEXT NOT NULL DEFAULT '',
    day TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    tool_name TEXT,
    tool_args TEXT,
    tool_result TEXT,
    tool_status TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_session_log_session ON session_log(session_id, id);
CREATE INDEX IF NOT EXISTS idx_session_log_day ON session_log(day, id);
CREATE INDEX IF NOT EXISTS idx_session_log_run ON session_log(run_id, id);
"""

_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS session_log_fts USING fts5(
    content, tool_name, tool_args, tool_result,
    content='session_log', content_rowid='id', tokenize='{tokenize}'
);
"""

_FTS_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS slog_ai AFTER INSERT ON session_log BEGIN
    INSERT INTO session_log_fts(rowid, content, tool_name, tool_args, tool_result)
    VALUES (new.id, new.content, COALESCE(new.tool_name,''), COALESCE(new.tool_args,''), COALESCE(new.tool_result,''));
END;
CREATE TRIGGER IF NOT EXISTS slog_ad AFTER DELETE ON session_log BEGIN
    INSERT INTO session_log_fts(session_log_fts, rowid, content, tool_name, tool_args, tool_result)
    VALUES ('delete', old.id, old.content, COALESCE(old.tool_name,''), COALESCE(old.tool_args,''), COALESCE(old.tool_result,''));
END;
"""

TRUNCATE_ARGS_MARK = "…（参数超长，已按上限截断）"
TRUNCATE_RESULT_MARK = "…（结果超长，已按上限截断）"


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _match_expr(tokens: list[str]) -> str:
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in tokens)


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _locked(method):
    @functools.wraps(method)
    def wrapper(self: "SessionLog", *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


class SessionLog:
    """会话全量记忆的记忆单元（memory.db 新表）。"""

    def __init__(
        self,
        db_path: Path | None = None,
        *,
        max_args_chars: int = 4000,
        max_result_chars: int = 4000,
        max_entry_chars: int = 20000,
    ) -> None:
        path = db_path or (DATA_DIR / "memory.db")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._args_limit = int(max_args_chars)
        self._result_limit = int(max_result_chars)
        self._entry_limit = int(max_entry_chars)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        self._init_fts()
        self._conn.commit()

    def _init_fts(self) -> None:
        self.fts_enabled = False
        self.fts_tokenizer = ""
        tokenizer = "trigram"
        while tokenizer:
            try:
                self._conn.executescript(_FTS_SCHEMA.format(tokenize=tokenizer))
                self.fts_enabled = True
                self.fts_tokenizer = tokenizer
                break
            except sqlite3.OperationalError:
                tokenizer = "unicode61" if tokenizer == "trigram" else ""
        self._fts_triggers = False
        if self.fts_enabled:
            try:
                self._conn.executescript(_FTS_TRIGGERS)
                self._fts_triggers = True
            except sqlite3.OperationalError:
                self._fts_triggers = False

    # ---------------------------------------------------------------- 写入

    def _insert(
        self,
        session_id: int,
        run_id: str,
        kind: str,
        content: str,
        *,
        tool_name: str | None = None,
        tool_args: str | None = None,
        tool_result: str | None = None,
        tool_status: str | None = None,
        now: dt.datetime | None = None,
    ) -> int:
        at = (now or dt.datetime.now().astimezone()).isoformat(timespec="seconds")
        day = (now or dt.datetime.now().astimezone()).date().isoformat()
        cur = self._conn.execute(
            "INSERT INTO session_log(session_id, run_id, day, kind, content,"
            " tool_name, tool_args, tool_result, tool_status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                int(session_id),
                str(run_id or ""),
                day,
                kind,
                content,
                tool_name,
                tool_args,
                tool_result,
                tool_status,
                at,
            ),
        )
        if self.fts_enabled and not self._fts_triggers:
            self._conn.execute(
                "INSERT INTO session_log_fts(rowid, content, tool_name, tool_args, tool_result)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    int(cur.lastrowid),
                    content,
                    tool_name or "",
                    tool_args or "",
                    tool_result or "",
                ),
            )
        self._conn.commit()
        return int(cur.lastrowid)

    def _clip(self, text: str, limit: int, mark: str) -> str:
        if len(text) <= limit:
            return text
        return text[:limit] + mark

    @_locked
    def append_user(self, session_id: int, run_id: str, content: str, *,
                    channel: str = "", now: dt.datetime | None = None) -> int:
        return self._insert(
            session_id, run_id, "user", self._clip(str(content), self._entry_limit, "…（超长截断）"), now=now
        )

    @_locked
    def append_assistant(
        self, session_id: int, run_id: str, content: str, *,
        tool_calls: list | None = None, now: dt.datetime | None = None
    ) -> int:
        """中间助手消息：文本 + 本步发起的工具调用清单（供排查对账）。"""
        payload = ""
        if tool_calls:
            try:
                payload = json.dumps(
                    [
                        {"name": getattr(c.function, "name", None) or "", "arguments": getattr(c.function, "arguments", "")}
                        for c in tool_calls
                    ],
                    ensure_ascii=False,
                )
            except (TypeError, ValueError):
                payload = "（工具调用清单序列化失败）"
            payload = self._clip(payload, self._args_limit, TRUNCATE_ARGS_MARK)
        return self._insert(
            session_id, run_id, "assistant", self._clip(str(content), self._entry_limit, "…（超长截断）"),
            tool_args=payload or None, now=now,
        )

    @_locked
    def append_tool(
        self, session_id: int, run_id: str, name: str, args_json: str, result: str,
        status: str, *, now: dt.datetime | None = None
    ) -> int:
        return self._insert(
            session_id, run_id, "tool", "",
            tool_name=str(name or ""),
            tool_args=self._clip(str(args_json or ""), self._args_limit, TRUNCATE_ARGS_MARK),
            tool_result=self._clip(str(result or ""), self._result_limit, TRUNCATE_RESULT_MARK),
            tool_status=str(status or ""), now=now,
        )

    @_locked
    def append_system(self, session_id: int, run_id: str, note: str, *,
                      now: dt.datetime | None = None) -> int:
        return self._insert(
            session_id, run_id, "system", self._clip(str(note), 2000, "…"), now=now
        )

    # ---------------------------------------------------------------- 索引与精确读取

    @_locked
    def day_index(self, limit: int = 400) -> list[dict]:
        rows = self._conn.execute(
            "SELECT day, COUNT(DISTINCT session_id), COUNT(*), COALESCE(SUM(LENGTH(COALESCE(tool_result,'')) + LENGTH(content)), 0)"
            " FROM session_log GROUP BY day ORDER BY day DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
        return [
            {"day": str(r[0]), "sessions": int(r[1]), "entries": int(r[2]), "chars": int(r[3])}
            for r in rows
        ]

    @_locked
    def sessions_for_day(self, day: str) -> list[dict]:
        """某天的会话索引：含工具步计数（排查时先看哪场会话动作多）。"""
        rows = self._conn.execute(
            "SELECT session_id, COUNT(*) AS entries, SUM(kind='tool') AS tools, MIN(id) AS first_id"
            " FROM session_log WHERE day = ? GROUP BY session_id ORDER BY first_id ASC",
            (str(day),),
        ).fetchall()
        return [
            {
                "session_id": int(r[0]),
                "entries": int(r[1]),
                "tools": int(r[2] or 0),
            }
            for r in rows
        ]

    @_locked
    def entries(
        self,
        *,
        day: str | None = None,
        session_id: int | None = None,
        run_id: str | None = None,
        after: int = 0,
        limit: int = 200,
    ) -> list[dict]:
        sql = (
            "SELECT id, session_id, run_id, day, kind, content, tool_name, tool_args,"
            " tool_result, tool_status, created_at FROM session_log"
        )
        where: list[str] = []
        params: list = []
        if day is not None:
            where.append("day = ?")
            params.append(str(day))
        if session_id is not None:
            where.append("session_id = ?")
            params.append(int(session_id))
        if run_id:
            where.append("run_id = ?")
            params.append(str(run_id))
        if after:
            where.append("id > ?")
            params.append(int(after))
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id ASC LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))
        return [self._row(r) for r in self._conn.execute(sql, params).fetchall()]

    @staticmethod
    def _row(r) -> dict:
        return {
            "id": int(r[0]),
            "session_id": int(r[1]),
            "run_id": str(r[2]),
            "day": str(r[3]),
            "kind": str(r[4]),
            "content": str(r[5]),
            "tool_name": str(r[6] or ""),
            "tool_args": str(r[7] or ""),
            "tool_result": str(r[8] or ""),
            "tool_status": str(r[9] or ""),
            "created_at": str(r[10]),
        }

    @_locked
    def search(self, query: str, limit: int = 30) -> list[dict]:
        """检索范围含对话文本与工具名/参数/结果——排查"执行了什么"靠它。"""
        tokens = [t for t in str(query).split() if t]
        if not tokens:
            return []
        bound = max(1, min(int(limit), 100))
        if self.fts_enabled:
            rows = self._conn.execute(
                "SELECT l.id FROM session_log_fts JOIN session_log l ON l.id = session_log_fts.rowid"
                " WHERE session_log_fts MATCH ? ORDER BY bm25(session_log_fts) LIMIT ?",
                (_match_expr(tokens), bound),
            ).fetchall()
            if rows:
                return self._by_ids([int(r[0]) for r in rows])
        rows = self._like(tokens, bound)
        if rows:
            return rows
        bigrams = sorted(
            {
                t[i : i + 2]
                for t in tokens
                for i in range(len(t) - 1)
                if _has_cjk(t[i : i + 2])
            }
        )
        return self._like(bigrams, bound) if bigrams else []

    def _like(self, tokens: list[str], limit: int) -> list[dict]:
        clause = " OR ".join(
            "(content LIKE ? OR COALESCE(tool_name,'') LIKE ? OR COALESCE(tool_args,'') LIKE ?"
            " OR COALESCE(tool_result,'') LIKE ?)"
            for _ in tokens
        )
        params: list = []
        for t in tokens:
            like = f"%{t}%"
            params.extend([like, like, like, like])
        sql = (
            "SELECT id, session_id, run_id, day, kind, content, tool_name, tool_args,"
            " tool_result, tool_status, created_at FROM session_log"
            f" WHERE {clause} ORDER BY id DESC LIMIT ?"
        )
        return [self._row(r) for r in self._conn.execute(sql, [*params, limit]).fetchall()]

    def _by_ids(self, ids: list[int]) -> list[dict]:
        result: list[dict] = []
        for offset in range(0, len(ids), 100):
            chunk = ids[offset : offset + 100]
            placeholders = ",".join("?" for _ in chunk)
            rows = self._conn.execute(
                f"SELECT id, session_id, run_id, day, kind, content, tool_name, tool_args,"
                f" tool_result, tool_status, created_at FROM session_log WHERE id IN ({placeholders})",
                chunk,
            ).fetchall()
            result.extend(self._row(r) for r in rows)
        order = {value: index for index, value in enumerate(ids)}
        result.sort(key=lambda item: order[item["id"]])
        return result

    # ---------------------------------------------------------------- 统计与清理

    @_locked
    def stats(self) -> dict:
        row = self._conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT day), COUNT(DISTINCT session_id), COUNT(DISTINCT run_id),"
            " COALESCE(SUM(kind='tool'), 0), COALESCE(MIN(day), ''), COALESCE(MAX(day), '')"
            " FROM session_log"
        ).fetchone()
        return {
            "entries": int(row[0]),
            "days": int(row[1]),
            "sessions": int(row[2]),
            "runs": int(row[3]),
            "tools": int(row[4]),
            "first_day": str(row[5] or ""),
            "last_day": str(row[6] or ""),
        }

    @_locked
    def prune(self, retention_days: int, *, today: str | None = None) -> int:
        days = int(retention_days)
        if days <= 0:
            return 0
        base = dt.date.fromisoformat(
            today or dt.datetime.now().astimezone().date().isoformat()
        )
        cutoff = (base - dt.timedelta(days=days)).isoformat()
        cur = self._conn.execute("DELETE FROM session_log WHERE day < ?", (cutoff,))
        self._conn.commit()
        return int(cur.rowcount)

    def close(self) -> None:
        with self._lock:
            self._conn.close()
