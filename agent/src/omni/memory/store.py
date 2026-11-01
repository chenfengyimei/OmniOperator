"""SQLite 记忆存储：会话 / 消息 / 事实 / 用户档案 / 全文检索 / review 日志。

设计取舍（P1）：
- 单文件 SQLite + WAL + busy_timeout，零外部服务，Windows/macOS 通用；
- Web 通道多线程访问同一连接：check_same_thread=False + 全公开方法一把
  可重入锁（ @_locked ），本地单用户场景足够（回顾线程用的是独立连接）；
- 全文检索用 FTS5 trigram tokenizer（对中文原生友好，2字以上可查）；
  FTS5 不可用时自动降级到 LIKE 三级链，功能不缺失只损失排序质量；
- FTS 索引由**触发器**维护，打开连接时不再 rebuild 全库（D11/A16）：
  以前每开一个连接（review 每次一轮、心跳每个周期）就重建一遍索引，数据量一大
  就成了启动风暴；现在只有"首次建表 / 分词器升级 / 旧库没有状态记录"才重建一次，
  状态写在 `meta` 表里；
- 用户档案限长（PROFILE_MAX_CHARS）：写满时返回错误+当前档案+剩余额度，
  逼调用方在当轮合并——这是 Hermes 验证过的"限额+报错=自整理"协议
  （xuexi/NOTES.md §1.3）；
- 工具调用中间过程不持久化，只落 user 与最终 assistant 文本。
"""
from __future__ import annotations

import contextlib
import datetime as dt
import functools
import json
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any

from ..config import DATA_DIR
from .clarity import (
    CLARITY_CLEAR,
    CLARITY_ORDER,
    CLARITY_VIVID,
    ClarityConfig,
    apply_daily_decay,
    clarity_index,
    normalize_clarity,
    review_effect,
)
from .store_schema import (
    CLARITY_META_KEY,
    DEFAULT_TITLE,
    FTS_META_KEY,
    FTS_TRIGGERS,
    FTS_VERSION,
    PROFILE_MAX_CHARS,
)
from .store_schema import (
    FTS_SCHEMA as _FTS_SCHEMA,
)
from .store_schema import (
    SCHEMA as _SCHEMA,
)

# 兼容旧私有名引用：声明本体已归 `store_schema.py`
_FTS_TRIGGERS = FTS_TRIGGERS



def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_text(text: str) -> str:
    """遗忘匹配用的归一化：去空白与标点、统一大小写。

    只做"同一句话的写法差异"层面的归一，不做语义近似——误删比漏删更难挽回，
    拿不准时宁可让用户再说一次"忘掉 X"。
    """
    return re.sub(r"[\s\W_]+", "", str(text or "").lower())


def _match_expr(tokens: list[str]) -> str:
    """FTS5 MATCH 表达式：每个 token 短语化后 OR 连接（防特殊字符破坏语法）。"""
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in tokens)


# 召回强化参数（借鉴 SeekMemory 的"命中强化 + 冷却"：防"越提越重要"的正反馈）。
# 强化只参与排序、不做入场券——检索链仍是唯一候选来源；增益上限
# RECALL_HIT_CAP × RECALL_HIT_BOOST = 1.5 个链位，最多让一条记忆前进一个
# 链位（超越一个零命中邻居），不会跳过两个。hits=0 时顺序与纯链序完全一致，
# 冻结检索评测不受影响。
RECALL_HIT_CAP = 10  # 排序时计入的最大命中次数
RECALL_HIT_BOOST = 0.15  # 每次命中的排序增益
TOUCH_COOLDOWN_MINUTES = 30  # 冷却窗口：窗口内重复命中不计数
RECALL_ROW_LIMIT_FACTOR = 2  # 重排取候选数 = limit × 此系数

# 清晰度降权（P5-a）：每低一档施加的链位惩罚，以及惩罚上限。
# 与 RECALL_HIT_BOOST 同量级——清晰度是一个**弱信号**，只用来在链序相近时
# 把陈旧记忆往后挪，不该把一条新记忆从第 1 位直接挤到第 20 位。
RECALL_CLARITY_STEP = 0.4  # 每降一档的惩罚
RECALL_CLARITY_CAP = 2.0  # 惩罚上限（最多后移 2 个链位）


def clarity_penalty(clarity: str | None) -> float:
    """清晰度 → 排序惩罚（链位数，越大越靠后）。

    `vivid`/`clear` 惩罚为 0（正常记忆不该被降权）；越模糊惩罚越大，
    但封顶在 `RECALL_CLARITY_CAP` 个链位——**陈旧不等于该被埋掉**。
    未知/脏值按 `clear` 处理（0 惩罚），宁可不高估陈旧度。
    """
    idx = clarity_index(clarity)
    if idx <= CLARITY_ORDER.index(CLARITY_CLEAR):
        return 0.0
    return min(RECALL_CLARITY_CAP, (idx - CLARITY_ORDER.index(CLARITY_CLEAR)) * RECALL_CLARITY_STEP)


def _fact_row(row: tuple) -> dict:
    """检索链统一行结构（`id`/`content`/`hits`/`clarity`）。

    单独一个函数是为了让三处查询（FTS、LIKE 链、空查询）产出的行**形状完全一致**——
    少一处漏选 `clarity`，降权就会在某个分支静默失效。
    """
    return {
        "id": int(row[0]),
        "content": str(row[1]),
        "hits": int(row[2] or 0),
        "clarity": normalize_clarity(row[3] if len(row) > 3 else None),
    }


