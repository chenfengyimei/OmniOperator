"""运行与用量 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。"""
from __future__ import annotations

import datetime as dt
from typing import Any, TYPE_CHECKING

from fastapi import APIRouter, HTTPException

from ...llm.usage import MICRO
from ...memory.state import StateUnavailable

if TYPE_CHECKING:
    from ...bootstrap import Runtime


def create_runs_router(runtime: Runtime) -> APIRouter:
    router = APIRouter(tags=["runs"])

    @router.get("/api/runs")
    def run_list(session_id: int | None = None, limit: int = 20) -> dict[str, Any]:
        try:
            runs = runtime.runs.list_recent(session_id=session_id, limit=limit)
        except StateUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        return {"runs": [run.brief() for run in runs]}

    @router.get("/api/runs/{run_id}")
    def run_get(run_id: str) -> dict[str, Any]:
        record = runtime.runs.get(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail="运行不存在")
        return {"run": record.brief(), "live": runtime.active_runs.get(run_id) is not None}

    @router.get("/api/runs/{run_id}/events")
    def run_events(run_id: str, after: int = 0, limit: int = 200) -> dict[str, Any]:
        """事件续读：`after=seq` 只拿新事件，断线重连不会重放整个回合。"""
        if runtime.runs.get(run_id) is None:
            raise HTTPException(status_code=404, detail="运行不存在")
        events = runtime.runs.events(run_id, after=after, limit=limit)
        return {
            "run_id": run_id,
            "events": [event.brief() for event in events],
            "next_after": events[-1].seq if events else after,
        }

    @router.post("/api/runs/{run_id}/cancel")
    def run_cancel(run_id: str) -> dict[str, Any]:
        """取消一次运行：**协作式**，不会杀线程。

        检查点在"每次模型调用前、模型返回后、每次工具调用前"；正在等待的模型请求
        会在返回后立刻停止，正在等人工批准的会撤销待批准项。所以这里返回的
        `status` 是"已经开始取消"，真正停下以 GET /api/runs/{id} 的终态为准。
        """
        if runtime.active_runs.cancel(run_id, "用户在工作台取消"):
            return {"ok": True, "run_id": run_id, "detail": "已请求取消（协作式生效中）"}
        record = runtime.runs.get(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail="运行不存在")
        if record.finished:
            raise HTTPException(
                status_code=409, detail=f"运行已结束（{record.status}），无需取消"
            )
        raise HTTPException(
            status_code=409,
            detail="该运行不在本进程内（可能由其它进程执行），本进程无法取消",
        )

    @router.get("/api/llm/usage")
    def llm_usage(days: int = 7, limit: int = 20) -> dict[str, Any]:
        """今日费用 + 按用途汇总 + 最近调用流水。

        `limit_micro` 为 null 表示**未设上限**（只记账不拦截），不是"上限为 0"。
        `cost_micro` 为 null 表示这笔钱算不出来（单价未知），不是免费。
        """
        try:
            summary = runtime.usage.summary(days=max(1, min(days, 90)))
            today = dt.date.today().isoformat()
            day = runtime.usage.day_total(today)
            calls = [record.brief() for record in runtime.usage.recent(limit)]
        except StateUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        budget = runtime.config.llm.budget
        daily_limit = int(round(budget.daily_usd * MICRO))
        return {
            "today": {
                "date": today,
                **day,
                "limit_micro": daily_limit or None,
                "remaining_micro": max(0, daily_limit - day["used_micro"])
                if daily_limit
                else None,
            },
            "per_run_limit_micro": int(round(budget.per_run_usd * MICRO)) or None,
            "summary": summary,
            "calls": calls,
            "models": {
                model: cap.model_dump()
                for model, cap in runtime.config.llm.capabilities.items()
            },
        }

    return router
