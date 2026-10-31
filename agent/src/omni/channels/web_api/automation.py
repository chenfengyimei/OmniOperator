"""调度与可靠投递 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。"""
from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException

from ...memory.cron import CronError, Job, compute_next_run, to_iso
from ...memory.state import StateConflict, StateUnavailable
from ...safety.gates import private_target_allowed
from .schemas import CronCreateIn, CronNameIn, CronToggleIn, CronUpdateIn

if TYPE_CHECKING:
    from ...bootstrap import Runtime


def create_automation_router(runtime: Runtime) -> APIRouter:
    router = APIRouter(tags=["automation"])

    @router.post("/api/system/cron/create")
    def cron_create(payload: CronCreateIn) -> dict[str, Any]:
        """工作台直建任务：与 agent 的 cron_add 走同一套 JobStore 校验。"""
        job = Job(
            name=payload.name.strip(),
            prompt=payload.prompt.strip(),
            every_minutes=payload.every_minutes,
            daily_at=(payload.daily_at.strip() if payload.daily_at else None),
            timezone=payload.timezone,
            context_max_age_minutes=payload.context_max_age_minutes,
            deliver_to_qq=payload.deliver_to_qq,
            skills=[s.strip() for s in payload.skills if s.strip()],
            context_from=(
                payload.context_from.strip() if payload.context_from else None
            ),
        )
        try:
            job.validate()
            if job.deliver_to_qq is not None:
                # 与工具/投递同一判定：空白名单不是"不限"，撤销后立刻建不出新任务
                decision = private_target_allowed(
                    runtime.qq_policy().allowed_users, job.deliver_to_qq
                )
                if not decision.allowed:
                    raise CronError(f"deliver_to_qq 不可用（{decision.reason}）")
            job.next_run_at = to_iso(
                compute_next_run(job, dt.datetime.now().astimezone())
            )
            runtime.cron_jobs.upsert(job)  # 含 context_from 源存在校验
        except CronError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except StateConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except StateUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        return {"ok": True, "name": job.name, "next_run_at": job.next_run_at}

    @router.get("/api/system/cron")
    def cron_list() -> dict[str, Any]:
        # 读不出来就报 503：返回空列表会让界面显示"还没有任务"，那是假消息
        try:
            stored = runtime.cron_jobs.list()
        except StateUnavailable as exc:
            raise HTTPException(status_code=503, detail=f"任务状态不可用：{exc}")
        jobs = []
        for j in stored:
            jobs.append(
                {
                    "name": j.name,
                    "prompt": j.prompt,
                    "schedule": (
                        f"每 {j.every_minutes} 分钟"
                        if j.every_minutes
                        else f"每天 {j.daily_at}"
                    ),
                    "every_minutes": j.every_minutes,
                    "daily_at": j.daily_at,
                    "timezone": j.timezone,
                    "context_max_age_minutes": j.context_max_age_minutes,
                    "deliver_to_qq": j.deliver_to_qq,
                    "context_from": j.context_from,
                    "skills": j.skills,
                    "enabled": j.enabled,
                    "revision": j.revision,
                    "next_run_at": j.next_run_at,
                    "last_run_at": j.last_run_at,
                    "last_result": j.last_result,
                }
            )
        return {"jobs": jobs}

    @router.post("/api/system/cron/update")
    def cron_update(payload: CronUpdateIn) -> dict[str, Any]:
        """编辑任务定义：与创建同一套校验；expected_revision 旧表单提交返回 409。

        字段语义用「是否出现在请求里」区分：没传 = 不改；显式 null = 清除
        （前端编辑表单把空输入框提交成 null，才能把 deliver_to_qq 等可选项删掉）。
        """
        try:
            job = runtime.cron_jobs.get(payload.name)
            if job is None:
                raise HTTPException(status_code=404, detail="任务不存在")
            fields = payload.model_dump(exclude={"name", "expected_revision"})
            touched = {
                k: v for k, v in fields.items() if k in payload.model_fields_set
            }
            if not touched:
                raise HTTPException(status_code=400, detail="没有要修改的字段")
            for key, value in touched.items():
                setattr(job, key, value)
            if job.deliver_to_qq is not None:
                # 与创建/工具/投递同一判定：空白名单不是"不限"
                decision = private_target_allowed(
                    runtime.qq_policy().allowed_users, job.deliver_to_qq
                )
                if not decision.allowed:
                    raise CronError(f"deliver_to_qq 不可用（{decision.reason}）")
            job.validate()
            schedule_changed = any(
                k in touched for k in ("every_minutes", "daily_at", "enabled", "timezone")
            )
            if schedule_changed and job.enabled:
                job.next_run_at = to_iso(
                    compute_next_run(job, dt.datetime.now().astimezone())
                )
            runtime.cron_jobs.upsert(
                job, expected_revision=payload.expected_revision
            )
        except CronError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except StateConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except StateUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        return {
            "ok": True,
            "name": job.name,
            "enabled": job.enabled,
            "next_run_at": job.next_run_at,
        }

    @router.post("/api/system/cron/toggle")
    def cron_toggle(payload: CronToggleIn) -> dict[str, Any]:
        try:
            job = runtime.cron_jobs.get(payload.name)
            if job is None:
                raise HTTPException(status_code=404, detail="任务不存在")
            job.enabled = payload.enabled
            if payload.enabled:
                job.next_run_at = to_iso(
                    compute_next_run(job, dt.datetime.now().astimezone())
                )
            runtime.cron_jobs.upsert(job, validate=False)
        except StateUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        return {"ok": True, "enabled": job.enabled, "next_run_at": job.next_run_at}

    @router.post("/api/system/cron/delete")
    def cron_delete(payload: CronNameIn) -> dict[str, Any]:
        try:
            return {"ok": runtime.cron_jobs.remove(payload.name)}
        except StateUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc))

    @router.get("/api/system/cron/history")
    def cron_history(limit: int = 50) -> dict[str, Any]:
        """执行账本：成功与失败都记账，新的在前。"""
        try:
            return {"history": runtime.cron_ledger.recent(min(limit, 100))}
        except StateUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc))

    @router.get("/api/system/cron/outbox")
    def cron_outbox() -> dict[str, Any]:
        """待投递队列：活跃（待补投/结果未知）在前，终态最近记录在后。"""
        if runtime.outbox is None:
            raise HTTPException(status_code=503, detail="投递队列不可用")
        try:
            items = runtime.outbox.list(limit=50)
        except StateUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        for item in items:
            # 完整内容已在会话里；面板只给预览，避免一屏刷满长报告
            item["preview"] = (item.pop("content") or "")[:80]
            item.pop("claim_token", None)
        return {"items": items}

    @router.post("/api/system/cron/outbox/{entry_id}/retry")
    def cron_outbox_retry(entry_id: int) -> dict[str, Any]:
        """手动重排（unknown/dead/discarded → pending）：心跳下一轮补投。"""
        if runtime.outbox is None:
            raise HTTPException(status_code=503, detail="投递队列不可用")
        try:
            if not runtime.outbox.retry(entry_id):
                raise HTTPException(status_code=404, detail="条目不存在或已投递")
        except StateUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        return {"ok": True, "message": "已重新排队，QQ 连接时由心跳自动补投"}

    @router.post("/api/system/cron/outbox/{entry_id}/discard")
    def cron_outbox_discard(entry_id: int) -> dict[str, Any]:
        """丢弃一条待投递（已投递的不能丢——那就是历史了）。"""
        if runtime.outbox is None:
            raise HTTPException(status_code=503, detail="投递队列不可用")
        try:
            if not runtime.outbox.discard(entry_id):
                raise HTTPException(status_code=404, detail="条目不存在或已投递")
        except StateUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        return {"ok": True}

    return router
