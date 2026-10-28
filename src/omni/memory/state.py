"""共享运行状态（`data/state.db`）：定时任务、执行账本、额度与迁移记录。

为什么不再用 JSON 文件（A09 / A10 / A17）：

- **跨进程丢写**：`threading.Lock` 只在一个进程里互斥。心跳、工作台、CLI 三个进程
  同时读改写同一个 JSON，后写的会把先写的整份覆盖掉；固定的 `.tmp` 名字还会互相踩。
- **损坏被当成正常**：`json.loads` 失败就 `return {}`，于是今天的写额度被清零、
  任务列表凭空变空——错误数据看起来像"首次启动"。
- **没有至多一次**：两个调度进程都能读到同一个到期任务。

SQLite 给出的正是这三件事：跨进程事务、唯一键、条件更新（`UPDATE ... WHERE used < limit`
的 rowcount 就是"我拿到了最后一份额度"的证据）。

纪律：
1. 事务短：只包数据库操作，不把模型请求、人工等待、网络调用放进写事务；
2. 失败要可解释：损坏/只读/版本未知/数据库忙分别有明确状态，**绝不当成空数据继续**；
3. 旧文件迁移要么整份成功、要么整份不生效（导入与"已迁移"标记同一个事务）。
"""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import shutil
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .jobs import CronError, Job, compute_next_run, parse_iso, to_iso

SCHEMA_VERSION = 11
BROWSER_WRITE_SCOPE = "browser_write"

# 只有 BEGIN 会重试（见 _begin）；次数与退避都要小，宁可尽快报错也不要长时间静默等待
_BEGIN_ATTEMPTS = 5
_BEGIN_RETRY_S = 0.02

LEGACY_JOBS = "cron_jobs.json"
LEGACY_EXECUTIONS = "cron_executions.json"
LEGACY_BUDGET = "browser_budget.json"

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

_MIGRATIONS: dict[int, str] = {
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
}

SESSION_RESOURCE = "session:{}"

_CAPABILITY_TABLE = {"jobs": "jobs", "executions": "executions", "budget": "budgets"}


class StateError(RuntimeError):
    """状态库无法给出可信结果。"""


class StateUnavailable(StateError):
    """相关能力必须停用（版本未知 / 旧数据损坏 / 目录只读），不得当成空数据继续。"""


class StateConflict(StateError):
    """revision 冲突：调用方基于旧快照提交。"""


class StateBusy(StateError):
    """数据库忙（另一个进程持有写事务）。忙不是成功，调用方必须如实上报。"""


@dataclass(frozen=True)
class BudgetHit:
    ok: bool
    used: int
    limit: int
    detail: str = ""


@dataclass(frozen=True)
class Claim:
    job: Job
    token: str
    overdue_s: float


@dataclass(frozen=True)
class Lease:
    """资源租约：谁占着这个资源（会话 / 浏览器 profile），什么时候自动失效。"""

    resource: str
    owner: str
    token: str
    acquired_at: str
    expires_at: float

    @property
    def session_id(self) -> int | None:
        """`session:<id>` 形态时返回会话 id（其它资源返回 None）。"""
        prefix, _, rest = self.resource.partition(":")
        return int(rest) if prefix == "session" and rest.isdigit() else None


SessionLease = Lease  # 兼容旧名


@dataclass(frozen=True)
class MigrationRecord:
    source: str
    status: str        # missing | empty | ok | corrupt | resolved_missing | failed
    sha256: str = ""
    imported: int = 0
    detail: str = ""
    applied_at: str = ""


def _statements(sql: str) -> list[str]:
    """把迁移脚本拆成单条语句（迁移 SQL 里不要在字符串字面量中出现分号）。"""
    return [chunk.strip() for chunk in sql.split(";") if chunk.strip()]


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _translate_operational(exc: sqlite3.OperationalError) -> StateError:
    """把"环境问题"翻译成可解释状态；其它 SQL 错误保持原样（那是代码 bug，不该被吞）。

    只读文件/目录、锁冲突、磁盘错误都不是"没有数据"——调用方必须能分辨并停用能力。
    """
    text = str(exc).lower()
    if "readonly" in text or "read-only" in text:
        return StateUnavailable(f"状态库不可写（文件或目录只读）：{exc}")
    if "locked" in text or "busy" in text:
        return StateBusy(f"状态库忙（另一个进程正在写）：{exc}")
    if "disk i/o" in text or "full" in text:
        return StateUnavailable(f"状态库写入失败（磁盘）：{exc}")
    return StateError(f"状态库操作失败：{exc}")


def _row_to_job(row: sqlite3.Row) -> Job:
    try:
        skills = json.loads(row["skills"] or "[]")
    except json.JSONDecodeError:
        skills = []
    return Job(
        name=str(row["name"]),
        prompt=str(row["prompt"]),
        every_minutes=int(row["every_minutes"]) if row["every_minutes"] is not None else None,
        daily_at=row["daily_at"],
        deliver_to_qq=int(row["deliver_to_qq"]) if row["deliver_to_qq"] is not None else None,
        skills=[str(s) for s in skills] if isinstance(skills, list) else [],
        context_from=row["context_from"],
        enabled=bool(row["enabled"]),
        next_run_at=row["next_run_at"],
        last_run_at=row["last_run_at"],
        last_result=row["last_result"],
        revision=int(row["revision"]),
        timezone=str(row["timezone"]),
        context_max_age_minutes=int(row["context_max_age_minutes"]),
    )


def _job_params(job: Job) -> tuple:
    return (
        job.name,
        job.prompt,
        job.every_minutes,
        job.daily_at,
        job.deliver_to_qq,
        json.dumps(list(job.skills or []), ensure_ascii=False),
        job.context_from,
        1 if job.enabled else 0,
        job.next_run_at,
        job.last_run_at,
        job.last_result,
        _now(),
        job.timezone,
        job.context_max_age_minutes,
    )


