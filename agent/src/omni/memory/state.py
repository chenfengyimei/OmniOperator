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
from pathlib import Path
from typing import Any, Iterable

from .jobs import CronError, Job, compute_next_run, parse_iso, to_iso
from .cron_store import ExecutionLog, JobStore
from .mcp_schemas import MCPSchemaStore
from .outbox import OutboxStore
from .skill_acks import SkillAckStore
from .state_errors import (
    StateBusy,
    StateConflict,
    StateError,
    StateUnavailable,
    translate_operational as _translate_operational,
)
from .state_mixin_jobs import JobsMixin, _job_params
from .state_mixin_leases import LeasesMixin
from .state_mixin_ledger import LedgerMixin
from .state_records import BudgetHit, Claim, Lease, MigrationRecord, SessionLease
from .state_schema import MIGRATIONS, SCHEMA_VERSION, _META

# 兼容旧测试/调用对私有迁移表名的引用：声明本体已归 `state_schema.py`
_MIGRATIONS = MIGRATIONS

# MCPSchemaStore / SkillAckStore / OutboxStore 已迁至各自领域模块；
# 异常与 Claim/Lease/BudgetHit/MigrationRecord 已迁至 state_errors/state_records；
# 从 state 再导出以保持 `from omni.memory.state import MCPSchemaStore` 等旧 import 兼容。
# 一次性任务错过触发时间的宽限常量本体在 state_mixin_jobs.py
from .state_mixin_jobs import ONE_SHOT_MISSED_GRACE_S
BROWSER_WRITE_SCOPE = "browser_write"

# 只有 BEGIN 会重试（见 _begin）；次数与退避都要小，宁可尽快报错也不要长时间静默等待
_BEGIN_ATTEMPTS = 5
_BEGIN_RETRY_S = 0.02

LEGACY_JOBS = "cron_jobs.json"
LEGACY_EXECUTIONS = "cron_executions.json"
LEGACY_BUDGET = "browser_budget.json"

_CAPABILITY_TABLE = {"jobs": "jobs", "executions": "executions", "budget": "budgets"}


def _statements(sql: str) -> list[str]:
    """把迁移脚本拆成单条语句（迁移 SQL 里不要在字符串字面量中出现分号）。"""
    return [chunk.strip() for chunk in sql.split(";") if chunk.strip()]


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


class StateStore(JobsMixin, LeasesMixin, LedgerMixin):
    """`data/state.db` 的读写入口。进程内线程安全，跨进程靠 SQLite 事务。

    职责边界：连接/WAL/事务原语、schema 迁移、旧 JSON 导入与健康检查。
    任务/租约/账本方法由 JobsMixin/LeasesMixin/LedgerMixin 提供（公开方法
    名不变，`state.upsert_job(...)` 等调用点零改动）；Outbox/MCP 指纹/
    技能标记在各自领域模块中，构造时由 bootstrap 注入同一个 StateStore。
    """

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
