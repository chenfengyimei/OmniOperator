"""定时任务域的两个门面（`JobStore` / `ExecutionLog`）。

之前定义在 `state.py` 末尾；按域归位后由 state.py 单向 import 本模块再
导出，旧 import `from omni.memory.state import JobStore, ExecutionLog`
继续可用。两个门面只做转发与 cron_outputs 读写，不持有连接——事务仍由
注入的 `StateStore` 提供，表结构声明在 `memory/state_schema.py`。

`JobStore` 是 bootstrap 组装根暴露给 core/cron 与 scheduling 工具的公开
仓储口径；`ExecutionLog` 是执行账本 + `cron_outputs` 任务链结果的门面。
"""
from __future__ import annotations

import datetime as dt

from .jobs import CronError, Job, parse_iso


class JobStore:
    """定时任务仓储的门面（旧类名保留，调用点不动）。"""

    def __init__(self, state) -> None:
        # 类型标注放宽为鸭子类型：宿主是 StateStore，直接标注会形成循环 import。
        self._state = state

    @property
    def state(self):
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

    def claim_due(self, now: dt.datetime, **kwargs):
        return self._state.claim_due(now, **kwargs)

    def release_claim(self, token: str) -> bool:
        return self._state.release_claim(token)

    def renew_claim(self, token: str, ttl_s: float = 3600) -> bool:
        return self._state.renew_job_claim(token, ttl_s)

    def update_run_state(self, name: str, **fields) -> bool:
        return self._state.update_run_state(name, **fields)


class ExecutionLog:
    """执行账本门面：滚动保留最近 keep 条（旧类名保留，调用点不动）。"""

    def __init__(self, state, keep: int = 200) -> None:
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
                         (job, run_id, status, result[:4000], finished_at or _log_now()))

    def source(self, job: str, max_age_minutes: int) -> dict:
        rows = self._state._read("SELECT * FROM cron_outputs WHERE job=?", (job,))
        if not rows or rows[0]["status"] != "done":
            raise CronError(f"依赖任务 {job} 没有最近成功结果（未完成或失败），本次停止")
        result = dict(rows[0])
        age = dt.datetime.now().astimezone() - parse_iso(result["finished_at"]).astimezone()
        if not 0 <= age.total_seconds() <= max_age_minutes * 60:
            raise CronError(f"依赖任务 {job} 的结果已过期，本次停止")
        return result


def _log_now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")
