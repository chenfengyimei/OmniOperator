"""`state.db` 的表结构与迁移声明唯一来源。

只声明数据形状，不做连接、迁移执行、仓储行为或进程管理；运行入口仍是
`memory/state.py::StateStore`。把 schema 从仓储实现里拆出来的原因：

1. `state.py` 同时承担连接、事务、迁移、任务、租约、额度、投递、MCP 指纹等多个职责，
   schema 变更与仓储行为混在一起，评审很难确认"这次改的是数据形状还是运行逻辑"；
2. `command_jobs` 的表结构此前由独立模块注入迁移表，同一数据库的 DDL 有两处来源，
   后续统一数据结构演进时容易漏改。

约束：

- 迁移 SQL 保持逐版本追加，已发布版本不允许改写；新结构一律新增版本号；
- 迁移脚本不要在字符串字面量中出现分号（`state.py::_statements` 按分号拆分）；
- 每个版本的语义必须写清楚，尤其涉及搬数据或删旧表的版本。
"""

from __future__ import annotations

from ..config import STATE_SCHEMA_VERSION

SCHEMA_VERSION = STATE_SCHEMA_VERSION

_META = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# v1：任务 / 领取 / 执行账本 / 额度 / 迁移记录
_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS jobs (
    name TEXT PRIMARY KEY,
    prompt TEXT NOT NULL,
    every_minutes INTEGER,
    daily_at TEXT,
    deliver_to_qq INTEGER,
    skills TEXT NOT NULL DEFAULT '[]',
    context_from TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    next_run_at TEXT,
    last_run_at TEXT,
    last_result TEXT,
    revision INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_claims (
    job TEXT PRIMARY KEY,
    token TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job TEXT NOT NULL,
    started_at TEXT NOT NULL,
    duration_s REAL NOT NULL,
    ok INTEGER NOT NULL,
    result TEXT NOT NULL DEFAULT '',
    delivery TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_executions_job ON executions(job, id);
CREATE TABLE IF NOT EXISTS budgets (
    scope TEXT NOT NULL,
    day TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (scope, day)
);
CREATE TABLE IF NOT EXISTS migrations (
    source TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    sha256 TEXT NOT NULL DEFAULT '',
    imported INTEGER NOT NULL DEFAULT 0,
    detail TEXT NOT NULL DEFAULT '',
    applied_at TEXT NOT NULL
);
"""

# v2：跨进程会话租约（同一会话同一时刻只有一个进程在跑回合）
_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS session_leases (
    session_id INTEGER PRIMARY KEY,
    owner TEXT NOT NULL,
    token TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    expires_at REAL NOT NULL
);
"""

# v3：租约推广到任意"一次只能一个进程用"的资源（浏览器 profile 也要）。
# 会话租约从 session_id 整数键换成统一的 resource 键（"session:<id>"），既有一行都不丢。
_SCHEMA_V3 = """
CREATE TABLE IF NOT EXISTS resource_leases (
    resource TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    token TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    expires_at REAL NOT NULL
);
INSERT OR IGNORE INTO resource_leases(resource, owner, token, acquired_at, expires_at)
    SELECT 'session:' || session_id, owner, token, acquired_at, expires_at FROM session_leases;
DROP TABLE IF EXISTS session_leases;
"""

# v4：运行记录与事件流（D09）。事件序号按 run 单调递增，客户端用 after=seq 续读，
# 不需要重放整个回合；`owner` 记"哪个进程的哪次启动"，重启时据此把遗留 running 归入 interrupted。
_SCHEMA_V4 = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    parent_run_id TEXT,
    session_id INTEGER,
    channel TEXT NOT NULL DEFAULT '',
    principal TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    phase TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    ended_at TEXT,
    error_code TEXT NOT NULL DEFAULT '',
    error_text TEXT NOT NULL DEFAULT '',
    last_seq INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_runs_session ON runs(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status, created_at);
CREATE TABLE IF NOT EXISTS run_events (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (run_id, seq)
);
"""

# v5：模型调用流水与当日费用账（D10）。金额一律用**微美元整数**：
# 浮点累加会在几十万次调用后出现"账不平"，而账不平的预算等于没有预算。
# `cost_micro` 允许 NULL —— NULL 是"不知道多少钱"，0 是"确定免费"，两者不能混。
_SCHEMA_V5 = """
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    session_id INTEGER,
    purpose TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    cost_micro INTEGER,
    cost_source TEXT NOT NULL DEFAULT 'unknown',
    outcome TEXT NOT NULL DEFAULT 'ok',
    error_code TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 1,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_started ON llm_calls(started_at);
CREATE INDEX IF NOT EXISTS idx_llm_calls_run ON llm_calls(run_id, id);
CREATE TABLE IF NOT EXISTS llm_budget (
    day TEXT PRIMARY KEY,
    reserved_micro INTEGER NOT NULL DEFAULT 0,
    spent_micro INTEGER NOT NULL DEFAULT 0
);
"""

_SCHEMA_V6 = """
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    session_id INTEGER,
    run_id TEXT,
    scope TEXT NOT NULL DEFAULT 'private',
    kind TEXT NOT NULL DEFAULT 'image',
    source TEXT NOT NULL DEFAULT '',
    mime TEXT NOT NULL,
    size INTEGER NOT NULL,
    width INTEGER,
    height INTEGER,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_artifacts_session ON artifacts(session_id, created_at);
"""

_SCHEMA_V7 = """
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job TEXT NOT NULL,
    target_qq INTEGER NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    sent_at TEXT
);
-- 去重只在「活跃」期内生效：同一条消息 (target, content_hash) 不能排两次队；
-- 已 sent/discarded/dead 的不再占名额，之后同样内容可以重新入队
CREATE UNIQUE INDEX IF NOT EXISTS uq_outbox_live
    ON outbox(target_qq, content_hash) WHERE status IN ('pending', 'unknown');
CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(status, updated_at);
"""

_SCHEMA_V8 = """
CREATE TABLE IF NOT EXISTS mcp_schemas (
    server TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    tool_count INTEGER NOT NULL DEFAULT 0,
    confirmed_at TEXT,
    seen_at TEXT NOT NULL
);
"""

_SCHEMA_V9 = """
CREATE TABLE IF NOT EXISTS skill_acks (
    skill TEXT PRIMARY KEY,
    rev INTEGER NOT NULL,
    acked_at TEXT NOT NULL
);
"""

_SCHEMA_V10 = """
ALTER TABLE outbox ADD COLUMN claim_token TEXT;
DROP INDEX uq_outbox_live;
CREATE UNIQUE INDEX uq_outbox_live ON outbox(target_qq, content_hash)
    WHERE status IN ('pending', 'sending', 'unknown');
"""

_SCHEMA_V11 = """
ALTER TABLE jobs ADD COLUMN timezone TEXT NOT NULL DEFAULT 'local';
ALTER TABLE jobs ADD COLUMN context_max_age_minutes INTEGER NOT NULL DEFAULT 1440;
CREATE TABLE cron_outputs (
    job TEXT PRIMARY KEY, run_id TEXT NOT NULL, status TEXT NOT NULL,
    result TEXT NOT NULL, finished_at TEXT NOT NULL
);
"""

_SCHEMA_V12 = """
CREATE TABLE qq_messages (
    bot INTEGER NOT NULL, scene TEXT NOT NULL, message_id TEXT NOT NULL,
    status TEXT NOT NULL, created_at REAL NOT NULL,
    PRIMARY KEY(bot, scene, message_id)
);
CREATE INDEX idx_qq_messages_created ON qq_messages(created_at);
CREATE TABLE qq_sent (
    bot INTEGER NOT NULL, scene TEXT NOT NULL, message_id TEXT NOT NULL,
    created_at REAL NOT NULL, PRIMARY KEY(bot, scene, message_id)
);
"""

_SCHEMA_V13 = """
CREATE TABLE publication_drafts (
    id TEXT PRIMARY KEY, session_id INTEGER NOT NULL, kind TEXT NOT NULL,
    target TEXT NOT NULL, account TEXT NOT NULL, content TEXT NOT NULL, content_hash TEXT NOT NULL,
    observation TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft', attempt_token TEXT, evidence TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX uq_publication_sent ON publication_drafts(kind,target,account,content_hash)
    WHERE status IN ('sending','unknown','sent');
"""

# v14：后台命令账本。声明保留在 state_schema 中；command_schema.py 只做旧 import 兼容。
COMMAND_JOBS_SCHEMA = """
CREATE TABLE command_jobs (
    job_id TEXT PRIMARY KEY,
    session_id INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    owner_identity TEXT NOT NULL,
    command TEXT NOT NULL,
    shell TEXT NOT NULL,
    cwd TEXT NOT NULL,
    timeout_s REAL NOT NULL,
    status TEXT NOT NULL,
    process_identity TEXT NOT NULL DEFAULT '',
    exit_code INTEGER,
    side_effect_state TEXT NOT NULL DEFAULT 'none',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    error_code TEXT NOT NULL DEFAULT ''
);
CREATE INDEX command_jobs_session ON command_jobs(session_id, created_at);
"""

# v15：费用额度的**分口径账本**（P5-c）。
#
# 为什么另起一张表，而不是把 llm_budget 改成 (day, scope) 复合主键：
# llm_budget 是真机上已经在用的表，改主键要搬存量行、要处理跨版本回滚，
# 风险与收益不成比例。新表是纯增量——老数据一行不动，新数据按口径分账。
#
# 口径（scope）取通道名（cli/web/qq/cron…），不是参考实现的 qq/bili/other：
# 那是它自己的业务面，我们的自然口径是通道——用户想控制的是"QQ 上别烧太多"。
# 'total' 是汇总口径（所有口径之和），保留字。
#
# 每天只提醒一次的标记也在这张表里（level='warn'），不另开 meta：
# 同一张表、同一个事务，"记用量"和"记已提醒"不会各写各的。
#
# 用 CREATE TABLE IF NOT EXISTS：迁移要**可重放**——版本回退后再前滚是运维手段
# （备份恢复、测试构造旧库），裸 CREATE TABLE 会在重放时撞"already exists"而失败。
_SCHEMA_V15 = """
CREATE TABLE IF NOT EXISTS llm_budget_scope (
    day TEXT NOT NULL,
    scope TEXT NOT NULL,
    reserved_micro INTEGER NOT NULL DEFAULT 0,
    spent_micro INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, scope)
);
CREATE TABLE IF NOT EXISTS llm_budget_alert (
    day TEXT NOT NULL,
    scope TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'warn',
    at TEXT NOT NULL,
    PRIMARY KEY (day, scope, level)
);
"""

MIGRATIONS: dict[int, str] = {
    1: _SCHEMA_V1,
    2: _SCHEMA_V2,
    3: _SCHEMA_V3,
    4: _SCHEMA_V4,
    5: _SCHEMA_V5,
    6: _SCHEMA_V6,
    7: _SCHEMA_V7,
    8: _SCHEMA_V8,
    9: _SCHEMA_V9,
    10: _SCHEMA_V10,
    11: _SCHEMA_V11,
    12: _SCHEMA_V12,
    13: _SCHEMA_V13,
    14: COMMAND_JOBS_SCHEMA,
    15: _SCHEMA_V15,
}
