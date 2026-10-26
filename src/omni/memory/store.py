"""SQLite 记忆存储：会话 / 消息 / 事实 / 用户档案 / 全文检索 / review 日志。

设计取舍（P1）：
- 单文件 SQLite + WAL + busy_timeout，零外部服务，Windows/macOS 通用；
- 全文检索用 FTS5 trigram tokenizer（对中文原生友好，2字以上可查）；
  FTS5 不可用时自动降级到 LIKE 三级链，功能不缺失只损失排序质量；
- facts/messages 写入时同步进 FTS 索引，每次打开 'rebuild' 一次
  补齐旧数据（当前数据量小，重建成本可忽略）；
- 用户档案限长（PROFILE_MAX_CHARS）：写满时返回错误+当前档案+剩余额度，
  逼调用方在当轮合并——这是 Hermes 验证过的"限额+报错=自整理"协议
  （xuexi/NOTES.md §1.3）；
- 工具调用中间过程不持久化，只落 user 与最终 assistant 文本。
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
CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    outcome TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    content, content='facts', content_rowid='id', tokenize='{tokenize}'
);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content, content='messages', content_rowid='id', tokenize='{tokenize}'
);
"""

PROFILE_MAX_CHARS = 2000

DEFAULT_TITLE = "新会话"


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _match_expr(tokens: list[str]) -> str:
    """FTS5 MATCH 表达式：每个 token 短语化后 OR 连接（防特殊字符破坏语法）。"""
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in tokens)


