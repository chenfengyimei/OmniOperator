"""对话全量记忆（M1）：完整对话文本一字不差的追加型存档。

职责：
- 记录 user 与 assistant 的全部对话文本（含带工具调用回合里非空的助手文本），
  按「天」打索引用于列表读取与整理取材；
- 提供按天/会话的索引、分页精确读取与 FTS/LIKE 检索——绝不一次性把全量
  记忆交给调用方，读取一律带 limit 游标；
- 单条超长按上限截断并留标记（原文太大通常是异常输出，完整版归 session_log）。

明确不做：不记工具调用细节（归 SessionLog）；不做摘要压缩（归 JournalStore）；
不参与上下文组装（组装仍以 messages 表为准，本模块对组装器只写不读）。
"""

from __future__ import annotations

import datetime as dt
import functools
import sqlite3
import threading
from pathlib import Path

from ..config import DATA_DIR

_SCHEMA = """
CREATE TABLE IF NOT EXISTS dialogue_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dialogue_session ON dialogue_log(session_id, id);
CREATE INDEX IF NOT EXISTS idx_dialogue_day ON dialogue_log(day, id);
"""

_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS dialogue_fts USING fts5(
    content, content='dialogue_log', content_rowid='id', tokenize='{tokenize}'
);
"""

_FTS_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS dialogue_ai AFTER INSERT ON dialogue_log BEGIN
    INSERT INTO dialogue_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS dialogue_ad AFTER DELETE ON dialogue_log BEGIN
    INSERT INTO dialogue_fts(dialogue_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS dialogue_au AFTER UPDATE ON dialogue_log BEGIN
    INSERT INTO dialogue_fts(dialogue_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO dialogue_fts(rowid, content) VALUES (new.id, new.content);
END;
"""

ROLES = ("user", "assistant")
TRUNCATE_MARK = "\n…（对话全量记录超长，已按上限截断；完整工具过程见会话全量记忆）"


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _match_expr(tokens: list[str]) -> str:
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in tokens)


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _today() -> str:
    return dt.datetime.now().astimezone().date().isoformat()


