"""定时任务工具集（toolset=cron）：agent 可自主排程（Hermes cronjob_tools 精神）。

"每天早上八点给我发B站日报"→ cron_add 一次搞定。任务由 omni-heartbeat
进程执行（工具只写 jobs.json，跨进程共享）。
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any

from ..memory.cron import CronError, Job, JobStore, compute_next_run, to_iso
from ..safety.gates import private_target_allowed
from .registry import Risk, Tool, ToolRegistry

AllowedUsers = list[int] | Callable[[], list[int]]


def register_cron_tools(
    registry: ToolRegistry, store: JobStore, allowed_qq_users: AllowedUsers
) -> None:
    """allowed_qq_users 可以是列表或提供者；传提供者时按"读时"取当前白名单。"""
    if not callable(allowed_qq_users):
        snapshot = list(allowed_qq_users)
        allowed_qq_users = lambda: list(snapshot)  # noqa: E731 —— 只是包一层取数

    def _fmt(job: Job) -> str:
        schedule = f"每 {job.every_minutes} 分钟" if job.every_minutes else f"每天 {job.daily_at}"
        state = "启用" if job.enabled else "停用"
        delivery = f" → QQ {job.deliver_to_qq}" if job.deliver_to_qq else ""
        chain = f" ← {job.context_from}" if job.context_from else ""
        return (
            f"- {job.name}：{schedule}{delivery}{chain}（{state}，下次 {job.next_run_at or '待排'}，"
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
                context_from=(
                    str(args["context_from"]).strip() if args.get("context_from") else None
                ),
            )
            if job.deliver_to_qq is not None:
                decision = private_target_allowed(allowed_qq_users(), job.deliver_to_qq)
                if not decision.allowed:
                    return f"错误：deliver_to_qq 不可用（{decision.reason}）"
            job.validate()
            job.next_run_at = to_iso(
                compute_next_run(job, dt.datetime.now().astimezone())
            )
            store.upsert(job)  # upsert 做跨任务校验：context_from 源必须已存在
        except (CronError, TypeError, ValueError) as exc:
            return f"错误：{exc}"
        return (
            f"已创建任务 {job.name}（下次运行 {job.next_run_at}）。"
            "注意：任务由 omni-heartbeat 进程执行，需它在运行才会触发。"
        )

    def cron_remove(args: dict[str, Any]) -> str:
        name = str(args.get("name", ""))
        return f"已删除任务 {name}。" if store.remove(name) else f"错误：没有任务 {name}"

    def cron_edit(args: dict[str, Any]) -> str:
        """部分更新：只改传入的字段；显式传 null 清除可选字段（deliver_to_qq/context_from）。"""
        name = str(args.get("name", ""))
        job = store.get(name)
        if job is None:
            return f"错误：没有任务 {name}"
        try:
            if "prompt" in args:
                job.prompt = str(args.get("prompt") or "")
            if "every_minutes" in args:
                job.every_minutes = args.get("every_minutes")
            if "daily_at" in args:
                raw = args.get("daily_at")
                job.daily_at = str(raw).strip() if raw else None
            if "deliver_to_qq" in args:
                job.deliver_to_qq = args.get("deliver_to_qq")
            if "skills" in args:
                job.skills = [str(s) for s in (args.get("skills") or [])]
            if "context_from" in args:
                raw = args.get("context_from")
                job.context_from = str(raw).strip() if raw else None
            if "enabled" in args:
                job.enabled = bool(args.get("enabled"))
            if job.deliver_to_qq is not None:
                decision = private_target_allowed(allowed_qq_users(), job.deliver_to_qq)
                if not decision.allowed:
                    return f"错误：deliver_to_qq 不可用（{decision.reason}）"
            job.validate()
            # 调度字段或启用状态变了才重算下次运行；纯改 prompt 不动排程
            schedule_changed = any(
                k in args for k in ("every_minutes", "daily_at", "enabled")
            )
            if schedule_changed and job.enabled:
                job.next_run_at = to_iso(
                    compute_next_run(job, dt.datetime.now().astimezone())
                )
            store.upsert(job)  # 含 context_from 源存在校验
        except (CronError, TypeError, ValueError) as exc:
            return f"错误：{exc}"
        state = "启用" if job.enabled else "停用"
        when = f"，下次运行 {job.next_run_at}" if job.enabled else ""
        return f"已更新任务 {job.name}（{state}{when}）。"

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
                    "context_from": {
                        "type": "string",
                        "description": (
                            "任务链（可选）：把另一个已存在任务的最新输出作为本次输入。"
                            "如夜间整理的输出喂给早报任务"
                        ),
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
    registry.register(
        Tool(
            name="cron_edit",
            description=(
                "修改已存在的定时任务（只传要改的字段）：prompt / every_minutes / daily_at /"
                " deliver_to_qq / skills / context_from / enabled。可选字段传 null 表示清除。"
                "修改调度字段后自动重算下次运行时间"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "要修改的任务名"},
                    "prompt": {"type": "string", "description": "新的到点执行指令"},
                    "every_minutes": {"type": "integer", "description": "新的周期分钟数（与 daily_at 二选一）"},
                    "daily_at": {"type": "string", "description": "新的每日定时 HH:MM"},
                    "deliver_to_qq": {"type": "integer", "description": "结果投递到该 QQ（null 清除）"},
                    "skills": {"type": "array", "items": {"type": "string"}, "description": "执行前注入的技能名"},
                    "context_from": {"type": "string", "description": "任务链源任务（null 清除）"},
                    "enabled": {"type": "boolean", "description": "启用/停用"},
                },
                "required": ["name"],
            },
            func=cron_edit,
            toolset="cron",
            risk=Risk.SAFE,
        )
    )