class StateStore:
    """`data/state.db` 的读写入口。进程内线程安全，跨进程靠 SQLite 事务。"""

    def __init__(
        self,
        path: Path,
        *,
        busy_timeout_ms: int = 5000,
        keep_executions: int = 200,
    ) -> None:
        self.path = Path(path)
        self._keep_executions = max(1, int(keep_executions))
        self._lock = threading.RLock()
        self._depth = 0
        self._degraded: dict[str, str] = {}
        self._notes: dict[str, str] = {}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                str(self.path),
                timeout=max(0.1, busy_timeout_ms / 1000),
                check_same_thread=False,
                isolation_level=None,  # 事务全部显式 BEGIN / COMMIT
            )
        except (OSError, sqlite3.Error) as exc:
            raise StateUnavailable(f"无法打开状态库 {self.path}：{exc}") from exc
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
            self._conn.executescript(_META)
        except sqlite3.OperationalError as exc:
            self._conn.close()
            raise StateUnavailable(
                f"状态库不可写（{self.path}）：{exc}。"
                "只读目录/文件被占用时不做任何降级处理，避免把已有状态当成空数据"
            ) from exc
        self._apply_schema()

    # ---------------------------------------------------------------- schema

    def _current_version(self) -> int:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            return 0
        try:
            return int(row[0])
        except (TypeError, ValueError) as exc:
            raise StateUnavailable(f"state.db 的 schema_version 不可解析：{row[0]!r}") from exc

    def _apply_schema(self) -> None:
        version = self._current_version()
        if version > SCHEMA_VERSION:
            self._conn.close()
            raise StateUnavailable(
                f"state.db 版本 {version} 高于本程序支持的 {SCHEMA_VERSION}："
                "拒绝打开（降级写入会破坏新版本数据）。请升级程序，或用备份恢复到新目录"
            )
        # 每一步都在**一个写事务**里：两个进程同时第一次启动时，后来者要么等锁，
        # 要么进事务后发现版本已经推进而直接跳过。不能用 executescript——它会先隐式
        # COMMIT，等于把"建表 + 搬数据 + 删旧表"拆成三条可被穿插的语句。
        while version < SCHEMA_VERSION:
            target = version + 1
            sql = _MIGRATIONS.get(target)
            if sql is None:
                self._conn.close()
                raise StateUnavailable(f"缺少 v{target} 的迁移脚本，拒绝继续")
            try:
                self._begin()
            except StateError as exc:
                self._conn.close()
                raise StateUnavailable(f"state.db 迁移到 v{target} 时无法取得写锁：{exc}") from exc
            try:
                current = self._current_version()
                if current >= target:  # 别的进程已经做完了
                    self._conn.rollback()
                    version = current
                    continue
                for statement in _statements(sql):
                    self._conn.execute(statement)
                self._conn.execute(
                    "INSERT INTO meta(key, value) VALUES ('schema_version', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (str(target),),
                )
                self._conn.commit()
            except sqlite3.Error as exc:
                with contextlib.suppress(sqlite3.Error):
                    self._conn.rollback()
                self._conn.close()
                raise StateUnavailable(f"state.db 迁移到 v{target} 失败：{exc}") from exc
            version = target

    @property
    def schema_version(self) -> int:
        return self._current_version()

    # ---------------------------------------------------------------- 基础

    @contextlib.contextmanager
    def _tx(self):
        """显式写事务。忙就报忙，不重试、不当成功。"""
        with self._lock:
            outer = self._depth == 0
            if outer:
                self._begin()
            self._depth += 1
            try:
                yield self._conn
            except BaseException as exc:
                self._depth -= 1
                if outer:
                    with contextlib.suppress(sqlite3.Error):
                        self._conn.rollback()
                if isinstance(exc, sqlite3.OperationalError):
                    raise _translate_operational(exc) from exc
                raise
            else:
                self._depth -= 1
                if outer:
                    try:
                        self._conn.commit()
                    except sqlite3.OperationalError as exc:
                        raise _translate_operational(exc) from exc

    def _begin(self) -> None:
        """开写事务。**只重试 BEGIN**，绝不重试事务体。

        为什么只重试 BEGIN：BEGIN 之前没有任何语句执行过，重试不可能重复副作用；
        而 Windows 上多个进程同时首次打开 WAL 库时，建立 -shm 存在竞态，会短暂返回
        "attempt to write a readonly database"/"database is locked"。这种情况重试即可，
        真·只读目录则在有限次重试后如实报"不可写"。
        """
        last: sqlite3.OperationalError | None = None
        for attempt in range(_BEGIN_ATTEMPTS):
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                return
            except sqlite3.OperationalError as exc:
                last = exc
                text = str(exc).lower()
                if "readonly" not in text and "read-only" not in text and "locked" not in text:
                    raise _translate_operational(exc) from exc
                time.sleep(_BEGIN_RETRY_S * (attempt + 1))
        assert last is not None
        raise _translate_operational(last) from last

    def _read(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            try:
                return self._conn.execute(sql, tuple(params)).fetchall()
            except sqlite3.OperationalError as exc:
                raise StateUnavailable(f"状态库读取失败：{exc}") from exc

    def _table_empty(self, capability: str) -> bool:
        table = _CAPABILITY_TABLE[capability]
        return int(self._read(f"SELECT COUNT(*) FROM {table}")[0][0]) == 0

    def degraded_reason(self, capability: str) -> str | None:
        return self._degraded.get(capability)

    def _require(self, capability: str) -> None:
        reason = self._degraded.get(capability)
        if reason:
            raise StateUnavailable(f"{capability} 状态不可用：{reason}")

    def close(self) -> None:
        with self._lock:
            with contextlib.suppress(sqlite3.Error):
                self._conn.close()

    # ---------------------------------------------------------------- 任务

    def list_jobs(self) -> list[Job]:
        self._require("jobs")
        rows = self._read("SELECT * FROM jobs ORDER BY name")
        return [_row_to_job(row) for row in rows]

    def get_job(self, name: str) -> Job | None:
        self._require("jobs")
        rows = self._read("SELECT * FROM jobs WHERE name = ?", (name,))
        return _row_to_job(rows[0]) if rows else None

    def upsert_job(
        self,
        job: Job,
        *,
        validate: bool = True,
        expected_revision: int | None = None,
    ) -> int:
        """写入任务定义，返回新的 revision。

        `expected_revision` 给了就做乐观并发：前端基于旧表单提交会得到 StateConflict，
        而不是静默覆盖后台（比如调度器/另一标签页）已经改过的内容。
        """
        self._require("jobs")  # 状态未知时连写也停：否则"能建不能读"更难解释
        if validate:
            job.validate()
        with self._tx() as conn:
            if validate and job.context_from:
                row = conn.execute(
                    "SELECT 1 FROM jobs WHERE name = ?", (job.context_from,)
                ).fetchone()
                if row is None:
                    raise CronError(
                        f"context_from 指向的任务 {job.context_from} 不存在（先创建它）"
                    )
                visited = {job.name}
                source = job.context_from
                while source:
                    if source in visited:
                        raise CronError("context_from 不能形成依赖环")
                    visited.add(source)
                    parent = conn.execute("SELECT context_from FROM jobs WHERE name=?", (source,)).fetchone()
                    source = parent[0] if parent else None
            current = conn.execute(
                "SELECT revision FROM jobs WHERE name = ?", (job.name,)
            ).fetchone()
            if current is None:
                if expected_revision not in (None, 0):
                    raise StateConflict(
                        f"任务 {job.name} 不存在（你基于 revision {expected_revision} 提交）"
                    )
                conn.execute(
                    "INSERT INTO jobs(name, prompt, every_minutes, daily_at, deliver_to_qq, "
                    "skills, context_from, enabled, next_run_at, last_run_at, last_result, "
                    "updated_at, timezone, context_max_age_minutes, revision) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                    _job_params(job),
                )
                job.revision = 1
                return 1
            revision = int(current[0])
            if expected_revision is not None and int(expected_revision) != revision:
                raise StateConflict(
                    f"任务 {job.name} 已被修改（当前 revision {revision}，"
                    f"你基于 {expected_revision} 提交）；请刷新后再改"
                )
            conn.execute(
                "UPDATE jobs SET prompt = ?, every_minutes = ?, daily_at = ?, "
                "deliver_to_qq = ?, skills = ?, context_from = ?, enabled = ?, "
                "next_run_at = ?, last_run_at = ?, last_result = ?, updated_at = ?, "
                "timezone = ?, context_max_age_minutes = ?, "
                "revision = revision + 1 WHERE name = ?",
                (*_job_params(job)[1:], job.name),
            )
            job.revision = revision + 1
            return job.revision

    def update_run_state(
        self,
        name: str,
        *,
        next_run_at: str | None = None,
        last_run_at: str | None = None,
        last_result: str | None = None,
    ) -> bool:
        """只更新调度器拥有的字段，**不递增 revision**（否则用户正在编辑的表单永远冲突）。"""
        self._require("jobs")
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE jobs SET next_run_at = COALESCE(?, next_run_at), "
                "last_run_at = COALESCE(?, last_run_at), "
                "last_result = COALESCE(?, last_result) WHERE name = ?",
                (next_run_at, last_run_at, last_result, name),
            )
            return cur.rowcount > 0

    def remove_job(self, name: str) -> bool:
        self._require("jobs")
        with self._tx() as conn:
            # 运行中删除不释放已有 claim：否则同名重建会与旧执行重叠。
            conn.execute("DELETE FROM cron_outputs WHERE job = ?", (name,))
            return conn.execute("DELETE FROM jobs WHERE name = ?", (name,)).rowcount > 0

    def claim_due(
        self,
        now: dt.datetime,
        *,
        token: str | None = None,
        ttl_s: float = 3600.0,
        max_running: int = 2,
    ) -> Claim | None:
        """原子领取一个到期任务：同一个到期时刻只会有一个进程拿到。

        在**同一个写事务**里选任务、推进 `next_run_at`（至多一次语义）、写领取记录。
        两个调度进程同时调用时，后到的那次读到的已经是推进后的时刻，因此不会重复触发。
        """
        self._require("jobs")
        token = token or uuid.uuid4().hex
        now = now.astimezone()
        with self._tx() as conn:
            stamp = time.time()
            conn.execute("DELETE FROM job_claims WHERE expires_at < ?", (stamp,))
            active = {r[0] for r in conn.execute("SELECT job FROM job_claims")}
            due: list[tuple[dt.datetime, Job]] = []
            for row in conn.execute(
                "SELECT * FROM jobs WHERE enabled = 1 AND next_run_at IS NOT NULL"
            ).fetchall():
                raw = row["next_run_at"]
                try:
                    due_dt = parse_iso(str(raw))
                except (TypeError, ValueError):
                    continue
                if due_dt.tzinfo is None:
                    due_dt = due_dt.astimezone()
                if now >= due_dt:
                    candidate = _row_to_job(row)
                    if candidate.name in active:
                        conn.execute("UPDATE jobs SET next_run_at=?, last_result=? WHERE name=?",
                                     (to_iso(compute_next_run(candidate, now)), "运行中再次到期：已合并，不积压", candidate.name))
                    else:
                        due.append((due_dt, candidate))
            if len(active) >= max(1, max_running):
                return None  # 未领取的任务留在数据库，无内存排队
            if not due:
                return None
            due.sort(key=lambda item: item[0])
            due_dt, job = due[0]
            job.next_run_at = to_iso(compute_next_run(job, from_dt=now))
            conn.execute(
                "UPDATE jobs SET next_run_at = ? WHERE name = ?", (job.next_run_at, job.name)
            )
            conn.execute(
                "INSERT INTO job_claims(job, token, claimed_at, expires_at) VALUES (?, ?, ?, ?)",
                (job.name, token, _now(), stamp + float(ttl_s)),
            )
        return Claim(job=job, token=token, overdue_s=(now - due_dt).total_seconds())

    def release_claim(self, token: str) -> bool:
        with self._tx() as conn:
            return conn.execute("DELETE FROM job_claims WHERE token = ?", (token,)).rowcount > 0

    def renew_job_claim(self, token: str, ttl_s: float = 3600) -> bool:
        with self._tx() as conn:
            now = time.time()
            return conn.execute("UPDATE job_claims SET expires_at=? WHERE token=? AND expires_at>?",
                                (now + ttl_s, token, now)).rowcount == 1

    # ---------------------------------------------------------------- 租约

    def lease(self, resource: str) -> Lease | None:
        rows = self._read("SELECT * FROM resource_leases WHERE resource = ?", (resource,))
        if not rows:
            return None
        row = rows[0]
        return Lease(
            resource=str(row["resource"]),
            owner=str(row["owner"]),
            token=str(row["token"]),
            acquired_at=str(row["acquired_at"]),
            expires_at=float(row["expires_at"]),
        )

    def acquire_lease(
        self,
        resource: str,
        owner: str,
        *,
        ttl_s: float = 600.0,
        token: str | None = None,
        now: float | None = None,
    ) -> Lease | None:
        """拿到租约返回 Lease；被别的所有者占着返回 None（不排队、不抢）。"""
        token = token or uuid.uuid4().hex
        stamp = time.time() if now is None else float(now)
        expires = stamp + float(ttl_s)
        with self._tx() as conn:
            # 过期租约先清掉：进程崩溃后不需要人工解锁，但要等满 TTL
            conn.execute(
                "DELETE FROM resource_leases WHERE resource = ? AND expires_at < ?",
                (resource, stamp),
            )
            held = conn.execute(
                "SELECT owner FROM resource_leases WHERE resource = ?", (resource,)
            ).fetchone()
            if held is not None:
                return None
            conn.execute(
                "INSERT INTO resource_leases(resource, owner, token, acquired_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (resource, str(owner), token, _now(), expires),
            )
        return Lease(
            resource=resource,
            owner=str(owner),
            token=token,
            acquired_at=_now(),
            expires_at=expires,
        )

    def renew_lease(
        self, token: str, *, ttl_s: float = 600.0, now: float | None = None
    ) -> bool:
        stamp = time.time() if now is None else float(now)
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE resource_leases SET expires_at = ? WHERE token = ? AND expires_at >= ?",
                (stamp + float(ttl_s), token, stamp),
            )
            return cur.rowcount > 0

    def release_lease(self, token: str) -> bool:
        """只释放自己那把（按 token）：不能顺手释放别人持有的租约。"""
        with self._tx() as conn:
            return conn.execute(
                "DELETE FROM resource_leases WHERE token = ?", (token,)
            ).rowcount > 0

    # 会话是资源租约的特例（键 `session:<id>`），保留语义化方法给调用方用
    def session_lease(self, session_id: int) -> Lease | None:
        return self.lease(SESSION_RESOURCE.format(int(session_id)))

    def acquire_session_lease(
        self,
        session_id: int,
        owner: str,
        *,
        ttl_s: float = 600.0,
        token: str | None = None,
        now: float | None = None,
    ) -> Lease | None:
        return self.acquire_lease(
            SESSION_RESOURCE.format(int(session_id)),
            owner,
            ttl_s=ttl_s,
            token=token,
            now=now,
        )

    def renew_session_lease(
        self, token: str, *, ttl_s: float = 600.0, now: float | None = None
    ) -> bool:
        return self.renew_lease(token, ttl_s=ttl_s, now=now)

    def release_session_lease(self, token: str) -> bool:
        return self.release_lease(token)

    # ---------------------------------------------------------------- 执行账本

    def record_execution(
        self,
        *,
        job: str,
        started_at: str,
        duration_s: float,
        ok: bool,
        result: str = "",
        delivery: str = "",
        keep: int | None = None,
    ) -> None:
        keep = self._keep_executions if keep is None else max(1, int(keep))
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO executions(job, started_at, duration_s, ok, result, delivery) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (job, started_at, round(float(duration_s), 2), 1 if ok else 0,
                 str(result)[:300], str(delivery)[:200]),
            )
            conn.execute(
                "DELETE FROM executions WHERE id NOT IN "
                "(SELECT id FROM executions ORDER BY id DESC LIMIT ?)",
                (keep,),
            )

    def recent_executions(self, limit: int = 50) -> list[dict]:
        rows = self._read(
            "SELECT job, started_at, duration_s, ok, result, delivery FROM executions "
            "ORDER BY id DESC LIMIT ?",
            (max(1, int(limit)),),
        )
        return [
            {
                "job": str(row["job"]),
                "started_at": str(row["started_at"]),
                "duration_s": float(row["duration_s"]),
                "ok": bool(row["ok"]),
                "result": str(row["result"]),
                "delivery": str(row["delivery"]),
            }
            for row in rows
        ]

    # ---------------------------------------------------------------- 额度

    def consume_budget(self, scope: str, day: str, limit: int) -> BudgetHit:
        """原子消耗一份额度：`UPDATE ... WHERE used < limit` 的 rowcount 就是判据。"""
        limit = int(limit)
        if limit <= 0:
            return BudgetHit(False, 0, max(0, limit), "额度上限为 0：未授权任何写操作")
        with self._tx() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO budgets(scope, day, used) VALUES (?, ?, 0)",
                (scope, day),
            )
            cur = conn.execute(
                "UPDATE budgets SET used = used + 1 WHERE scope = ? AND day = ? AND used < ?",
                (scope, day, limit),
            )
            used = int(
                conn.execute(
                    "SELECT used FROM budgets WHERE scope = ? AND day = ?", (scope, day)
                ).fetchone()[0]
            )
        if cur.rowcount == 1:
            return BudgetHit(True, used, limit, f"写操作 {used}/{limit}（今日）")
        return BudgetHit(False, used, limit, f"额度已用完（{used}/{limit}）")

    def budget_used(self, scope: str, day: str) -> int:
        rows = self._read(
            "SELECT used FROM budgets WHERE scope = ? AND day = ?", (scope, day)
        )
        return int(rows[0][0]) if rows else 0

    # ---------------------------------------------------------------- 迁移

    def _migration(self, source: str) -> MigrationRecord | None:
        rows = self._read("SELECT * FROM migrations WHERE source = ?", (source,))
        if not rows:
            return None
        row = rows[0]
        return MigrationRecord(
            source=str(row["source"]),
            status=str(row["status"]),
            sha256=str(row["sha256"]),
            imported=int(row["imported"]),
            detail=str(row["detail"]),
            applied_at=str(row["applied_at"]),
        )

    def _write_migration(
        self, source: str, status: str, sha256: str = "", imported: int = 0, detail: str = "",
        conn: sqlite3.Connection | None = None,
    ) -> MigrationRecord:
        stamp = _now()
        sql = (
            "INSERT INTO migrations(source, status, sha256, imported, detail, applied_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(source) DO UPDATE SET "
            "status = excluded.status, sha256 = excluded.sha256, imported = excluded.imported, "
            "detail = excluded.detail, applied_at = excluded.applied_at"
        )
        params = (source, status, sha256, int(imported), detail, stamp)
        if conn is not None:
            conn.execute(sql, params)
        else:
            with self._tx() as own:
                own.execute(sql, params)
        return MigrationRecord(source, status, sha256, imported, detail, stamp)

    def migrate_legacy(self, data_dir: Path) -> list[MigrationRecord]:
        """把旧 JSON 迁进 state.db：缺失 / 合法空 / 损坏分别处理，重入不重复导入。"""
        data_dir = Path(data_dir)
        return [
            self._migrate_file(data_dir / LEGACY_JOBS, "jobs", list),
            self._migrate_file(data_dir / LEGACY_EXECUTIONS, "executions", list),
            self._migrate_file(data_dir / LEGACY_BUDGET, "budget", dict),
        ]

    def _migrate_file(self, path: Path, capability: str, expect: type) -> MigrationRecord:
        key = str(path)
        previous = self._migration(key)
        if not path.is_file():
            if previous is None:
                return self._write_migration(key, "missing", detail="旧文件不存在，无需迁移")
            if previous.status == "corrupt":
                # 用户主动挪走了坏文件：解除停用，但如实说明用量已不可恢复
                self._degraded.pop(capability, None)
                return self._write_migration(
                    key, "resolved_missing", detail="损坏的旧文件已被移走；此前的计数无法恢复"
                )
            return previous

        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if previous is not None and previous.sha256 == digest and previous.status in {"ok", "empty"}:
            return previous  # 幂等：同一个文件不重复导入
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return self._mark_corrupt(path, capability, digest, f"解析失败：{exc}")
        if not isinstance(data, expect):
            return self._mark_corrupt(
                path, capability, digest, f"结构不符（期望 {expect.__name__}）"
            )
        if not data:
            return self._write_migration(key, "empty", digest, 0, "合法空文件，无内容可导入")

        with self._tx() as conn:
            imported, skipped = self._import_rows(conn, capability, data)
            record = self._write_migration(
                key, "ok", digest, imported, f"导入 {imported} 条" + (f"，跳过 {skipped} 条" if skipped else ""),
                conn=conn,
            )
        self._degraded.pop(capability, None)
        return record

    def _mark_corrupt(self, path: Path, capability: str, digest: str, detail: str) -> MigrationRecord:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        forensic = path.with_name(f"{path.name}.corrupt-{stamp}")
        with contextlib.suppress(OSError):
            shutil.copy2(path, forensic)  # 留一份现场，不覆盖、不删除原文件
        record = self._write_migration(str(path), "corrupt", digest, 0, detail)
        if capability == "budget":
            # 预算无法判断今天用了多少：必须停用，绝不能当成 0 继续放行
            self._degraded["budget"] = f"{path.name} 损坏：无法确认已用额度（{detail}）"
        elif self._table_empty(capability):
            self._degraded[capability] = f"{path.name} 损坏且库里没有可用数据（{detail}）"
        else:
            self._notes[capability] = f"{path.name} 损坏，但库内已有数据，按库内数据继续（{detail}）"
        return record

    def _import_rows(
        self, conn: sqlite3.Connection, capability: str, data: Any
    ) -> tuple[int, int]:
        if capability == "jobs":
            return self._import_jobs(conn, data)
        if capability == "executions":
            return self._import_executions(conn, data)
        return self._import_budget(conn, data)

    def _import_jobs(self, conn: sqlite3.Connection, rows: list) -> tuple[int, int]:
        imported = skipped = 0
        for raw in rows:
            if not isinstance(raw, dict):
                skipped += 1
                continue
            try:
                job = Job(
                    name=str(raw.get("name") or ""),
                    prompt=str(raw.get("prompt") or ""),
                    every_minutes=raw.get("every_minutes"),
                    daily_at=raw.get("daily_at"),
                    deliver_to_qq=raw.get("deliver_to_qq"),
                    skills=list(raw.get("skills") or []),
                    context_from=raw.get("context_from"),
                    enabled=bool(raw.get("enabled", True)),
                    next_run_at=raw.get("next_run_at"),
                    last_run_at=raw.get("last_run_at"),
                    last_result=raw.get("last_result"),
                )
            except (TypeError, ValueError):
                skipped += 1
                continue
            if not job.name or not job.prompt:
                skipped += 1
                continue
            cur = conn.execute(
                "INSERT INTO jobs(name, prompt, every_minutes, daily_at, deliver_to_qq, skills, "
                "context_from, enabled, next_run_at, last_run_at, last_result, updated_at, timezone, context_max_age_minutes, revision) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1) ON CONFLICT(name) DO NOTHING",
                _job_params(job),
            )
            imported += cur.rowcount
        return imported, skipped

    def _import_executions(self, conn: sqlite3.Connection, rows: list) -> tuple[int, int]:
        imported = skipped = 0
        for raw in rows:
            if not isinstance(raw, dict) or not raw.get("job"):
                skipped += 1
                continue
            try:
                conn.execute(
                    "INSERT INTO executions(job, started_at, duration_s, ok, result, delivery) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        str(raw.get("job")),
                        str(raw.get("started_at") or ""),
                        float(raw.get("duration_s") or 0.0),
                        1 if raw.get("ok") else 0,
                        str(raw.get("result") or "")[:300],
                        str(raw.get("delivery") or "")[:200],
                    ),
                )
            except (TypeError, ValueError):
                skipped += 1
                continue
            imported += 1
        return imported, skipped

    def _import_budget(self, conn: sqlite3.Connection, data: dict) -> tuple[int, int]:
        day = str(data.get("date") or "")
        if not day:
            return 0, 1
        try:
            used = max(0, int(data.get("used") or 0))
        except (TypeError, ValueError):
            return 0, 1
        conn.execute(
            "INSERT INTO budgets(scope, day, used) VALUES (?, ?, ?) "
            "ON CONFLICT(scope, day) DO UPDATE SET used = MAX(used, excluded.used)",
            (BROWSER_WRITE_SCOPE, day, used),
        )
        return 1, 0

    # ---------------------------------------------------------------- 健康

    def health(self) -> dict:
        with self._lock:
            try:
                integrity = str(self._conn.execute("PRAGMA integrity_check").fetchone()[0])
                journal = str(self._conn.execute("PRAGMA journal_mode").fetchone()[0])
                version = self._current_version()
                migrations = [
                    {
                        "source": str(row["source"]),
                        "status": str(row["status"]),
                        "imported": int(row["imported"]),
                        "detail": str(row["detail"]),
                        "applied_at": str(row["applied_at"]),
                    }
                    for row in self._conn.execute(
                        "SELECT * FROM migrations ORDER BY source"
                    ).fetchall()
                ]
                counts = {
                    table: int(self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                    for table in ("jobs", "executions", "budgets", "runs")
                }
            except sqlite3.Error as exc:
                return {"path": str(self.path), "integrity": f"error: {exc}", "degraded": dict(self._degraded)}
        return {
            "path": str(self.path),
            "schema_version": version,
            "supported_version": SCHEMA_VERSION,
            "journal_mode": journal,
            "integrity": integrity,
            "counts": counts,
            "degraded": dict(self._degraded),
            "notes": dict(self._notes),
            "migrations": migrations,
        }


class JobStore:
    """定时任务仓储的门面（旧类名保留，调用点不动）。"""

    def __init__(self, state: StateStore) -> None:
        self._state = state

    @property
    def state(self) -> StateStore:
        return self._state

    def list(self) -> list[Job]:
        return self._state.list_jobs()

    def get(self, name: str) -> Job | None:
        return self._state.get_job(name)

    def upsert(
        self, job: Job, *, validate: bool = True, expected_revision: int | None = None
    ) -> int:
        return self._state.upsert_job(
            job, validate=validate, expected_revision=expected_revision
        )

    def remove(self, name: str) -> bool:
        return self._state.remove_job(name)

    def claim_due(self, now: dt.datetime, **kwargs) -> Claim | None:
        return self._state.claim_due(now, **kwargs)

    def release_claim(self, token: str) -> bool:
        return self._state.release_claim(token)

    def renew_claim(self, token: str, ttl_s: float = 3600) -> bool:
        return self._state.renew_job_claim(token, ttl_s)

    def update_run_state(self, name: str, **fields) -> bool:
        return self._state.update_run_state(name, **fields)


class ExecutionLog:
    """执行账本门面：滚动保留最近 keep 条（旧类名保留，调用点不动）。"""

    def __init__(self, state: StateStore, keep: int = 200) -> None:
        self._state = state
        self._keep = keep

    def record(
        self,
        job: str,
        started_at: str,
        duration_s: float,
        ok: bool,
        result: str = "",
        delivery: str = "",
    ) -> None:
        self._state.record_execution(
            job=job,
            started_at=started_at,
            duration_s=duration_s,
            ok=ok,
            result=result,
            delivery=delivery,
            keep=self._keep,
        )

    def recent(self, limit: int = 50) -> list[dict]:
        return self._state.recent_executions(limit)

    def output(self, job: str, run_id: str, status: str, result: str = "", *, finished_at: str | None = None) -> None:
        with self._state._tx() as conn:
            conn.execute("INSERT INTO cron_outputs(job,run_id,status,result,finished_at) VALUES (?,?,?,?,?) "
                         "ON CONFLICT(job) DO UPDATE SET run_id=excluded.run_id,status=excluded.status,"
                         "result=excluded.result,finished_at=excluded.finished_at",
                         (job, run_id, status, result[:4000], finished_at or _now()))

    def source(self, job: str, max_age_minutes: int) -> dict:
        rows = self._state._read("SELECT * FROM cron_outputs WHERE job=?", (job,))
        if not rows or rows[0]["status"] != "done":
            raise CronError(f"依赖任务 {job} 没有最近成功结果（未完成或失败），本次停止")
        result = dict(rows[0])
        age = dt.datetime.now().astimezone() - parse_iso(result["finished_at"]).astimezone()
        if not 0 <= age.total_seconds() <= max_age_minutes * 60:
            raise CronError(f"依赖任务 {job} 的结果已过期，本次停止")
        return result


class OutboxStore:
    """投递 outbox（D17）：QQ 掉线时心跳结果不丢，恢复后由心跳进程补投。

    语义：
    - **pending**：明确没发出去（QQ 未连接等），恢复后自动补投；
    - **unknown**：发送中途失败——可能已经发出一部分，**不自动补投**，
      只在工作台等人决定（重复发送比漏发半条更糟）；
    - sent / discarded / dead：终态，不再参与去重与补投。

    去重 = (target_qq, content_hash) 在活跃期（pending/unknown）内唯一：
    同一条消息不会排两次队；进入终态后同样的内容可以重新入队。
    """

    LIVE_STATUSES = ("pending", "sending", "unknown")
    MAX_LIVE = 50
    MAX_ATTEMPTS = 3

    def __init__(self, state: StateStore) -> None:
        self._state = state

    @staticmethod
    def _now() -> str:
        return to_iso(dt.datetime.now().astimezone())

    def enqueue(
        self,
        *,
        job: str,
        target_qq: int,
        content: str,
        status: str = "pending",
        error: str = "",
    ) -> int | None:
        """入队返回条目 id；活跃重复返回 None（不是错误）；队列满抛 ValueError。"""
        if status not in {"pending", "unknown"}:
            raise ValueError("新投递只能是 pending 或 unknown")
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        now = self._now()
        try:
            with self._state._tx() as conn:
                live = conn.execute(
                    "SELECT COUNT(*) FROM outbox WHERE status IN ('pending','sending','unknown')"
                ).fetchone()[0]
                if int(live) >= self.MAX_LIVE:
                    raise ValueError(
                        f"待投递队列已满（{self.MAX_LIVE} 条活跃上限）：先在工作台清理"
                    )
                cursor = conn.execute(
                    "INSERT INTO outbox(job, target_qq, content, content_hash, status,"
                    " attempts, last_error, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,0,?,?,?)",
                    (job, int(target_qq), content, content_hash, status, error, now, now),
                )
                return int(cursor.lastrowid or 0)
        except sqlite3.IntegrityError:
            return None  # 活跃期内 (target, content_hash) 已在队列——去重命中

    def list(self, *, limit: int = 100) -> list[dict]:
        rows = self._state._read(
            "SELECT * FROM outbox"
            " ORDER BY CASE WHEN status IN ('pending','sending','unknown') THEN 0 ELSE 1 END,"
            " updated_at DESC LIMIT ?",
            (max(1, int(limit)),),
        )
        return [self._row(r) for r in rows]

    def get(self, entry_id: int) -> dict | None:
        rows = self._state._read("SELECT * FROM outbox WHERE id = ?", (int(entry_id),))
        return self._row(rows[0]) if rows else None

    def pending(self, *, max_attempts: int | None = None) -> list[dict]:
        cap = self.MAX_ATTEMPTS if max_attempts is None else max(1, int(max_attempts))
        rows = self._state._read(
            "SELECT * FROM outbox WHERE status = 'pending' AND attempts < ?"
            " ORDER BY created_at, id",
            (cap,),
        )
        return [self._row(r) for r in rows]

    def mark(self, entry_id: int, status: str, *, error: str = "") -> bool:
        """写终态/状态；sent 会带上 sent_at。返回是否真的改到了行。"""
        now = self._now()
        sent_at = now if status == "sent" else None
        with self._state._tx() as conn:
            cursor = conn.execute(
                "UPDATE outbox SET status = ?, last_error = ?, updated_at = ?,"
                " sent_at = COALESCE(?, sent_at) WHERE id = ? AND status != 'sending'",
                (status, error, now, sent_at, int(entry_id)),
            )
            return cursor.rowcount > 0

    def claim_next(self, *, now: dt.datetime | None = None, stale_after_s: float = 300,
                   only_id: int | None = None, exclude_ids: tuple[int, ...] = ()) -> dict | None:
        """原子领取一次发送；过期在途记录变 unknown，绝不恢复为可重试。"""
        instant = now or dt.datetime.now().astimezone()
        stamp = to_iso(instant)
        cutoff = to_iso(instant - dt.timedelta(seconds=max(60, stale_after_s)))
        with self._state._tx() as conn:
            conn.execute(
                "UPDATE outbox SET status='unknown', claim_token=NULL, updated_at=?,"
                " last_error='发送进程中断或超时，结果未知，禁止自动重发'"
                " WHERE status='sending' AND julianday(updated_at) < julianday(?)",
                (stamp, cutoff),
            )
            filters = "" if only_id is None else " AND id=?"
            params = [self.MAX_ATTEMPTS] + ([] if only_id is None else [int(only_id)])
            if exclude_ids:
                filters += " AND id NOT IN (" + ",".join("?" for _ in exclude_ids) + ")"
                params.extend(exclude_ids)
            row = conn.execute(
                "SELECT id FROM outbox WHERE status='pending' AND attempts < ?"
                + filters + " ORDER BY created_at,id LIMIT 1", params,
            ).fetchone()
            if row is None:
                return None
            token = uuid.uuid4().hex
            conn.execute(
                "UPDATE outbox SET status='sending', claim_token=?, updated_at=?,"
                " attempts=attempts+1 WHERE id=? AND status='pending'",
                (token, stamp, row[0]),
            )
            return self._row(conn.execute("SELECT * FROM outbox WHERE id=?", (row[0],)).fetchone())

    def finish_claim(self, entry_id: int, token: str, status: str, *, error: str = "") -> bool:
        """只有仍持有本次发送身份的调用方能结算；旧进程不能覆盖人工处理结果。"""
        if status not in {"pending", "sent", "unknown", "dead", "discarded"}:
            raise ValueError("非法投递结果")
        now = self._now()
        with self._state._tx() as conn:
            cursor = conn.execute(
                "UPDATE outbox SET status=?, claim_token=NULL, last_error=?, updated_at=?,"
                " sent_at=CASE WHEN ?='sent' THEN ? ELSE sent_at END"
                " WHERE id=? AND status='sending' AND claim_token=?",
                (status, error, now, status, now, int(entry_id), token),
            )
            return cursor.rowcount == 1

    def attempt(self, entry_id: int, *, error: str = "") -> int:
        """记一次失败尝试并返回累计次数（调用方据此决定是否成死信）。"""
        now = self._now()
        with self._state._tx() as conn:
            cursor = conn.execute(
                "UPDATE outbox SET attempts = attempts + 1, last_error = ?, updated_at = ?"
                " WHERE id = ?",
                (error, now, int(entry_id)),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"outbox 条目 {entry_id} 不存在")
            rows = conn.execute(
                "SELECT attempts FROM outbox WHERE id = ?", (int(entry_id),)
            ).fetchall()
            return int(rows[0][0])

    def retry(self, entry_id: int, *, note: str = "用户手动重试") -> bool:
        """unknown/dead/discarded → 重新排队（attempts 清零，给满额重试预算）。"""
        now = self._now()
        with self._state._tx() as conn:
            cursor = conn.execute(
                "UPDATE outbox SET status = 'pending', attempts = 0, last_error = ?,"
                " updated_at = ? WHERE id = ? AND status IN ('unknown','dead','discarded')",
                (note, now, int(entry_id)),
            )
            return cursor.rowcount > 0

    def discard(self, entry_id: int, *, note: str = "用户丢弃") -> bool:
        now = self._now()
        with self._state._tx() as conn:
            cursor = conn.execute(
                "UPDATE outbox SET status = 'discarded', last_error = ?, updated_at = ?"
                " WHERE id = ? AND status IN ('pending','unknown','dead','discarded')",
                (note, now, int(entry_id)),
            )
            return cursor.rowcount > 0

    @staticmethod
    def _row(r: Iterable) -> dict:
        row = list(r)
        keys = (
            "id", "job", "target_qq", "content", "content_hash", "status",
            "attempts", "last_error", "created_at", "updated_at", "sent_at", "claim_token",
        )
        out = {k: v for k, v in zip(keys, row)}
        out["id"] = int(out["id"])
        out["target_qq"] = int(out["target_qq"])
        out["attempts"] = int(out["attempts"])
        return out


class MCPSchemaStore:
    """MCP 工具目录指纹（D19）：服务升级换 schema 后，旧只读授权要重新检查。

    只读授权是用户按**当时的**工具与参数形态给的；服务升级后同名工具可能
    完全换了解释。指纹不判断"变好还是变坏"——变化即提醒，复查是人做的事。
    """

    def __init__(self, state: StateStore) -> None:
        self._state = state

    def observe(self, server: str, fingerprint: str, tool_count: int) -> str | None:
        """记录当前观察到的指纹；与存量不一致时返回旧指纹（= 漂移提醒），首次返回 None。"""
        now = to_iso(dt.datetime.now().astimezone())
        # 首次登记和漂移撤权在同一事务，不能让并发观察看到新指纹配旧确认。
        with self._state._tx() as conn:
            row = conn.execute(
                "SELECT fingerprint, confirmed_at FROM mcp_schemas WHERE server = ?",
                (server,),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO mcp_schemas(server, fingerprint, tool_count,"
                    " confirmed_at, seen_at) VALUES (?,?,?,?,?)",
                    (server, fingerprint, int(tool_count), now, now),
                )
                return None  # 首次使用显式配置的只读名单，后续变化必须复查。
            stored = str(row[0])
            conn.execute(
                "UPDATE mcp_schemas SET fingerprint = ?, tool_count = ?, seen_at = ?,"
                " confirmed_at = CASE WHEN fingerprint = ? THEN confirmed_at ELSE NULL END"
                " WHERE server = ?",
                (fingerprint, int(tool_count), now, fingerprint, server),
            )
            return stored if stored != fingerprint else None

    def confirm(self, server: str, fingerprint: str) -> bool:
        """用户复查后确认当前指纹（清除漂移提醒）。指纹又变了就拒绝——确认的必须是看到的那份。"""
        now = to_iso(dt.datetime.now().astimezone())
        with self._state._tx() as conn:
            cursor = conn.execute(
                "UPDATE mcp_schemas SET confirmed_at = ? WHERE server = ?"
                " AND fingerprint = ?",
                (now, server, fingerprint),
            )
            return cursor.rowcount > 0

    def status(self, server: str) -> dict | None:
        rows = self._state._read(
            "SELECT fingerprint, tool_count, confirmed_at, seen_at FROM mcp_schemas"
            " WHERE server = ?",
            (server,),
        )
        if not rows:
            return None
        return {
            "fingerprint": str(rows[0][0]),
            "tool_count": int(rows[0][1]),
            "confirmed_at": rows[0][2],
            "seen_at": rows[0][3],
        }


class SkillAckStore:
    """技能变更的「已读」标记（D18）：待审阅 = 标记之后由 agent/review 写下的修订。

    标记只前进不后退（`MAX`），刷新页面不会把已读变回未读；
    用户自己的编辑器保存（author=user）与磁盘外部改动的快照（author=external）
    本来就不进待审阅队列，标记也不因它们变化。
    """

    def __init__(self, state: StateStore) -> None:
        self._state = state

    def ack(self, skill: str, rev: int) -> int:
        """标记已读到 rev（只前进）；返回标记后的值。"""
        now = to_iso(dt.datetime.now().astimezone())
        with self._state._tx() as conn:
            conn.execute(
                "INSERT INTO skill_acks(skill, rev, acked_at) VALUES (?,?,?)"
                " ON CONFLICT(skill) DO UPDATE SET"
                " rev = MAX(skill_acks.rev, excluded.rev), acked_at = excluded.acked_at",
                (skill, max(0, int(rev)), now),
            )
            row = conn.execute(
                "SELECT rev FROM skill_acks WHERE skill = ?", (skill,)
            ).fetchone()
            return int(row[0])

    def acked(self, skill: str) -> int:
        rows = self._state._read(
            "SELECT rev FROM skill_acks WHERE skill = ?", (skill,)
        )
        return int(rows[0][0]) if rows else 0

    def acked_all(self) -> dict[str, int]:
        rows = self._state._read("SELECT skill, rev FROM skill_acks")
        return {str(r[0]): int(r[1]) for r in rows}