def _locked(method):
    """公开方法统一可重入锁：Web 线程池 / agent 线程 / 整理 worker 三方并发。"""

    @functools.wraps(method)
    def wrapper(self: "DialogueLog", *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


class DialogueLog:
    """对话全量记忆的记忆单元（memory.db 新表，零迁移负担）。"""

    def __init__(self, db_path: Path | None = None, *, max_entry_chars: int = 20_000) -> None:
        path = db_path or (DATA_DIR / "memory.db")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._limit = int(max_entry_chars)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        self._init_fts()
        self._conn.commit()

    def _init_fts(self) -> None:
        """trigram 优先、unicode61 兜底、都建不了退 LIKE；触发器建不了时手写同步。"""
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
                self._fts_triggers = False  # 老库建不了触发器：插入时手写同步

    # ---------------------------------------------------------------- 写入

    @_locked
    def append(
        self,
        session_id: int,
        role: str,
        content: str,
        *,
        channel: str = "",
        now: dt.datetime | None = None,
    ) -> int:
        """追加一条对话记录；超长截断。返回行 id（读取游标）。"""
        if role not in ROLES:
            raise ValueError(f"对话全量记忆只收 user/assistant，收到 {role!r}")
        text = str(content)
        if len(text) > self._limit:
            text = text[: self._limit] + TRUNCATE_MARK
        at = (now or dt.datetime.now().astimezone()).isoformat(timespec="seconds")
        day = (now or dt.datetime.now().astimezone()).date().isoformat()
        cur = self._conn.execute(
            "INSERT INTO dialogue_log(session_id, day, role, content, channel, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (int(session_id), day, role, text, str(channel or ""), at),
        )
        if self.fts_enabled and not self._fts_triggers:
            self._conn.execute(
                "INSERT INTO dialogue_fts(rowid, content) VALUES (?, ?)",
                (int(cur.lastrowid), text),
            )
        self._conn.commit()
        return int(cur.lastrowid)

    # ---------------------------------------------------------------- 索引与精确读取

    @_locked
    def day_index(self, limit: int = 400) -> list[dict]:
        """按天索引（列表读取入口）：天 → 会话数/条数/字符量，最新在前。"""
        rows = self._conn.execute(
            "SELECT day, COUNT(DISTINCT session_id), COUNT(*), COALESCE(SUM(LENGTH(content)), 0)"
            " FROM dialogue_log GROUP BY day ORDER BY day DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
        return [{"day": str(r[0]), "sessions": int(r[1]), "entries": int(r[2]), "chars": int(r[3])} for r in rows]

    @_locked
    def active_hours_by_day(self, limit_days: int = 30) -> dict[str, set[int]]:
        """每天出现过**用户消息**的小时集合（升序天 → 小时集合）。

        用途：作息画像（`safety/activity.py`）——按「天」而不是按「条数」
        统计，理由是一条长谈会把某个小时的条数拉高，而用户关心的是
        「通常这个点醒不醒着」。

        只统计 `role='user'`：assistant 的消息是我们自己回的，
        用户凌晨收到我们的回复不代表他凌晨还醒着。

        时间取 `created_at` 的**本地小时**（该字段是 ISO 带时区串，
        写入时用 `datetime.now().astimezone()`，所以它已经带本地偏移）。
        """
        rows = self._conn.execute(
            "SELECT day, created_at FROM dialogue_log WHERE role = 'user' ORDER BY id DESC LIMIT ?",
            (max(1, int(limit_days)) * 400,),
        ).fetchall()
        result: dict[str, set[int]] = {}
        for day, created_at in rows:
            text = str(created_at or "")
            # ISO 形如 2026-11-01T17:54:03+08:00 —— 第 11~13 位是 HH
            if len(text) < 13 or text[10] != "T":
                continue
            try:
                hour = int(text[11:13])
            except ValueError:
                continue
            result.setdefault(str(day), set()).add(hour)
        return result

    @_locked
    def days(self) -> list[str]:
        """全部有记录的天（升序）——整理 worker 枚举候选天用。"""
        rows = self._conn.execute("SELECT DISTINCT day FROM dialogue_log ORDER BY day ASC").fetchall()
        return [str(r[0]) for r in rows]

    @_locked
    def sessions_for_day(self, day: str) -> list[dict]:
        """某天的会话索引；标题取该会话首条 user 文本（自包含，不依赖 sessions 表）。"""
        rows = self._conn.execute(
            "SELECT session_id, COUNT(*) AS entries, MIN(id) AS first_id"
            " FROM dialogue_log WHERE day = ? GROUP BY session_id ORDER BY first_id ASC",
            (str(day),),
        ).fetchall()
        result: list[dict] = []
        for session_id, entries, first_id in rows:
            first = self._conn.execute(
                "SELECT role, content FROM dialogue_log WHERE id = ?", (int(first_id),)
            ).fetchone()
            title = str(first[1])[:24] if first and str(first[0]) == "user" else f"会话 {session_id}"
            result.append({"session_id": int(session_id), "entries": int(entries), "title": title})
        return result

    @_locked
    def entries(
        self,
        *,
        day: str | None = None,
        session_id: int | None = None,
        after: int = 0,
        limit: int = 200,
    ) -> list[dict]:
        """分页精确读取：按 id 游标正序，绝不整库返回。"""
        sql = "SELECT id, session_id, day, role, content, channel, created_at FROM dialogue_log"
        where: list[str] = []
        params: list = []
        if day is not None:
            where.append("day = ?")
            params.append(str(day))
        if session_id is not None:
            where.append("session_id = ?")
            params.append(int(session_id))
        if after:
            where.append("id > ?")
            params.append(int(after))
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id ASC LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))
        rows = self._conn.execute(sql, params).fetchall()
        return [
            {
                "id": int(r[0]),
                "session_id": int(r[1]),
                "day": str(r[2]),
                "role": str(r[3]),
                "content": str(r[4]),
                "channel": str(r[5]),
                "created_at": str(r[6]),
            }
            for r in rows
        ]

    @_locked
    def search(self, query: str, limit: int = 30) -> list[dict]:
        """FTS5 优先 → LIKE 全词 OR → 中文 2-gram（与 messages 检索同一降级链）。"""
        tokens = [t for t in str(query).split() if t]
        if not tokens:
            return []
        bound = max(1, min(int(limit), 100))
        if self.fts_enabled:
            rows = self._conn.execute(
                "SELECT l.id FROM dialogue_fts JOIN dialogue_log l ON l.id = dialogue_fts.rowid"
                " WHERE dialogue_fts MATCH ? ORDER BY bm25(dialogue_fts) LIMIT ?",
                (_match_expr(tokens), bound),
            ).fetchall()
            if rows:
                return self._by_ids([int(r[0]) for r in rows])
        rows = self._like(tokens, bound)
        if rows:
            return rows
        bigrams = sorted({t[i : i + 2] for t in tokens for i in range(len(t) - 1) if _has_cjk(t[i : i + 2])})
        return self._like(bigrams, bound) if bigrams else []

    def _like(self, tokens: list[str], limit: int) -> list[dict]:
        where = " OR ".join("content LIKE ?" for _ in tokens)
        rows = self._conn.execute(
            f"SELECT id, session_id, day, role, content, channel, created_at FROM dialogue_log"
            f" WHERE {where} ORDER BY id DESC LIMIT ?",
            [*(f"%{t}%" for t in tokens), limit],
        ).fetchall()
        return [self._row(r) for r in rows]

    def _by_ids(self, ids: list[int]) -> list[dict]:
        result: list[dict] = []
        for offset in range(0, len(ids), 100):
            chunk = ids[offset : offset + 100]
            placeholders = ",".join("?" for _ in chunk)
            rows = self._conn.execute(
                f"SELECT id, session_id, day, role, content, channel, created_at"
                f" FROM dialogue_log WHERE id IN ({placeholders})",
                chunk,
            ).fetchall()
            result.extend(self._row(r) for r in rows)
        order = {value: index for index, value in enumerate(ids)}
        result.sort(key=lambda item: order[item["id"]])
        return result

    @staticmethod
    def _row(r) -> dict:
        return {
            "id": int(r[0]),
            "session_id": int(r[1]),
            "day": str(r[2]),
            "role": str(r[3]),
            "content": str(r[4]),
            "channel": str(r[5]),
            "created_at": str(r[6]),
        }

    # ---------------------------------------------------------------- 整理支撑

    @_locked
    def max_id_for_day(self, day: str) -> int | None:
        row = self._conn.execute("SELECT MAX(id) FROM dialogue_log WHERE day = ?", (str(day),)).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    @_locked
    def has_activity(self, day: str) -> bool:
        row = self._conn.execute("SELECT 1 FROM dialogue_log WHERE day = ? LIMIT 1", (str(day),)).fetchone()
        return row is not None

    @_locked
    def material_for_day(self, day: str, *, max_chars: int) -> str:
        """整理取材：当天全部对话按序拼成带角色前缀的文本，超限保头留尾并标注。"""
        pieces: list[str] = []
        total = 0
        after = 0
        while True:  # entries 的 limit 有钳制，这里用游标翻完全天
            batch = self.entries(day=day, after=after, limit=1000)
            if not batch:
                break
            for entry in batch:
                line = f"[{entry['created_at'][11:16]}] {entry['role']}: {entry['content']}"
                pieces.append(line)
                total += len(line)
            after = batch[-1]["id"]
        if total <= max_chars:
            return "\n".join(pieces)
        mark = f"\n…（当天对话超过 {max_chars} 字符，中段已省略；完整原文可在记忆系统里按天读取）\n"
        if len(mark) + 20 > max_chars:  # 上限小到连标记都放不下：退短标记，保证还有正文
            mark = "\n…（超长截断）\n"
        budget = max(0, max_chars - len(mark))
        head: list[str] = []
        used = 0
        head_budget = int(budget * 0.7)
        for line in pieces:
            if used + len(line) > head_budget:
                break
            head.append(line)
            used += len(line)
        tail: list[str] = []
        for line in reversed(pieces):
            if used + len(line) > budget:
                break
            tail.append(line)
            used += len(line)
        tail.reverse()
        return "\n".join(head) + mark + "\n".join(tail)

    @_locked
    def stats(self) -> dict:
        row = self._conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT day), COUNT(DISTINCT session_id),"
            " COALESCE(SUM(LENGTH(content)), 0), COALESCE(MIN(day), ''), COALESCE(MAX(day), '')"
            " FROM dialogue_log"
        ).fetchone()
        return {
            "entries": int(row[0]),
            "days": int(row[1]),
            "sessions": int(row[2]),
            "chars": int(row[3]),
            "first_day": str(row[4] or ""),
            "last_day": str(row[5] or ""),
        }

    @_locked
    def prune(self, retention_days: int, *, today: str | None = None) -> int:
        """滚动清理超期记录（0=永不清理）；FTS 由触发器同步删除。"""
        days = int(retention_days)
        if days <= 0:
            return 0
        base = dt.date.fromisoformat(today or _today())
        cutoff = (base - dt.timedelta(days=days)).isoformat()
        cur = self._conn.execute("DELETE FROM dialogue_log WHERE day < ?", (cutoff,))
        self._conn.commit()
        return int(cur.rowcount)

    def close(self) -> None:
        with self._lock:
            self._conn.close()
