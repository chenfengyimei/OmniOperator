"""定时任务工具集（toolset=cron）：agent 可自主排程（Hermes cronjob_tools 精神）。

"每天早上八点给我发B站日报"→ cron_add 一次搞定。任务由 omni-heartbeat
进程执行（工具只写 jobs.json，跨进程共享）。
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from ..memory.cron import CronError, Job, JobStore, compute_next_run, to_iso
from .registry import Risk, Tool, ToolRegistry


def register_cron_tools(
    registry: ToolRegistry, store: JobStore, allowed_qq_users: list[int]
) -> None:

    def _fmt(job: Job) -> str:
        schedule = f"每 {job.every_minutes} 分钟" if job.every_minutes else f"每天 {job.daily_at}"
        state = "启用" if job.enabled else "停用"
        delivery = f" → QQ {job.deliver_to_qq}" if job.deliver_to_qq else ""
        return (
            f"- {job.name}：{schedule}{delivery}（{state}，下次 {job.next_run_at or '待排'}，"
            f"上次 {job.last_run_at or '从未'}）"
        )

    def cron_list(_args: dict[str, Any]) -> str:
        jobs = store.list()
        if not jobs:
            return "还没有定时任务。用 cron_add 创建（任务由 omni-heartbeat 进程执行）。"
        return "\n".join(_fmt(j) for j in jobs)

    def cron_add(args: dict[str, Any]) -> str:
        try:
            job = Job(
                name=str(args.get("name", "")),
                prompt=str(args.get("prompt", "")),
                every_minutes=args.get("every_minutes"),
                daily_at=str(args["daily_at"]) if args.get("daily_at") else None,
                deliver_to_qq=args.get("deliver_to_qq"),
                skills=[str(s) for s in (args.get("skills") or [])],
            )
            if job.deliver_to_qq is not None and allowed_qq_users:
                if int(job.deliver_to_qq) not in allowed_qq_users:
                    return "错误：deliver_to_qq 必须是白名单里的 QQ 号"
            job.validate()
        except (CronError, TypeError, ValueError) as exc:
            return f"错误：{exc}"
        job.next_run_at = to_iso(compute_next_run(job, dt.datetime.now().astimezone()))
        store.upsert(job)
        return (
            f"已创建任务 {job.name}（下次运行 {job.next_run_at}）。"
            "注意：任务由 omni-heartbeat 进程执行，需它在运行才会触发。"
        )

    def cron_remove(args: dict[str, Any]) -> str:
        name = str(args.get("name", ""))
        return f"已删除任务 {name}。" if store.remove(name) else f"错误：没有任务 {name}"

    registry.register(
        Tool(
            name="cron_list",
            description="列出全部定时任务（心跳任务）",
            parameters={"type": "object", "properties": {}, "required": []},
            func=cron_list,
            toolset="cron",
            risk=Risk.SAFE,
        )
    )
    registry.register(
        Tool(
            name="cron_add",
            description=(
                "创建定时任务：到点自动执行一段指令（由 omni-heartbeat 进程调度）。"
                "用户说'每天X点做Y'/'每隔N分钟做Y'时使用。"
                "every_minutes 与 daily_at 二选一；可选 deliver_to_qq 把结果发到白名单 QQ"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "任务名，如 morning-report"},
                    "prompt": {"type": "string", "description": "到点执行的指令（自包含）"},
                    "every_minutes": {"type": "integer", "description": "周期分钟数（≥10），与 daily_at 二选一"},
                    "daily_at": {"type": "string", "description": "每天定时 HH:MM（如 08:30），与 every_minutes 二选一"},
                    "deliver_to_qq": {"type": "integer", "description": "结果投递到该 QQ（须在白名单）"},
                    "skills": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "执行前注入的技能名列表（可选）",
                    },
                },
                "required": ["name", "prompt"],
            },
            func=cron_add,
            toolset="cron",
            risk=Risk.SAFE,
        )
    )
    registry.register(
        Tool(
            name="cron_remove",
            description="删除定时任务",
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string", "description": "任务名"}},
                "required": ["name"],
            },
            func=cron_remove,
            toolset="cron",
            risk=Risk.SAFE,
        )
    )