class MemoryStore:
    def __init__(self, db_path: Path | None = None) -> None:
        path = db_path or (DATA_DIR / "memory.db")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        self._init_fts()
        self._conn.commit()

    def _init_fts(self) -> None:
        """优先 trigram（中文友好）；不可用时逐级退到 unicode61 / 无 FTS。"""
        self.fts_enabled = False
        for tokenize in ("trigram", "unicode61"):
            try:
                self._conn.executescript(_FTS_SCHEMA.format(tokenize=tokenize))
            except sqlite3.OperationalError:
                continue
            # 'rebuild' 重建补齐既有行（外部内容表）
            try:
                self._conn.execute("INSERT INTO facts_fts(facts_fts) VALUES ('rebuild')")
                self._conn.execute("INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')")
            except sqlite3.OperationalError:
                continue
            self.fts_enabled = True
            return

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
        cur = self._conn.execute(
            "INSERT INTO messages(session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, role, content, _now()),
        )
        if self.fts_enabled:
            self._conn.execute(
                "INSERT INTO messages_fts(rowid) VALUES (?)", (int(cur.lastrowid),)
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

    def search_messages(
        self, query: str, limit: int = 8
    ) -> list[tuple[int, str, str, str]]:
        """跨会话搜历史消息：FTS5 优先，LIKE 兜底。返回 (session_id, role, content, created_at)。"""
        tokens = [t for t in query.split() if t]
        if not tokens:
            return []
        if self.fts_enabled:
            rows = self._conn.execute(
                "SELECT m.session_id, m.role, m.content, m.created_at FROM messages_fts "
                "JOIN messages m ON m.id = messages_fts.rowid WHERE messages_fts MATCH ? "
                "ORDER BY bm25(messages_fts) LIMIT ?",
                (_match_expr(tokens), limit),
            ).fetchall()
            if rows:
                return [(int(r[0]), str(r[1]), str(r[2]), str(r[3])) for r in rows]
        # LIKE 兜底：全词 OR → 2-gram OR
        rows = self._like_messages(tokens, limit)
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
        return self._like_messages(bigrams, limit) if bigrams else []

    def _like_messages(
        self, tokens: list[str], limit: int
    ) -> list[tuple[int, str, str, str]]:
        where = " OR ".join("content LIKE ?" for _ in tokens)  # 模板由 token 数生成，参数绑定
        rows = self._conn.execute(
            f"SELECT session_id, role, content, created_at FROM messages "
            f"WHERE {where} ORDER BY id DESC LIMIT ?",
            [*(f"%{t}%" for t in tokens), limit],
        ).fetchall()
        return [(int(r[0]), str(r[1]), str(r[2]), str(r[3])) for r in rows]

    # ---------- 事实记忆 ----------

    def save_fact(self, content: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO facts(content, created_at) VALUES (?, ?)", (content, _now())
        )
        if self.fts_enabled:
            self._conn.execute(
                "INSERT INTO facts_fts(rowid) VALUES (?)", (int(cur.lastrowid),)
            )
        self._conn.commit()
        return int(cur.lastrowid)

    def search_facts(self, query: str, limit: int = 5) -> list[str]:
        """多级降级检索：FTS5（bm25 排序）→ 全词 AND → 全词 OR → 中文 2-gram 兜底。

        2-gram 兜底应对"我喜欢喝什么"这类整句式查询——"喜欢"等片段能
        命中"用户喜欢喝拿铁"（FTS trigram 需要 ≥3 字，短查询走后面几级）。
        """
        tokens = [t for t in query.split() if t]
        if not tokens:  # 空查询：返回最近的记忆
            rows = self._conn.execute(
                "SELECT content FROM facts ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [str(r[0]) for r in rows]
        if self.fts_enabled:
            try:
                rows = self._conn.execute(
                    "SELECT content FROM facts_fts JOIN facts ON facts.id = facts_fts.rowid "
                    "WHERE facts_fts MATCH ? ORDER BY bm25(facts_fts) LIMIT ?",
                    (_match_expr(tokens), limit),
                ).fetchall()
                if rows:
                    return [str(r[0]) for r in rows]
            except sqlite3.OperationalError:
                pass  # 查询过短等场景，走 LIKE 链
        and_where = " AND ".join("content LIKE ?" for _ in tokens)
        rows = self._fetch_facts(and_where, [f"%{t}%" for t in tokens], limit)
        if rows:
            return rows
        or_where = " OR ".join("content LIKE ?" for _ in tokens)
        rows = self._fetch_facts(or_where, [f"%{t}%" for t in tokens], limit)
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
        if bigrams:
            where = " OR ".join("content LIKE ?" for _ in bigrams)
            return self._fetch_facts(where, [f"%{b}%" for b in bigrams], limit)
        return []

    def _fetch_facts(self, where: str, params: list[str], limit: int) -> list[str]:
        rows = self._conn.execute(  # 模板由 token 数生成，参数全部绑定
            f"SELECT content FROM facts WHERE {where} ORDER BY id DESC LIMIT ?",
            [*params, limit],
        ).fetchall()
        return [str(r[0]) for r in rows]

    # ---------- 用户档案（USER.md 式，限额+报错驱动自整理） ----------

    def profile_lines(self) -> list[tuple[int, str]]:
        rows = self._conn.execute(
            "SELECT id, content FROM profile ORDER BY id"
        ).fetchall()
        return [(int(r[0]), str(r[1])) for r in rows]

    def profile_usage_chars(self) -> int:
        return sum(len(content) for _, content in self.profile_lines())

    def profile_add(self, content: str) -> tuple[bool, str]:
        """超限时返回 (False, 错误+当前档案+剩余额度)，逼调用方当轮合并。"""
        content = content.strip()
        if not content:
            return False, "错误：档案内容不能为空"
        usage = self.profile_usage_chars()
        if usage + len(content) > PROFILE_MAX_CHARS:
            current = "\n".join(
                f"[{lid}] {text}" for lid, text in self.profile_lines()
            )
            return False, (
                f"档案已满（{usage}/{PROFILE_MAX_CHARS} 字符，剩余 "
                f"{PROFILE_MAX_CHARS - usage}），放不下这条。请先合并或删减现有条目再添加。"
                f"当前档案：\n{current}"
            )
        now = _now()
        self._conn.execute(
            "INSERT INTO profile(content, created_at, updated_at) VALUES (?, ?, ?)",
            (content, now, now),
        )
        self._conn.commit()
        return True, f"已加入档案：{content}（当前 {usage + len(content)}/{PROFILE_MAX_CHARS} 字符）"

    def profile_edit(self, line_id: int, content: str) -> bool:
        cur = self._conn.execute(
            "UPDATE profile SET content = ?, updated_at = ? WHERE id = ?",
            (content.strip(), _now(), line_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def profile_delete(self, line_id: int) -> bool:
        cur = self._conn.execute("DELETE FROM profile WHERE id = ?", (line_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def profile_block(self) -> str:
        """系统提示词注入用的人物档案快照块；空档案返回空串。"""
        lines = self.profile_lines()
        if not lines:
            return ""
        usage = self.profile_usage_chars()
        body = "\n".join(f"- {content}" for _, content in lines)
        return (
            f"## 关于用户（长期档案，{usage}/{PROFILE_MAX_CHARS} 字符，"
            f"用 profile_manage 工具维护，写满需先合并）\n{body}"
        )

    # ---------- review 日志（回合后自进化观测） ----------

    def save_review_log(self, session_id: int, outcome: str) -> None:
        self._conn.execute(
            "INSERT INTO reviews(session_id, outcome, created_at) VALUES (?, ?, ?)",
            (session_id, outcome, _now()),
        )
        self._conn.commit()

    def list_reviews(self, session_id: int, limit: int = 10) -> list[str]:
        rows = self._conn.execute(
            "SELECT outcome FROM reviews WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [str(r[0]) for r in rows]

    def close(self) -> None:
        self._conn.close()
