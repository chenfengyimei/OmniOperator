"""心跳调度器：tick 循环 + 至多一次派发 + 任务执行/投递（Hermes cron 最小版）。

三纪律（xuexi/NOTES.md §1.6）：
1. tick 里**先推进 next_run_at 并落盘**，再派发（至多一次语义，进程崩溃也不重放）；
2. 错过超过半个周期（每日任务上限 2 小时）不补跑，只重排；
3. 无人值守上下文：入口通道负责用 DenyApprover 构建 runtime（fail-closed）。

D05：领取改成 `JobStore.claim_due()` —— 选任务、推进 next_run_at、写领取记录在**同一个
写事务**里完成。所以两个调度进程同时 tick 时，同一个到期时刻只有一个能拿到（A17 的一部分；
完整的调度/投递语义在 D17）。旧的 `list() + upsert()` 是"先读后写"，两个进程都会派发。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import time
from collections.abc import Awaitable, Callable
from typing import Any

from ..memory.cron import CronError, Job, JobStore, to_iso
from ..memory.state import StateUnavailable
from ..safety.gates import private_target_allowed

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
        """检查到期任务：原子领取 → 跳过过期窗口 → 派发；返回本次触发的任务名。"""
        now = self._clock() if now is None else now
        now_dt = dt.datetime.fromtimestamp(now).astimezone()
        fired: list[str] = []
        while True:
            claim = self._store.claim_due(now_dt)
            if claim is None:
                break
            job = claim.job
            period = self._period_seconds(job)
            if claim.overdue_s > min(period / 2, DAILY_MISS_SKIP_SECONDS):
                # 纪律 2：错过窗口只重排（claim 已推进 next_run_at），释放领取记录
                self._store.release_claim(claim.token)
                print(
                    f"[心跳] {job.name} 错过窗口（迟到 {claim.overdue_s / 60:.0f} 分钟），"
                    "跳过补跑、仅重排",
                    flush=True,
                )
                continue
            fired.append(job.name)
            asyncio.create_task(self._run_job_safely(job, claim.token))
        return fired

    @staticmethod
    def _period_seconds(job: Job) -> float:
        if job.every_minutes is not None:
            return float(job.every_minutes) * 60
        return 86400.0

    async def _run_job_safely(self, job: Job, claim_token: str | None = None) -> None:
        """同一任务串行执行；失败记账不崩调度器；结束时释放领取记录。

        成败判定只看**执行器是否抛异常**，不看返回文本前缀——返回文本是
        模型生成的，任务恰好让它从"执行失败"开头就会被误判（D17 收口项）。
        """
        lock = self._job_locks.setdefault(job.name, asyncio.Lock())
        async with lock:
            now_dt = dt.datetime.now().astimezone()
            started_at = to_iso(now_dt)
            started = time.monotonic()
            try:
                result = await self._executor(job)
                ok = True
            except Exception as exc:  # noqa: BLE001
                result = f"执行失败：{exc}"
                ok = False
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
                    ok=ok,
                    result=result,
                    delivery=delivery,
                )
            # 只写调度器拥有的字段：不碰 revision，免得用户正在编辑的表单永远冲突
            self._store.update_run_state(
                job.name,
                last_run_at=to_iso(now_dt),
                last_result=(result[:180] + (f" | {delivery}" if delivery else ""))[:240],
            )
            if claim_token:
                self._store.release_claim(claim_token)


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

    成败来自 `run_turn` 的结构化状态：未正常完成（取消/预算拦截/失败）就抛给
    调度器记"执行失败"——**不**再对模型生成的回答文本做前缀判断。
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
        turn = await asyncio.to_thread(
            runtime.agent.run_turn, prompt, session_id=session_id
        )
        if turn.status != "done":
            raise CronError(
                f"回合未完成（status={turn.status}"
                f"{'，' + turn.error_code if turn.error_code else ''}）：{turn.answer[:200]}"
            )
        runtime.spawn_review(session_id)
        return turn.answer

    return executor


def _enqueue_or_note(
    runtime: Any, job: str, target: int, content: str, reason: str, status: str
) -> str:
    """把没发出去的消息放进 outbox；入队失败时如实说明（结果本身已落会话，不丢）。"""
    outbox = runtime.outbox
    if outbox is None:
        return f"未投递：{reason}（outbox 不可用）"
    try:
        entry_id = outbox.enqueue(
            job=job, target_qq=int(target), content=content, status=status, error=reason
        )
    except (StateUnavailable, ValueError) as exc:
        return f"未投递：{reason}（入队失败：{exc}；结果已落在会话里，可手动查看）"
    if entry_id is None:
        return f"未投递：{reason}（同内容已在待投递队列，去重不重复入队）"
    if status == "unknown":
        return (
            f"发送结果未知（{reason}）：已记录为待人工处理，**不自动重发**"
            "（可能已发出一部分）；可在工作台「定时任务 → 待投递」决定"
        )
    return f"已入待投递队列（QQ 恢复后自动补投）：{reason}"


def make_runtime_deliver(runtime: Any) -> Callable[[Job, str], Awaitable[str]]:
    """投递：QQ 白名单用户私聊；发不出去的进 outbox（D17），不静默丢。

    授权判定与工具侧共用 `private_target_allowed`（A04 的根因是两处语义相反：
    这里把空名单当"不限"，工具侧当"全拒"）。策略每次从 runtime.qq_policy() 现取，
    撤销白名单在下一次投递就生效。

    分类纪律（大纲 D17：unknown 不能自动重发）：
    - QQ 未连接 → **明确没发**，入队 pending，恢复后由心跳进程补投；
    - 白名单撤销 → 用户意图，**不入队**（入队会在恢复后把用户已拒绝的消息发出去）；
    - 发送中途抛异常 → **结果未知**（可能已发出一部分），入队 unknown，
      只在工作台等人决定；
    - QQ 未启用 → 不入队（用户没开投递通道，队列只会越积越多）。
    """

    async def deliver(job: Job, result: str) -> str:
        target = job.deliver_to_qq
        if not target:
            return ""  # 只落会话，不投递
        qq = runtime.qq
        if qq is None:
            return "未投递：QQ 未启用"
        decision = private_target_allowed(runtime.qq_policy().allowed_users, target)
        if not decision.allowed:
            return f"未投递：{decision.reason}"
        if not qq.connected:
            return _enqueue_or_note(
                runtime, job.name, target, result, "QQ 未连接（NapCat 未启动？）", "pending"
            )
        try:
            await qq.send_private(int(target), result)
        except Exception as exc:  # noqa: BLE001 —— 发送中途失败：可能已发出一部分
            return _enqueue_or_note(runtime, job.name, target, result, str(exc), "unknown")
        return f"已投递 QQ {target}"

    return deliver


def make_outbox_drainer(runtime: Any) -> Callable[[], Awaitable[dict[str, int]]]:
    """补投 pending：QQ 连接恢复后由心跳进程周期调用。

    - 白名单已撤销的条目直接丢弃（撤销 = 用户意图，恢复后也不许发）；
    - 发送抛 `ConnectionError`（还没写到 socket，明确没发）→ 记一次尝试，保持 pending；
    - 其它异常 → 标 unknown，**停止自动补投**（可能已发出一部分，重发更糟）；
    - 尝试满 `OutboxStore.MAX_ATTEMPTS` 次 → dead（终态；面板可手动重试）。
    """

    async def drain() -> dict[str, int]:
        stats = {"sent": 0, "discarded": 0, "retry": 0, "unknown": 0, "dead": 0}
        qq = runtime.qq
        outbox = runtime.outbox
        if qq is None or outbox is None or not qq.connected:
            return stats  # 通道没恢复就不动队列
        for entry in outbox.pending():
            decision = private_target_allowed(
                runtime.qq_policy().allowed_users, entry["target_qq"]
            )
            if not decision.allowed:
                outbox.mark(
                    entry["id"], "discarded", error=f"白名单撤销：{decision.reason}"
                )
                stats["discarded"] += 1
                continue
            try:
                await qq.send_private(int(entry["target_qq"]), entry["content"])
                outbox.mark(entry["id"], "sent")
                stats["sent"] += 1
            except ConnectionError as exc:
                attempts = outbox.attempt(entry["id"], error=f"补投失败：{exc}")
                if attempts >= outbox.MAX_ATTEMPTS:
                    outbox.mark(entry["id"], "dead", error=f"补投失败 {attempts} 次：{exc}")
                    stats["dead"] += 1
                else:
                    stats["retry"] += 1
            except Exception as exc:  # noqa: BLE001
                outbox.mark(
                    entry["id"],
                    "unknown",
                    error=f"补投结果未知，已停止自动重发：{exc}",
                )
                stats["unknown"] += 1
        return stats

    return drain


async def outbox_drain_loop(runtime: Any, interval_s: float = 60.0) -> None:
    """周期补投（心跳进程挂载）。单轮异常（状态库瞬时不可用等）不杀循环。"""
    drain = make_outbox_drainer(runtime)
    while True:
        try:
            await drain()
        except Exception as exc:  # noqa: BLE001 —— 打印后下轮再试
            print(f"[心跳] outbox 补投失败（下轮重试）：{exc}", flush=True)
        await asyncio.sleep(interval_s)
