"""心跳入口：uv run omni-heartbeat（定时任务常驻调度，无人值守 fail-closed）。

与 omni-qq 的关系：各自独立进程、各连一条 NapCat WS；本进程**只发不收**
（消息事件全部忽略），不会与 omni-qq 重复回复。QQ 未启用时照常运行
（任务结果只落会话，可在 Web 工作台查看 cron: 开头的会话）。
"""
from __future__ import annotations

import asyncio
import os

from ..bootstrap import build_runtime
from ..config import DATA_DIR, load_env
from ..core.cron import CronScheduler, make_runtime_deliver, make_runtime_executor
from ..safety.gates import DenyApprover


async def _serve(runtime) -> None:
    scheduler = CronScheduler(
        runtime.cron_jobs,
        make_runtime_executor(runtime),
        make_runtime_deliver(runtime),
        ledger=runtime.cron_ledger,
    )

    async def _ignore_event(_event: dict) -> None:
        return None  # 消息由 omni-qq 处理；这里只保持发送通道

    tasks = [scheduler.run()]
    if runtime.qq is not None:
        tasks.append(runtime.qq.run(_ignore_event))
    jobs = runtime.cron_jobs.list()
    print(f"[心跳] 调度器已启动：{len(jobs)} 个任务（Ctrl+C 退出）", flush=True)
    await asyncio.gather(*tasks)


def main() -> None:
    load_env()
    pid_file = DATA_DIR / "run" / "omni-heartbeat.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()), encoding="utf-8")  # 工作台状态探测用
    try:
        runtime = build_runtime(approver=DenyApprover())  # 无人值守：fail-closed
        asyncio.run(_serve(runtime))
    finally:
        pid_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
