"""定时任务模型与存储的公开入口（保留旧导入路径）。

拆成了三块，避免"存储要用模型、模型又要存储"的循环依赖：

- `memory/jobs.py`：`Job` 模型、校验与调度时间计算（纯数据 + 纯函数）；
- `memory/state.py`：`StateStore`（`data/state.db` 事务存储）与 `JobStore`/`ExecutionLog` 门面；
- 本模块：只做再导出。`from ..memory.cron import Job, JobStore, compute_next_run` 这类
  老调用点一行都不用改。

为什么不再用 JSON 文件：见 `memory/state.py` 的模块注释（A09 跨进程丢写 / A10 损坏当空 /
A17 无至多一次）。
"""
from __future__ import annotations

from .jobs import (
    DAILY_RE,
    MIN_EVERY_MINUTES,
    NAME_RE,
    CronError,
    Job,
    compute_next_run,
    parse_iso,
    parse_run_at,
    to_iso,
)
from .state import (
    BROWSER_WRITE_SCOPE,
    SCHEMA_VERSION,
    Claim,
    ExecutionLog,
    JobStore,
    MigrationRecord,
    StateBusy,
    StateConflict,
    StateError,
    StateStore,
    StateUnavailable,
)

__all__ = [
    "BROWSER_WRITE_SCOPE",
    "DAILY_RE",
    "MIN_EVERY_MINUTES",
    "NAME_RE",
    "SCHEMA_VERSION",
    "Claim",
    "CronError",
    "ExecutionLog",
    "Job",
    "JobStore",
    "MigrationRecord",
    "StateBusy",
    "StateConflict",
    "StateError",
    "StateStore",
    "StateUnavailable",
    "compute_next_run",
    "parse_iso",
    "parse_run_at",
    "to_iso",
]
