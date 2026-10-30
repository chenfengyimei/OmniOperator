"""心跳入口：uv run omni-heartbeat（定时任务常驻调度，无人值守 fail-closed）。

与 omni-qq 的关系：各自独立进程、各连一条 NapCat WS；本进程**只发不收**
（消息事件全部忽略），不会与 omni-qq 重复回复。QQ 未启用时照常运行
（任务结果只落会话，可在 Web 工作台查看 cron: 开头的会话）。
"""
from __future__ import annotations

import asyncio
import os

from ..bootstrap import build_runtime
from ..config import load_env
from ..core.cron import (
    CronScheduler,
    artifacts_sweep_loop,
    make_runtime_deliver,
    make_runtime_executor,
    outbox_drain_loop,
)
from ..core.journal import journal_loop
from ..memory.state import StateUnavailable
from ..safety.gates import DenyApprover
from .lifecycle import serve_until_stop


async def _serve(runtime) -> None:
    async def _ignore_event(_event: dict) -> None:
        return None  # 消息由 omni-qq 处理；这里只保持发送通道

    try:
        jobs = runtime.cron_jobs.list()
    except StateUnavailable as exc:
        # 状态库损坏/被降级时**不启动调度器**：既不能把"读不出来"当成"没有任务"，
        # 也不能带着未知的 last_run/next_run 继续派发。QQ 发送通道照常保持。
        print(f"[心跳] 定时任务状态不可用，调度器未启动：{exc}", flush=True)
        if runtime.qq is not None:
            await runtime.qq.run(_ignore_event)
        return

    scheduler = CronScheduler(
        runtime.cron_jobs,
        make_runtime_executor(runtime),
        make_runtime_deliver(runtime),
        ledger=runtime.cron_ledger,
        max_running=runtime.config.cron.max_running,
    )
    tasks = [scheduler.run(), outbox_drain_loop(runtime), artifacts_sweep_loop(runtime),
             journal_loop(runtime)]
    if runtime.qq is not None:
        tasks.append(runtime.qq.run(_ignore_event))
    print(
        f"[心跳] 调度器已启动：{len(jobs)} 个任务（Ctrl+C 退出）；"
        "QQ 掉线期间的任务结果进入待投递队列，恢复后每分钟自动补投；"
        "每小时清理过期产物；每5分钟检查记忆整理（日/周/月笔记）",
        flush=True,
    )
    try:
        await asyncio.gather(*tasks)
    finally:
        if not await scheduler.close():
            print("[心跳] 仍有在途任务，未宣称关闭完成", flush=True)


def main() -> None:
    from ..system.observability import configure_service_logging
    configure_service_logging()
    load_env()
    runtime = build_runtime(approver=DenyApprover(), owner_kind="heartbeat")  # 无人值守：fail-closed
    # pid 目录来自 RuntimePaths（与真实运行数据根一起注入，不另拼全局路径）
    launch_id = runtime.services.register_current("omni-heartbeat")
    try:
        asyncio.run(serve_until_stop(runtime, "omni-heartbeat", launch_id, _serve(runtime)))
    finally:
        result = runtime.close()
        if not result["complete"]:
            print(f"[心跳] 关闭残留：{result['residual']}", flush=True)
        if result["complete"]:
            runtime.services.unregister_current("omni-heartbeat", launch_id)


if __name__ == "__main__":
    main()
