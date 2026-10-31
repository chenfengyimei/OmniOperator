"""memory.db 的表结构、FTS 索引声明与额度常量（唯一来源）。

只承载数据形状：DDL 字符串、FTS 触发器、检索版本号与档案限额。
连接、迁移执行、FTS 重建与全部仓储行为在 `memory/store.py::MemoryStore`。

约束：

- DDL 变更只能**追加**（旧库用 `PRAGMA table_info` 补列，见 store.py 的
  `_migrate_columns`）；memory.db 没有 schema_version 表（与 state.db 的
  版本化迁移不同），改列前先读 `store.py` 的迁移注释；
- `_SCHEMA` 里每张表的语义注释写在 `store.py` 模块头与 DATA_MODEL.md；
- `PROFILE_MAX_CHARS` 是跨模块公开契约（web_api / guard / tools 消费），
  改动前先核对「限额+报错=自整理」协议（xuexi/NOTES.md §1.3）。
"""
from __future__ import annotations

SCHEMA = """
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
CREATE TABLE IF NOT EXISTS message_contexts (
    message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
    payload TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS message_contexts_ad AFTER DELETE ON messages BEGIN
    DELETE FROM message_contexts WHERE message_id = old.id;
END;
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    source TEXT NOT NULL DEFAULT 'chat',
    scope TEXT NOT NULL DEFAULT 'private',
    session_id INTEGER,
    superseded_by INTEGER
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
CREATE TABLE IF NOT EXISTS bindings (
    channel TEXT NOT NULL,
    channel_user TEXT NOT NULL,
    session_id INTEGER NOT NULL,
    PRIMARY KEY (channel, channel_user)
);
CREATE TABLE IF NOT EXISTS todos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    pos INTEGER NOT NULL,
    content TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    from_msg_id INTEGER NOT NULL,
    to_msg_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'compress',
    model TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    superseded_by INTEGER,
    UNIQUE(session_id, to_msg_id)
);
CREATE INDEX IF NOT EXISTS idx_summaries_session ON summaries(session_id, id);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tombstones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    normalized TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    session_id INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tombstones_norm ON tombstones(normalized);
CREATE TABLE IF NOT EXISTS review_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    up_to_msg_id INTEGER NOT NULL,
    outcome TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(session_id, up_to_msg_id)
);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    content, content='facts', content_rowid='id', tokenize='{tokenize}'
);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content, content='messages', content_rowid='id', tokenize='{tokenize}'
);
"""

# 索引由触发器维护（D11）：外部内容表的增删改都必须同步发给索引，
# 否则"原文进库了、搜不到"这种偏差只能靠全量 rebuild 掩盖。
FTS_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO facts_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
"""

FTS_META_KEY = "fts"
FTS_VERSION = 2

PROFILE_MAX_CHARS = 2000

DEFAULT_TITLE = "新会话"

# 旧私有名兼容：store.py 历史上用这些名字引用声明本体
_SCHEMA = SCHEMA
_FTS_SCHEMA = FTS_SCHEMA
_FTS_TRIGGERS = FTS_TRIGGERS
