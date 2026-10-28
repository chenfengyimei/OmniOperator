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
    make_runtime_deliver,
    make_runtime_executor,
    outbox_drain_loop,
)
from ..memory.state import StateUnavailable
from ..safety.gates import DenyApprover


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
    )
    tasks = [scheduler.run(), outbox_drain_loop(runtime)]
    if runtime.qq is not None:
        tasks.append(runtime.qq.run(_ignore_event))
    print(
        f"[心跳] 调度器已启动：{len(jobs)} 个任务（Ctrl+C 退出）；"
        "QQ 掉线期间的任务结果进入待投递队列，恢复后每分钟自动补投",
        flush=True,
    )
    await asyncio.gather(*tasks)


def main() -> None:
    load_env()
    runtime = build_runtime(approver=DenyApprover(), owner_kind="heartbeat")  # 无人值守：fail-closed
    # pid 目录来自 RuntimePaths（与真实运行数据根一起注入，不另拼全局路径）
    pid_file = runtime.paths.run / "omni-heartbeat.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()), encoding="utf-8")  # 工作台状态探测用
    try:
        asyncio.run(_serve(runtime))
    finally:
        pid_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
