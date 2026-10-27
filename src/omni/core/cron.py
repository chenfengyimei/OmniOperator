"""心跳调度器：tick 循环 + 至多一次派发 + 任务执行/投递（Hermes cron 最小版）。

三纪律（xuexi/NOTES.md §1.6）：
1. tick 里**先推进 next_run_at 并落盘**，再派发（至多一次语义，进程崩溃也不重放）；
2. 错过超过半个周期（每日任务上限 2 小时）不补跑，只重排；
3. 无人值守上下文：入口通道负责用 DenyApprover 构建 runtime（fail-closed）。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import time
from collections.abc import Awaitable, Callable
from typing import Any

from ..memory.cron import Job, JobStore, compute_next_run, parse_iso, to_iso

TICK_SECONDS = 30
DAILY_MISS_SKIP_SECONDS = 2 * 3600


class CronScheduler:
    def __init__(
        self,
        store: JobStore,
        executor: Callable[[Job], Awaitable[str]],
        deliver: Callable[[Job, str], Awaitable[str]] | None = None,
        *,
        clock: Callable[[], float] = time.time,
        ledger: Any | None = None,
    ) -> None:
        self._store = store
        self._executor = executor
        self._deliver = deliver
        self._clock = clock
        self._ledger = ledger  # ExecutionLog（可选）：成功与失败都记账
        self._job_locks: dict[str, asyncio.Lock] = {}

    async def run(self) -> None:
        """常驻 tick 循环（阻塞直到取消）。"""
        while True:
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001 —— 单次 tick 失败不退出
                print(f"[心跳] tick 失败: {exc}", flush=True)
            await asyncio.sleep(TICK_SECONDS)

    async def tick(self, now: float | None = None) -> list[str]:
        """检查到期任务：先推进再派发；返回本次触发的任务名。"""
        now = self._clock() if now is None else now
        now_dt = dt.datetime.fromtimestamp(now).astimezone()
        fired: list[str] = []
        for job in self._store.list():
            if not job.enabled or not job.next_run_at:
                continue
            try:
                due_dt = parse_iso(job.next_run_at)
            except ValueError:
                continue
            if now_dt < due_dt:
                continue
            overdue = (now_dt - due_dt).total_seconds()
            period = self._period_seconds(job)
            skip = overdue > min(period / 2, DAILY_MISS_SKIP_SECONDS)
            # 纪律 1：先推进并落盘（至多一次）
            job.next_run_at = to_iso(compute_next_run(job, from_dt=now_dt))
            self._store.upsert(job, validate=False)
            if skip:
                print(
                    f"[心跳] {job.name} 错过窗口（迟到 {overdue / 60:.0f} 分钟），"
                    "跳过补跑、仅重排",
                    flush=True,
                )
                continue
            fired.append(job.name)
            asyncio.create_task(self._run_job_safely(job))
        return fired

    @staticmethod
    def _period_seconds(job: Job) -> float:
        if job.every_minutes is not None:
            return float(job.every_minutes) * 60
        return 86400.0

    async def _run_job_safely(self, job: Job) -> None:
        """同一任务串行执行；失败记账不崩调度器。"""
        lock = self._job_locks.setdefault(job.name, asyncio.Lock())
        async with lock:
            now_dt = dt.datetime.now().astimezone()
            started_at = to_iso(now_dt)
            started = time.monotonic()
            try:
                result = await self._executor(job)
            except Exception as exc:  # noqa: BLE001
                result = f"执行失败：{exc}"
            delivery = ""
            if self._deliver is not None:
                try:
                    delivery = await self._deliver(job, result)
                except Exception as exc:  # noqa: BLE001
                    delivery = f"投递失败：{exc}"
            if self._ledger is not None:
                self._ledger.record(
                    job=job.name,
                    started_at=started_at,
                    duration_s=time.monotonic() - started,
                    ok=not result.startswith("执行失败"),
                    result=result,
                    delivery=delivery,
                )
            fresh = self._store.get(job.name)
            if fresh is not None:
                fresh.last_run_at = to_iso(now_dt)
                fresh.last_result = (result[:180] + (f" | {delivery}" if delivery else ""))[:240]
                self._store.upsert(fresh, validate=False)


def _latest_source_output(store: Any, source_name: str) -> str:
    """取源任务绑定会话的最后一条 assistant 输出（没跑过则空，不虚构）。"""
    sid = store.get_binding("cron", source_name)
    if sid is None:
        return ""
    for role, content in reversed(store.recent_messages(sid, 10)):
        if role == "assistant" and content.strip():
            return content[:4000]
    return ""


def make_runtime_executor(runtime: Any) -> Callable[[Job], Awaitable[str]]:
    """默认执行器：任务绑定独立会话（保留历史，日报能对比昨天）→ agent → 回合 review。

    runtime 参数是 bootstrap.Runtime（duck typing，避免 core→bootstrap 依赖倒挂）。
    context_from 任务链（Hermes 同款）：源任务的最新输出注入本次 prompt 头部，
    串起"夜间整理 → 早报"这类先整理再汇报的流水线。
    """

    async def executor(job: Job) -> str:
        session_id = runtime.store.get_or_create_binding("cron", job.name)
        prompt = job.prompt
        if job.skills:
            names = ", ".join(job.skills)
            prompt = f"（先 skill_view 查看 {names} 并按其流程执行）\n{job.prompt}"
        if job.context_from:
            context = _latest_source_output(runtime.store, job.context_from)
            if context:
                prompt = (
                    f"以下是定时任务「{job.context_from}」的最新输出，"
                    f"作为本次工作的输入：\n\n{context}\n\n---\n{prompt}"
                )
        answer = await asyncio.to_thread(
            runtime.agent.chat, prompt, session_id=session_id
        )
        runtime.spawn_review(session_id)
        return answer

    return executor


def make_runtime_deliver(runtime: Any) -> Callable[[Job, str], Awaitable[str]]:
    """投递：QQ 白名单用户私聊；未配置/未连接时如实记账（不静默丢）。"""

    async def deliver(job: Job, result: str) -> str:
        target = job.deliver_to_qq
        if not target:
            return ""  # 只落会话，不投递
        qq = runtime.qq
        allowed = runtime.config.qq.allowed_users
        if qq is None:
            return "未投递：QQ 未启用"
        if allowed and int(target) not in allowed:
            return "未投递：目标不在白名单"
        if not qq.connected:
            return "未投递：QQ 未连接（NapCat 未启动？）"
        await qq.send_private(int(target), result)
        return f"已投递 QQ {target}"

    return deliver
