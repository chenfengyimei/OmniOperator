"""定时任务域的 `state.db` 访问 mixin（任务定义 / revision / 原子领取）。

`StateStore` 通过继承本 mixin 获得这些方法；宿主必须提供 `_require`、`_tx`、
`_read`（由 `memory/state.py::StateStore` 实现）。这里不做连接、迁移或事务实现。

为什么用 mixin：StateStore 的域方法群（jobs/leases/账本）公开 API 已被
core/cron、bootstrap 与 96 处调用点消费，保留方法名是兼容合同；按域拆文件
让评审只需看一个域的 SQL，而不是在 1000 行大类里找。
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import time
import uuid

from .jobs import CronError, Job, compute_next_run, parse_iso, to_iso
from .state_errors import StateConflict

# 一次性任务（无 every/daily 的闹钟）错过触发时间的宽限：超过即停用不补发——
# 迟到太久的闹钟没有意义，账本里留下"错过"的记录即可。
ONE_SHOT_MISSED_GRACE_S = 600


def _jobs_now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


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
        _jobs_now(),
        job.timezone,
        job.context_max_age_minutes,
    )


class JobsMixin:
    """任务域：定义读写、revision 乐观并发与跨进程原子领取。"""

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
                row = conn.execute("SELECT 1 FROM jobs WHERE name = ?", (job.context_from,)).fetchone()
                if row is None:
                    raise CronError(f"context_from 指向的任务 {job.context_from} 不存在（先创建它）")
                visited = {job.name}
                source = job.context_from
                while source:
                    if source in visited:
                        raise CronError("context_from 不能形成依赖环")
                    visited.add(source)
                    parent = conn.execute("SELECT context_from FROM jobs WHERE name=?", (source,)).fetchone()
                    source = parent[0] if parent else None
            current = conn.execute("SELECT revision FROM jobs WHERE name = ?", (job.name,)).fetchone()
            if current is None:
                if expected_revision not in (None, 0):
                    raise StateConflict(f"任务 {job.name} 不存在（你基于 revision {expected_revision} 提交）")
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
    ):
        """原子领取一个到期任务：同一个到期时刻只会有一个进程拿到。

        在**同一个写事务**里选任务、推进 `next_run_at`（至多一次语义）、写领取记录。
        两个调度进程同时调用时，后到的那次读到的已经是推进后的时刻，因此不会重复触发。
        """
        from .state_records import Claim

        self._require("jobs")
        token = token or uuid.uuid4().hex
        now = now.astimezone()
        with self._tx() as conn:
            stamp = time.time()
            conn.execute("DELETE FROM job_claims WHERE expires_at < ?", (stamp,))
            active = {r[0] for r in conn.execute("SELECT job FROM job_claims")}
            due: list[tuple[dt.datetime, Job]] = []
            for row in conn.execute("SELECT * FROM jobs WHERE enabled = 1 AND next_run_at IS NOT NULL").fetchall():
                raw = row["next_run_at"]
                try:
                    due_dt = parse_iso(str(raw))
                except (TypeError, ValueError):
                    continue
                if due_dt.tzinfo is None:
                    due_dt = due_dt.astimezone()
                if now >= due_dt:
                    candidate = _row_to_job(row)
                    is_once = candidate.every_minutes is None and not candidate.daily_at
                    if is_once:
                        # 一次性任务（闹钟）：迟到超过宽限（停机期间错过）不补发——
                        # 停用并留痕；宽限内的正常触发走下面的领取路径
                        if (now - due_dt).total_seconds() > ONE_SHOT_MISSED_GRACE_S:
                            conn.execute(
                                "UPDATE jobs SET enabled = 0, last_result = ? WHERE name = ?",
                                (
                                    f"错过触发时间 {raw}（超过 {ONE_SHOT_MISSED_GRACE_S // 60} 分钟宽限），未执行已停用",
                                    candidate.name,
                                ),
                            )
                        elif candidate.name not in active:
                            due.append((due_dt, candidate))
                        continue
                    if candidate.name in active:
                        conn.execute(
                            "UPDATE jobs SET next_run_at=?, last_result=? WHERE name=?",
                            (
                                to_iso(compute_next_run(candidate, now)),
                                "运行中再次到期：已合并，不积压",
                                candidate.name,
                            ),
                        )
                    else:
                        due.append((due_dt, candidate))
            if len(active) >= max(1, max_running):
                return None  # 未领取的任务留在数据库，无内存排队
            if not due:
                return None
            due.sort(key=lambda item: item[0])
            due_dt, job = due[0]
            if job.every_minutes is None and not job.daily_at:
                # 一次性任务领取即在**同一事务**停用（at-most-once）：执行器中途崩溃
                # 只留下"结果未知"的账本记录，绝不重触发——与 unknown 不重放纪律一致
                conn.execute("UPDATE jobs SET enabled = 0 WHERE name = ?", (job.name,))
            else:
                job.next_run_at = to_iso(compute_next_run(job, from_dt=now))
                conn.execute("UPDATE jobs SET next_run_at = ? WHERE name = ?", (job.next_run_at, job.name))
            conn.execute(
                "INSERT INTO job_claims(job, token, claimed_at, expires_at) VALUES (?, ?, ?, ?)",
                (job.name, token, _jobs_now(), stamp + float(ttl_s)),
            )
        return Claim(job=job, token=token, overdue_s=(now - due_dt).total_seconds())

    def release_claim(self, token: str) -> bool:
        with self._tx() as conn:
            return conn.execute("DELETE FROM job_claims WHERE token = ?", (token,)).rowcount > 0

    def renew_job_claim(self, token: str, ttl_s: float = 3600) -> bool:
        with self._tx() as conn:
            now = time.time()
            return (
                conn.execute(
                    "UPDATE job_claims SET expires_at=? WHERE token=? AND expires_at>?", (now + ttl_s, token, now)
                ).rowcount
                == 1
            )