def _locked(method):
    """公开存储方法统一加可重入锁（web 通道线程池会并发访问同一连接）。"""

    @functools.wraps(method)
    def wrapper(self: "MemoryStore", *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


class MemoryStore:
    def __init__(self, db_path: Path | None = None) -> None:
        path = db_path or (DATA_DIR / "memory.db")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._fts_triggers = False
        self._conn.executescript(_SCHEMA)
        self._migrate_columns()
        self._init_fts()
        self._conn.commit()

    def _migrate_columns(self) -> None:
        """旧库补列（D12）：记忆要带来源、作用域与失效标记。

        `memory.db` 从 D00 起就没有版本号，所以这里用 `PRAGMA table_info` 判断缺哪列，
        只加不改——旧数据保持原样，新列用默认值（`source='chat'`、`scope='private'`）。
        """
        additions = {
            "facts": {
                "updated_at": "TEXT",
                "source": "TEXT NOT NULL DEFAULT 'chat'",
                "scope": "TEXT NOT NULL DEFAULT 'private'",
                "session_id": "INTEGER",
                "superseded_by": "INTEGER",
                "hits": "INTEGER NOT NULL DEFAULT 0",
                "last_used": "TEXT",
                # 清晰度阶梯（P5-a）：新写入的事实直接是 vivid（刚说过的话最清楚）；
                # 旧库补列时统一给 clear —— 存量事实不会因为升级突然"变旧"，
                # 它们从 clear 起步，按天逐步降档。
                "clarity": "TEXT NOT NULL DEFAULT 'clear'",
                "clarity_changed_at": "TEXT",
                # 上次"被复习升档"的日期戳：升档每天最多一次（防反复召回把阶梯刷爆）。
                "clarity_reviewed_at": "TEXT",
            },
            "summaries": {"invalidated_at": "TEXT"},
        }
        for table, columns in additions.items():
            existing = {
                str(row[1]) for row in self._conn.execute(f"PRAGMA table_info({table})")
            }
            for name, ddl in columns.items():
                if name not in existing:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
        self._conn.commit()

    def _init_fts(self) -> None:
        """建/升级 FTS 索引，只在**真的需要**时重建全库（D11/A16）。

        判定顺序：
        1. 已有索引且分词器已是 trigram、`meta` 记录与本版本一致 → 什么都不做；
        2. 旧库（有索引但没状态记录）/ 分词器要升级 / 索引缺失 → 建一次并 `rebuild` 一次；
        3. FTS5 不可用 → `fts_enabled=False`，检索退到 LIKE 链（功能不缺失，只损失排序）。
        """
        self.fts_enabled = False
        self.fts_rebuilt = False
        self.fts_reason = ""
        self.fts_tokenizer = ""
        state = self._meta_json(FTS_META_KEY) or {}
        existing = self._fts_table_sql("messages_fts")
        current = "trigram" if "trigram" in (existing or "") else (
            "unicode61" if "unicode61" in (existing or "") else ""
        )
        fresh = not existing
        upgraded = bool(existing) and current != "trigram"
        legacy = bool(existing) and not state

        tokenizer = current or "trigram"
        created = False
        while tokenizer:
            try:
                if (fresh or upgraded) and existing:
                    # 分词器要换：旧表与新表不能同名共存，先删（下一步会重建并回填）
                    self._conn.execute("DROP TABLE IF EXISTS facts_fts")
                    self._conn.execute("DROP TABLE IF EXISTS messages_fts")
                    existing = ""
                self._conn.executescript(_FTS_SCHEMA.format(tokenize=tokenizer))
                created = True
                break
            except sqlite3.OperationalError:
                if tokenizer == "trigram":
                    tokenizer = "unicode61"
                    continue
                tokenizer = ""
        if not created:
            self.fts_reason = "no_fts5"  # 本机 SQLite 不带 FTS5：退到 LIKE
            return

        need_rebuild = fresh or upgraded or legacy or state.get("version") != FTS_VERSION
        if need_rebuild:
            try:
                self._conn.execute("INSERT INTO facts_fts(facts_fts) VALUES ('rebuild')")
                self._conn.execute("INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')")
                self.fts_rebuilt = True
                self.fts_reason = (
                    "fresh" if fresh else "tokenizer_upgrade" if upgraded else "legacy"
                )
            except sqlite3.OperationalError as exc:
                self.fts_reason = f"rebuild_failed:{exc}"
                return
        try:
            self._conn.executescript(_FTS_TRIGGERS)
            self._fts_triggers = True
        except sqlite3.OperationalError:
            self._fts_triggers = False  # 老 SQLite 建不了触发器：退回手写同步
        self._set_meta(
            FTS_META_KEY,
            {
                "version": FTS_VERSION,
                "tokenizer": tokenizer,
                "triggers": self._fts_triggers,
                "built_at": _now(),
            },
        )
        self.fts_enabled = True
        self.fts_tokenizer = tokenizer

    def _fts_table_sql(self, name: str) -> str:
        row = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone()
        return str(row[0] or "") if row else ""

    def _meta_json(self, key: str) -> dict:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        if not row:
            return {}
        try:
            import json

            value = json.loads(row[0])
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _set_meta(self, key: str, value: dict) -> None:
        import json

        self._conn.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value, ensure_ascii=False, sort_keys=True)),
        )
        self._conn.commit()

    # ---------- 会话 ----------

    @_locked
    def new_session(self, title: str = DEFAULT_TITLE) -> int:
        cur = self._conn.execute(
            "INSERT INTO sessions(created_at, title) VALUES (?, ?)", (_now(), title)
        )
        self._conn.commit()
        return int(cur.lastrowid)

    @_locked
    def latest_session_id(self) -> int | None:
        row = self._conn.execute(
            "SELECT id FROM sessions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return int(row[0]) if row else None

    @_locked
    def list_sessions(self, limit: int = 20) -> list[tuple[int, str, str]]:
        rows = self._conn.execute(
            "SELECT id, title, created_at FROM sessions ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [(int(r[0]), str(r[1]), str(r[2])) for r in rows]

    @_locked
    def session_exists(self, session_id: int) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return row is not None

    @_locked
    def rename_session(self, session_id: int, title: str) -> None:
        self._conn.execute(
            "UPDATE sessions SET title = ? WHERE id = ?", (title, session_id)
        )
        self._conn.commit()

    # ---------- 通道绑定（QQ 等外部用户 ↔ 本地会话） ----------

    @_locked
    def get_or_create_binding(self, channel: str, channel_user: str) -> int:
        """通道用户绑定到独立会话（首次自动创建，标题带通道前缀，工作台可见）。

        D06：跨进程用"写事务 + 主键唯一"完成，不再"先查后插"。旧写法两个进程同时
        第一次见到同一个 QQ 用户时，会各自建一个会话，其中一个绑定被覆盖成孤儿会话——
        表现为"这个人有两条互不相干的历史"。
        """
        key = (channel, str(channel_user))
        try:
            self._conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            raise RuntimeError(f"绑定会话失败（数据库忙）：{exc}") from exc
        try:
            row = self._conn.execute(
                "SELECT session_id FROM bindings WHERE channel = ? AND channel_user = ?",
                key,
            ).fetchone()
            if row is not None:
                self._conn.commit()
                return int(row[0])
            cur = self._conn.execute(
                "INSERT INTO sessions(created_at, title) VALUES (?, ?)",
                (_now(), f"{channel}:{key[1]}"),
            )
            session_id = int(cur.lastrowid)
            self._conn.execute(
                "INSERT INTO bindings(channel, channel_user, session_id) VALUES (?, ?, ?)",
                (*key, session_id),
            )
            self._conn.commit()
            return session_id
        except BaseException:
            with contextlib.suppress(sqlite3.Error):
                self._conn.rollback()
            raise

    @_locked
    def get_binding(self, channel: str, channel_user: str) -> int | None:
        row = self._conn.execute(
            "SELECT session_id FROM bindings WHERE channel = ? AND channel_user = ?",
            (channel, str(channel_user)),
        ).fetchone()
        return int(row[0]) if row else None

    @_locked
    def set_binding(self, channel: str, channel_user: str, session_id: int) -> None:
        """换绑（比如用户要求重开记忆）。"""
        self._conn.execute(
            "INSERT INTO bindings(channel, channel_user, session_id) VALUES (?, ?, ?) "
            "ON CONFLICT(channel, channel_user) DO UPDATE SET session_id = excluded.session_id",
            (channel, str(channel_user), session_id),
        )
        self._conn.commit()

    # ---------- 消息 ----------

    @_locked
    def append_message(
        self,
        session_id: int,
        role: str,
        content: str,
        *,
        auto_title: bool = True,
        prompt_context: dict | None = None,
    ) -> None:
        context_json = None
        if prompt_context is not None:
            if role != 'user':raise ValueError('只有用户回合可以附带上下文快照')
            context_json = json.dumps(prompt_context, ensure_ascii=False, sort_keys=True, allow_nan=False)
            if len(context_json) > 65536:raise ValueError('回合上下文快照超过 65536 字符上限')
        # 消息、上下文和自动标题同一事务，失败不能留下缺少快照的半个回合。
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO messages(session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (session_id, role, content, _now()),
            )
            if context_json is not None:
                self._conn.execute('INSERT INTO message_contexts(message_id,payload) VALUES (?,?)',
                                   (int(cur.lastrowid),context_json))
            if self.fts_enabled and not self._fts_triggers:
                self._conn.execute('INSERT INTO messages_fts(rowid) VALUES (?)',(int(cur.lastrowid),))
            if auto_title and role == 'user':
                self._conn.execute('UPDATE sessions SET title=? WHERE id=? AND title=?',
                                   (content[:24], session_id, DEFAULT_TITLE))

    @_locked
    def prompt_contexts(self, session_id: int, message_ids: list[int]) -> dict[int, dict]:
        """按会话读取不可变的回合快照，旧消息无快照时保持原样。"""
        result = {}
        unique = list(dict.fromkeys(message_ids))
        for offset in range(0,len(unique),500):
            identifiers = unique[offset:offset+500]
            placeholders = ','.join('?' for _ in identifiers)
            rows = self._conn.execute(
                'SELECT c.message_id,c.payload FROM message_contexts c JOIN messages m ON m.id=c.message_id '
                f'WHERE m.session_id=? AND c.message_id IN ({placeholders})',
                (session_id,*identifiers),
            )
            for message_id,raw in rows:
                if len(raw)>65536:raise ValueError('回合上下文快照超限')
                data=json.loads(raw)
                if not isinstance(data,dict):raise ValueError('回合上下文快照无效')
                result[int(message_id)]=data
        return result

    @_locked
    def recent_messages(self, session_id: int, limit: int) -> list[tuple[str, str]]:
        rows = self._conn.execute(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [(str(r[0]), str(r[1])) for r in reversed(rows)]

    @_locked
    def recent_messages_ex(self, session_id: int, limit: int) -> list[tuple[int, str, str]]:
        """带消息 id 的历史（D11）：摘要要按**消息区间**记覆盖范围，没 id 就对不上账。"""
        rows = self._conn.execute(
            "SELECT id, role, content FROM messages WHERE session_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [(int(r[0]), str(r[1]), str(r[2])) for r in reversed(rows)]

    @_locked
    def message_count(self, session_id: int) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,)
        ).fetchone()
        return int(row[0]) if row else 0

    # ---------- 回合后 review 的去重游标（D12） ----------

    @_locked
    def max_message_id(self, session_id: int) -> int | None:
        row = self._conn.execute(
            "SELECT MAX(id) FROM messages WHERE session_id = ?", (session_id,)
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    @_locked
    def claim_review(self, session_id: int, up_to_msg_id: int) -> bool:
        """认领"整理到这条消息为止"。`UNIQUE` 让并发/多进程只有一个成功。"""
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO review_runs(session_id, up_to_msg_id, outcome, created_at)"
            " VALUES (?, ?, '', ?)",
            (session_id, int(up_to_msg_id), _now()),
        )
        self._conn.commit()
        return cur.rowcount == 1

    @_locked
    def finish_review(
        self, session_id: int, up_to_msg_id: int, outcome: str, *, keep: bool = True
    ) -> None:
        """写结果；`keep=False`（失败）时撤销认领，让这段下次还能重试。"""
        if keep:
            self._conn.execute(
                "UPDATE review_runs SET outcome = ? WHERE session_id = ? AND up_to_msg_id = ?",
                (outcome[:500], session_id, int(up_to_msg_id)),
            )
        else:
            self._conn.execute(
                "DELETE FROM review_runs WHERE session_id = ? AND up_to_msg_id = ?",
                (session_id, int(up_to_msg_id)),
            )
        self._conn.commit()

    @_locked
    def last_reviewed(self, session_id: int) -> int | None:
        row = self._conn.execute(
            "SELECT MAX(up_to_msg_id) FROM review_runs WHERE session_id = ?", (session_id,)
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    @_locked
    def review_history(self, session_id: int, limit: int = 10) -> list[dict]:
        rows = self._conn.execute(
            "SELECT up_to_msg_id, outcome, created_at FROM review_runs"
            " WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, max(1, limit)),
        ).fetchall()
        return [
            {"up_to_msg_id": int(r[0]), "outcome": str(r[1]), "created_at": str(r[2])}
            for r in rows
        ]

    # ---------- 摘要（D11：持久上下文） ----------
    @_locked
    def active_summary(self, session_id: int) -> tuple[int, int, int, str, int] | None:
        """当前有效的摘要：`(id, from_msg_id, to_msg_id, content, version)`。

        只认**没有被取代**的最近一条：被取代的摘要留着做审计，不再进上下文。
        """
        row = self._conn.execute(
            "SELECT id, from_msg_id, to_msg_id, content, version FROM summaries "
            "WHERE session_id = ? AND superseded_by IS NULL AND invalidated_at IS NULL"
            " ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return (int(row[0]), int(row[1]), int(row[2]), str(row[3]), int(row[4]))

    @_locked
    def save_summary(
        self,
        session_id: int,
        *,
        from_msg_id: int,
        to_msg_id: int,
        content: str,
        source: str = "compress",
        model: str = "",
        supersedes: int | None = None,
    ) -> tuple[int, int, bool]:
        """写一条摘要，返回 `(id, version, 是否新建)`。

        幂等：同一 `(session_id, to_msg_id)` 只会有一条——重复压缩同一段就返回原记录，
        不会因为重试把摘要堆成好几份（也保证版本号不会乱跳）。
        """
        existing = self._conn.execute(
            "SELECT id, version FROM summaries WHERE session_id = ? AND to_msg_id = ?",
            (session_id, to_msg_id),
        ).fetchone()
        if existing is not None:
            return int(existing[0]), int(existing[1]), False
        version = 1
        if supersedes is not None:
            row = self._conn.execute(
                "SELECT version FROM summaries WHERE id = ?", (supersedes,)
            ).fetchone()
            if row is not None:
                version = int(row[0]) + 1
        cur = self._conn.execute(
            "INSERT INTO summaries(session_id, from_msg_id, to_msg_id, content, source,"
            " model, version, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                session_id,
                int(from_msg_id),
                int(to_msg_id),
                content,
                source,
                model,
                version,
                _now(),
            ),
        )
        new_id = int(cur.lastrowid)
        if supersedes is not None:
            self._conn.execute(
                "UPDATE summaries SET superseded_by = ? WHERE id = ? AND superseded_by IS NULL",
                (new_id, int(supersedes)),
            )
        self._conn.commit()
        return new_id, version, True

    @_locked
    def summary_history(self, session_id: int, limit: int = 20) -> list[dict]:
        """摘要版本链（工作台/排查用）：谁覆盖了哪段消息、被谁取代。"""
        rows = self._conn.execute(
            "SELECT id, from_msg_id, to_msg_id, version, source, model, created_at,"
            " superseded_by, length(content) FROM summaries WHERE session_id = ?"
            " ORDER BY id DESC LIMIT ?",
            (session_id, max(1, limit)),
        ).fetchall()
        return [
            {
                "id": int(r[0]),
                "from_msg_id": int(r[1]),
                "to_msg_id": int(r[2]),
                "version": int(r[3]),
                "source": str(r[4]),
                "model": str(r[5]),
                "created_at": str(r[6]),
                "superseded_by": int(r[7]) if r[7] is not None else None,
                "chars": int(r[8] or 0),
            }
            for r in rows
        ]

    @_locked
    def list_messages(
        self, session_id: int, limit: int = 500
    ) -> list[tuple[str, str, str]]:
        """工作台展示用：按时间正序返回 (role, content, created_at)。"""
        rows = self._conn.execute(
            "SELECT role, content, created_at FROM messages WHERE session_id = ? "
            "ORDER BY id ASC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [(str(r[0]), str(r[1]), str(r[2])) for r in rows]

    @_locked
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

    @_locked
    def save_fact(
        self,
        content: str,
        *,
        source: str = "chat",
        scope: str = "private",
        session_id: int | None = None,
    ) -> int | None:
        """写入一条事实。`source` 记来源（chat/review/manual/import），`scope` 定可见域。

        返回 `None` 表示**被遗忘规则拒绝**（用户说过不要记）：这条检查放在存储层，
        任何入口都绕不过去，不依赖调用方记不记得检查。
        """
        text = content.strip()
        if self.is_forgotten(text):
            return None
        cur = self._conn.execute(
            "INSERT INTO facts(content, created_at, updated_at, source, scope, session_id,"
            " clarity, clarity_changed_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            # 新写入的事实是 `vivid`：刚说出口的话最清楚。
            # （旧库补列走默认值 clear，存量事实从 clear 起步，不会因升级突然变旧。）
            (text, _now(), _now(), source, scope, session_id, CLARITY_VIVID, _now()),
        )
        if self.fts_enabled and not self._fts_triggers:
            self._conn.execute(
                "INSERT INTO facts_fts(rowid) VALUES (?)", (int(cur.lastrowid),)
            )
        self._conn.commit()
        return int(cur.lastrowid)

    @_locked
    def is_forgotten(self, content: str) -> bool:
        """用户要求"不要记"的内容：写入前的确定性检查（不靠模型自觉）。"""
        key = normalize_text(content)
        if not key:
            return False
        row = self._conn.execute(
            "SELECT 1 FROM tombstones WHERE normalized = ? LIMIT 1", (key,)
        ).fetchone()
        return row is not None

    @_locked
    def forget_fact(
        self, fact_id: int, *, reason: str = "用户要求删除", session_id: int | None = None
    ) -> bool:
        """删除一条事实并留下墓碑：review 不能再把它学回来。

        同时把"包含这段文字的摘要"标为失效——否则删了事实、摘要里还留着，
        下一轮照样被注入，等于没删。
        """
        row = self._conn.execute(
            "SELECT content FROM facts WHERE id = ?", (fact_id,)
        ).fetchone()
        if row is None:
            return False
        content = str(row[0])
        self._conn.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
        self._conn.execute(
            "INSERT INTO tombstones(normalized, reason, session_id, created_at)"
            " VALUES (?, ?, ?, ?)",
            (normalize_text(content), reason, session_id, _now()),
        )
        self._invalidate_summaries(content, session_id)
        self._conn.commit()
        return True

    def _invalidate_summaries(self, content: str, session_id: int | None) -> int:
        """把提到这段文字的摘要标失效（派生数据要跟着原文一起失效）。"""
        needle = content.strip()
        if not needle:
            return 0
        rows = self._conn.execute(
            "SELECT id, content FROM summaries WHERE invalidated_at IS NULL"
            + (" AND session_id = ?" if session_id is not None else ""),
            (session_id,) if session_id is not None else (),
        ).fetchall()
        hit = [int(r[0]) for r in rows if needle[:24] in str(r[1])]
        for summary_id in hit:
            self._conn.execute(
                "UPDATE summaries SET invalidated_at = ? WHERE id = ?",
                (_now(), summary_id),
            )
        self._conn.commit()
        return len(hit)

    @_locked
    def search_facts(
        self, query: str, limit: int = 5, *, scope: str | None = None
    ) -> list[str]:
        """多级降级检索：FTS5（bm25 排序）→ 全词 AND → 全词 OR → 中文 2-gram 兜底。

        `scope` 非空时**先按作用域过滤**：群聊场景只能看到群作用域的事实，
        私人事实连"命中"都不该发生（不是取回来再隐藏）。
        """
        return [row["content"] for row in self._search_fact_rows(query, limit, scope=scope)]

    @_locked
    def search_fact_rows(
        self, query: str, limit: int = 5, *, scope: str | None = None
    ) -> list[dict]:
        """search_facts 的行版：同一套降级链，但返回带 id 的行（供工作台逐条删除）。

        与 search_facts **共用同一个实现**（见 _search_fact_rows），因此返回顺序
        必然一致——前端按 id 删除时不会出现「删错条」的风险。
        """
        return self._search_fact_rows(query, limit, scope=scope)

    def _search_fact_rows(
        self, query: str, limit: int, *, scope: str | None = None
    ) -> list[dict]:
        """检索链的唯一实现：FTS5 → 全词 AND → 全词 OR → 中文 2-gram，末尾强化重排。"""
        tokens = [t for t in query.split() if t]
        where_scope = "" if scope is None else " AND scope = ?"
        scope_params: list[Any] = [] if scope is None else [scope]
        rows: list[dict]
        if not tokens:  # 空查询：返回最近的记忆
            rows = self._conn.execute(
                "SELECT id, content, hits, clarity FROM facts WHERE 1=1"
                f"{where_scope} ORDER BY id DESC LIMIT ?",
                [*scope_params, limit * RECALL_ROW_LIMIT_FACTOR],
            ).fetchall()
            return self._rank_fact_rows(
                [_fact_row(r) for r in rows],
                limit,
            )
        if self.fts_enabled:
            try:
                rows = self._conn.execute(
                    "SELECT f.id, f.content, f.hits, f.clarity FROM facts_fts "
                    f"JOIN facts f ON f.id = facts_fts.rowid "
                    f"WHERE facts_fts MATCH ?{where_scope.replace('scope', 'f.scope')} "
                    "ORDER BY bm25(facts_fts) LIMIT ?",
                    (_match_expr(tokens), *scope_params, limit * RECALL_ROW_LIMIT_FACTOR),
                ).fetchall()
                if rows:
                    return self._rank_fact_rows(
                        [_fact_row(r) for r in rows],
                        limit,
                    )
            except sqlite3.OperationalError:
                pass  # 查询过短等场景，走 LIKE 链
        and_where = " AND ".join("content LIKE ?" for _ in tokens)
        rows = self._fetch_fact_rows(and_where, [f"%{t}%" for t in tokens], limit * RECALL_ROW_LIMIT_FACTOR, where_scope, scope_params)
        if rows:
            return self._rank_fact_rows(rows, limit)
        or_where = " OR ".join("content LIKE ?" for _ in tokens)
        rows = self._fetch_fact_rows(or_where, [f"%{t}%" for t in tokens], limit * RECALL_ROW_LIMIT_FACTOR, where_scope, scope_params)
        if rows:
            return self._rank_fact_rows(rows, limit)
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
            rows = self._fetch_fact_rows(where, [f"%{b}%" for b in bigrams], limit * RECALL_ROW_LIMIT_FACTOR, where_scope, scope_params)
            if rows:
                return self._rank_fact_rows(rows, limit)
        return []

    def _rank_fact_rows(self, rows: list[dict], limit: int) -> list[dict]:
        """链序 + 有界强化重排：强化最多让一条记忆前进一个链位。

        检索链（FTS bm25 / LIKE）是唯一的"入场券"和主导排序；hits 只在近端
        平局起作用（增益上限 1.5 个链位，只能超越一个零命中邻居）。hits=0 时
        排序与纯链序完全一致——冻结检索评测与工作台删除契约
        （search_fact_rows == search_facts 顺序）都不受影响。

        **清晰度降权**（P5-a）同理有界：清晰度每低一档施加一个固定的小额惩罚，
        惩罚上限 `RECALL_CLARITY_CAP` 个链位。与 hits 的关系是**相加**：
        命中强化（正）与清晰度降权（负）互相抵消，"最近被用过的旧记忆"不会
        因为历史 hits 高就压过"刚写下的新记忆"。

        **全部记忆清晰度相同时惩罚恒为 0**，排序与纯链序逐字节一致——
        冻结检索评测（`tests/fixtures/retrieval_v1.json`）里所有样本同批写入、
        清晰度相同，因此评测数字不受本改动影响。
        """
        keyed = [
            (
                pos
                - min(int(row.get("hits") or 0), RECALL_HIT_CAP) * RECALL_HIT_BOOST
                + clarity_penalty(row.get("clarity")),
                pos,
                row,
            )
            for pos, row in enumerate(rows)
        ]
        keyed.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in keyed[: max(1, limit)]]

    def _fetch_fact_rows(
        self,
        where: str,
        params: list[str],
        limit: int,
        where_scope: str = "",
        scope_params: list[Any] | None = None,
    ) -> list[dict]:
        rows = self._conn.execute(  # 模板由 token 数生成，参数全部绑定
            f"SELECT id, content, hits, clarity FROM facts WHERE ({where}){where_scope} ORDER BY id DESC LIMIT ?",
            [*params, *(scope_params or []), limit],
        ).fetchall()
        return [_fact_row(r) for r in rows]

    @_locked
    def fact_rows(self, limit: int = 200) -> list[dict]:
        """工作台展示用：带来源/作用域/时间，让人能判断"这条是哪来的"。"""
        rows = self._conn.execute(
            "SELECT id, content, source, scope, session_id, created_at, hits, last_used,"
            " clarity FROM facts ORDER BY id DESC LIMIT ?",
            (max(1, limit),),
        ).fetchall()
        return [
            {
                "id": int(r[0]),
                "content": str(r[1]),
                "source": str(r[2]),
                "scope": str(r[3]),
                "session_id": int(r[4]) if r[4] is not None else None,
                "created_at": str(r[5]),
                "hits": int(r[6] or 0),
                "last_used": str(r[7]) if r[7] is not None else None,
                "clarity": normalize_clarity(r[8] if len(r) > 8 else None),
            }
            for r in rows
        ]

    @_locked
    def filter_facts(self, candidates: list[str], *, scope: str) -> list[str]:
        """派生检索结果回源校验：网络期间删除/纠错的事实不返回。"""
        if not candidates:
            return []
        marks = ",".join("?" for _ in candidates)
        found = {r[0] for r in self._conn.execute(
            f"SELECT content FROM facts WHERE scope=? AND content IN ({marks})", [scope, *candidates])}
        return [text for text in candidates if text in found]

    @_locked
    def touch_facts(
        self,
        contents: list[str],
        *,
        scope: str,
        cooldown_minutes: int = TOUCH_COOLDOWN_MINUTES,
    ) -> int:
        """召回命中计数：30 分钟冷却 + 每天最多一次（借鉴 SeekMemory 的防正反馈设计）。

        原子条件更新：只有跨过冷却**且**今天还没计过数的行才 `hits+1` 并刷新
        `last_used`——没跨过就不动行，`rowcount` 之和即本次真正计数的条数。
        不更新未计数的行，否则"没计数却刷新了时间"会把当天第一次真正该得的
        计数也堵掉。时间比较用 ISO 字符串的字典序；跨时区偏移变化（如 DST）
        的边界误差对启发式排序信号可接受。

        **复习效应**：被召回的记忆同时升一档清晰度（`review_effect`）。
        清晰度阶梯是单向衰减的，只有"复习即回升"这条正反馈才能避免
        "用得越多掉得越狠"。升档与计数在同一事务里，不额外开一条写路径。
        """
        if not contents:
            return 0
        now = dt.datetime.now().astimezone()
        cutoff = (now - dt.timedelta(minutes=cooldown_minutes)).isoformat(timespec="seconds")
        today = now.date().isoformat()
        counted = 0
        for text in dict.fromkeys(contents):
            cur = self._conn.execute(
                "UPDATE facts SET hits = hits + 1, last_used = ? "
                "WHERE scope = ? AND content = ? "
                "AND (last_used IS NULL OR (last_used < ? AND substr(last_used, 1, 10) <> ?))",
                (_now(), scope, text, cutoff, today),
            )
            counted += max(0, cur.rowcount)
        self._conn.commit()
        return counted

    @_locked
    def review_facts_clarity(self, contents: list[str], *, scope: str) -> int:
        """复习升档：被召回的内容清晰度升一格（最多到 vivid），**每天最多一次**。

        与 `touch_facts` **分开**是有意的：`touch_facts` 有 30 分钟冷却，
        而升档不受冷却限制——一条 31 分钟后再次被想起的记忆也应该变清楚，
        不该因为冷却窗口错过回升。返回值是实际升档的行数。

        **每天最多一次**：一轮对话里模型可能连着 recall 同一批事实多次，
        若每次都升一格，一条 `forgotten` 记忆会在几分钟内被拉回 `vivid`——
        阶梯就成了"刷出来的"，失去意义。用 `clarity_reviewed_at` 记当天戳，
        当天再召回不再升档（与衰减的每日闸门对称：一天最多降一格、最多升一格）。
        """
        if not contents:
            return 0
        today = dt.datetime.now().astimezone().date().isoformat()
        changed = 0
        for text in dict.fromkeys(contents):
            row = self._conn.execute(
                "SELECT id, clarity, clarity_reviewed_at FROM facts"
                " WHERE scope = ? AND content = ? LIMIT 1",
                (scope, text),
            ).fetchone()
            if row is None:
                continue
            reviewed_at = str(row[2] or "")
            if reviewed_at[:10] == today:
                continue  # 今天已经升过档了
            before = normalize_clarity(row[1])
            after = review_effect(before, now=_now())
            if after == before:
                continue
            self._conn.execute(
                "UPDATE facts SET clarity = ?, clarity_changed_at = ?, clarity_reviewed_at = ?"
                " WHERE id = ?",
                (after, _now(), _now(), int(row[0])),
            )
            changed += 1
        self._conn.commit()
        return changed

    @_locked
    def decay_facts_clarity(
        self,
        *,
        today: dt.date | None = None,
        config: ClarityConfig | None = None,
        force: bool = False,
    ) -> dict:
        """每日清晰度衰减：每条最多降一格，返回统计。

        - **一天只跑一次**：`meta.clarity_decay.day` 记着上次衰减的日期，
          当天再调直接返回（`changed=0`）。整理循环每 5 分钟一轮，
          没有这道闸门，"每天最多降一格"会退化成"每 5 分钟降一格"——
          抄 surfcore `memory-card.js:1565` 的 `lastDecayDate === today` 判断。
          `force=True` 用于手工补跑（测试与运维），绕过闸门。
        - 只降不升：升档只由复习（`review_facts_clarity`）触发；
        - **不删除任何行**：`forgotten` 只是不再主动召回；
        - 逐条判定（纯函数 `apply_daily_decay`），因此结果可完全预测与复算。
        """
        cfg = config or ClarityConfig()
        day = today or dt.datetime.now().astimezone().date()
        day_key = day.isoformat()
        state = self._meta_json(CLARITY_META_KEY) or {}
        if not force and str(state.get("day") or "") == day_key:
            # 今天已经衰减过：直接返回（只跑一次聚合查询看分布，不搬整表）。
            counts = {
                str(row[0]): int(row[1])
                for row in self._conn.execute(
                    "SELECT clarity, COUNT(*) FROM facts GROUP BY clarity"
                ).fetchall()
            }
            return {
                "day": day_key,
                "changed": 0,
                "total": sum(counts.values()),
                "by_clarity": counts,
                "skipped": True,
            }
        rows = self._conn.execute(
            "SELECT id, content, created_at, last_used, clarity FROM facts"
        ).fetchall()
        changed = 0
        counts = {}
        for row in rows:
            before = normalize_clarity(row[4])
            after = apply_daily_decay(
                before,
                created_at=str(row[2] or ""),
                last_used=str(row[3] or ""),
                today=day,
                config=cfg,
            )
            counts[after] = counts.get(after, 0) + 1
            if after == before:
                continue
            self._conn.execute(
                "UPDATE facts SET clarity = ?, clarity_changed_at = ? WHERE id = ?",
                (after, _now(), int(row[0])),
            )
            changed += 1
        self._conn.commit()
        self._set_meta(CLARITY_META_KEY, {"day": day_key, "at": _now(), "changed": changed})
        return {
            "day": day_key,
            "changed": changed,
            "total": len(rows),
            "by_clarity": counts,
            "skipped": False,
        }

    @_locked
    def fact_ids_by_contents(self, contents: list[str], *, scope: str) -> dict[str, int]:
        """按内容**精确**反查 id（forget 删除用）：SQL IN 直查全库，不看条数。

        旧实现借 `fact_rows(500)`（工作台展示口径，按新到旧截 500 条）做反查——
        记忆超过 500 条后删旧事实会静默失败（"已删除 0 条"），哪怕检索明明找到了它。
        """
        if not contents:
            return {}
        marks = ",".join("?" for _ in contents)
        rows = self._conn.execute(
            f"SELECT content, id FROM facts WHERE scope = ? AND content IN ({marks})",
            [scope, *contents],
        ).fetchall()
        return {str(r[0]): int(r[1]) for r in rows}

    @_locked
    def fact_timestamps(self, contents: list[str], *, scope: str) -> dict[str, str]:
        """按内容回查 `created_at`（recall 输出相对时间用）；不在库的内容无键。"""
        if not contents:
            return {}
        marks = ",".join("?" for _ in contents)
        rows = self._conn.execute(
            f"SELECT content, created_at FROM facts WHERE scope = ? AND content IN ({marks})",
            [scope, *contents],
        ).fetchall()
        return {str(r[0]): str(r[1]) for r in rows}

    @_locked
    def fact_clarities(self, contents: list[str], *, scope: str) -> dict[str, str]:
        """按内容回查清晰度档位（recall 标注陈旧程度用）；不在库的内容无键。"""
        if not contents:
            return {}
        marks = ",".join("?" for _ in contents)
        rows = self._conn.execute(
            f"SELECT content, clarity FROM facts WHERE scope = ? AND content IN ({marks})",
            [scope, *contents],
        ).fetchall()
        return {str(r[0]): normalize_clarity(r[1]) for r in rows}

    @_locked
    def forgotten_matches(self, query: str, limit: int = 1) -> list[str]:
        """元记忆线索：查询词与哪条遗忘墓碑同话题（LIKE 链，墓碑不建 FTS）。

        只回答"有没有被要求忘过的相关内容"，不展示墓碑原文——墓碑是"不要
        再记"的标记，详细展示等于变相泄露。中文整句查询靠 2-gram 兜底命中。
        """
        tokens = [t for t in query.split() if t]
        if not tokens:
            return []

        def _like(needles: list[str]) -> list[str]:
            where = " OR ".join("normalized LIKE ?" for _ in needles)
            rows = self._conn.execute(
                f"SELECT normalized FROM tombstones WHERE {where} ORDER BY id DESC LIMIT ?",
                [*(f"%{n}%" for n in needles), max(1, limit)],
            ).fetchall()
            return [str(r[0]) for r in rows]

        hits = _like(tokens)
        if hits:
            return hits
        bigrams = sorted(
            {
                t[i : i + 2]
                for t in tokens
                for i in range(len(t) - 1)
                if _has_cjk(t[i : i + 2])
            }
        )
        return _like(bigrams) if bigrams else []

    @_locked
    def facts_count(self) -> int:
        """事实总数（记忆系统总览用）。"""
        row = self._conn.execute("SELECT COUNT(*) FROM facts").fetchone()
        return int(row[0])

    @_locked
    def reviews_count(self) -> int:
        """复盘记录总数（记忆系统总览用）。"""
        row = self._conn.execute("SELECT COUNT(*) FROM reviews").fetchone()
        return int(row[0])

    # ---------- 用户档案（USER.md 式，限额+报错驱动自整理） ----------
    # 数据层统一后此表只读冻结：档案已迁往 owner 人物的 archive 条目（memory/owner.py），
    # 这组方法仅剩直接构造 MemoryStore 的老测试与迁移回滚路径使用，生产接线已全部换源。

    @_locked
    def profile_lines(self) -> list[tuple[int, str]]:
        rows = self._conn.execute(
            "SELECT id, content FROM profile ORDER BY id"
        ).fetchall()
        return [(int(r[0]), str(r[1])) for r in rows]

    @_locked
    def profile_usage_chars(self) -> int:
        return sum(len(content) for _, content in self.profile_lines())

    @_locked
    def profile_add(self, content: str, *, source: str = "manual") -> tuple[bool, str]:
        """超限时返回 (False, 错误+当前档案+剩余额度)，逼调用方当轮合并。

        source 形参只为与 OwnerMemory 门面同形（冻结表不记来源），保持忽略。
        """
        content = content.strip()
        if not content:
            return False, "错误：档案内容不能为空"
        usage = self.profile_usage_chars()
        if usage + len(content) > PROFILE_MAX_CHARS:
            current = "\n".join(f"[{lid}] {text}" for lid, text in self.profile_lines())
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

    @_locked
    def profile_edit(self, line_id: int, content: str) -> bool:
        return self.profile_edit_checked(line_id, content)[0]

    @_locked
    def profile_edit_checked(self, line_id: int, content: str) -> tuple[bool, str]:
        """改一行档案。**必须重算总额度**（A15）：以前 edit 不检查长度，
        一句"把档案改成……"就能把限额顶穿，add 的限额形同虚设。"""
        text = content.strip()
        if not text:
            return False, "错误：档案内容不能为空"
        row = self._conn.execute(
            "SELECT content FROM profile WHERE id = ?", (line_id,)
        ).fetchone()
        if row is None:
            return False, f"错误：不存在第 {line_id} 条（先 view 查看编号）"
        usage = self.profile_usage_chars()
        after = usage - len(str(row[0])) + len(text)
        if after > PROFILE_MAX_CHARS:
            return False, (
                f"档案额度不够：改完后 {after}/{PROFILE_MAX_CHARS} 字符。"
                "请先合并或删减其它条目再改。"
            )
        self._conn.execute(
            "UPDATE profile SET content = ?, updated_at = ? WHERE id = ?",
            (text, _now(), line_id),
        )
        self._conn.commit()
        return True, f"已更新（{after}/{PROFILE_MAX_CHARS} 字符）。"

    @_locked
    def profile_delete(self, line_id: int) -> bool:
        cur = self._conn.execute("DELETE FROM profile WHERE id = ?", (line_id,))
        self._conn.commit()
        return cur.rowcount > 0

    @_locked
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

    @_locked
    def save_review_log(self, session_id: int, outcome: str) -> None:
        self._conn.execute(
            "INSERT INTO reviews(session_id, outcome, created_at) VALUES (?, ?, ?)",
            (session_id, outcome, _now()),
        )
        self._conn.commit()

    @_locked
    def list_reviews(self, session_id: int, limit: int = 10) -> list[str]:
        rows = self._conn.execute(
            "SELECT outcome FROM reviews WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [str(r[0]) for r in rows]

    @_locked
    def list_all_reviews(self, limit: int = 20) -> list[tuple[int, str, str]]:
        """工作台展示用：跨会话最近 review 记录，新的在前。"""
        rows = self._conn.execute(
            "SELECT session_id, outcome, created_at FROM reviews ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [(int(r[0]), str(r[1]), str(r[2])) for r in rows]

    # ---------- 夜间整理（批量 review 的素材源） ----------

    @_locked
    def digest_since(self, since_iso: str) -> dict:
        """自某时刻以来的活动统计：活跃会话/新记忆/最近 review/档案额度。

        created_at 统一由 _now() 生成（带时区偏移的 ISO），同格式字符串
        直接字典序比较即时间序。夜间整理（daily_digest 工具）以这份
        统计为素材决定合并/沉淀什么。
        """
        counts = {
            int(r[0]): int(r[1])
            for r in self._conn.execute(
                "SELECT session_id, COUNT(*) FROM messages WHERE created_at >= ? "
                "GROUP BY session_id",
                (since_iso,),
            ).fetchall()
        }
        sessions = [
            {"id": sid, "title": title, "messages": counts[sid]}
            for sid, title, _created in self.list_sessions(limit=50)
            if sid in counts
        ]
        facts_new = int(
            self._conn.execute(
                "SELECT COUNT(*) FROM facts WHERE created_at >= ?", (since_iso,)
            ).fetchone()[0]
        )
        facts_recent = [
            str(r[0])
            for r in self._conn.execute(
                "SELECT content FROM facts WHERE created_at >= ? "
                "ORDER BY id DESC LIMIT 20",
                (since_iso,),
            ).fetchall()
        ]
        reviews_recent = [
            str(r[0])
            for r in self._conn.execute(
                "SELECT outcome FROM reviews WHERE created_at >= ? "
                "ORDER BY id DESC LIMIT 5",
                (since_iso,),
            ).fetchall()
        ]
        return {
            "since": since_iso,
            "sessions": sessions,
            "facts_new": facts_new,
            "facts_recent": facts_recent,
            "reviews_recent": reviews_recent,
            "profile_chars": self.profile_usage_chars(),
        }

    # ---------- 待办清单（todo 内联工具：agent 自维护多步编排） ----------

    @_locked
    def todo_add(self, session_id: int, content: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(pos), 0) FROM todos WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        cur = self._conn.execute(
            "INSERT INTO todos(session_id, pos, content) VALUES (?, ?, ?)",
            (session_id, int(row[0]) + 1, content),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    @_locked
    def todo_list(self, session_id: int) -> list[tuple[int, str, int]]:
        rows = self._conn.execute(
            "SELECT id, content, done FROM todos WHERE session_id = ? ORDER BY pos, id",
            (session_id,),
        ).fetchall()
        return [(int(r[0]), str(r[1]), int(r[2])) for r in rows]

    @_locked
    def todo_set_done(self, session_id: int, todo_id: int, done: bool = True) -> bool:
        cur = self._conn.execute(
            "UPDATE todos SET done = ? WHERE id = ? AND session_id = ?",
            (1 if done else 0, todo_id, session_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    @_locked
    def todo_delete(self, session_id: int, todo_id: int) -> bool:
        cur = self._conn.execute(
            "DELETE FROM todos WHERE id = ? AND session_id = ?", (todo_id, session_id)
        )
        self._conn.commit()
        return cur.rowcount > 0

    @_locked
    def todo_clear(self, session_id: int, *, done_only: bool = False) -> int:
        sql = "DELETE FROM todos WHERE session_id = ?"
        params: list = [session_id]
        if done_only:
            sql += " AND done = 1"
        cur = self._conn.execute(sql, params)
        self._conn.commit()
        return int(cur.rowcount)

    @_locked
    def close(self) -> None:
        self._conn.close()
