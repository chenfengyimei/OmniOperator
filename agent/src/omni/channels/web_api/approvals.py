"""人工审批 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from fastapi import APIRouter

from .schemas import ResolveIn

if TYPE_CHECKING:
    from ...bootstrap import Runtime


def create_approvals_router(runtime: Runtime) -> APIRouter:
    router = APIRouter(tags=["approvals"])

    @router.get("/api/approvals")
    def get_approvals() -> dict[str, Any]:
        used, quota = runtime.write_budget.usage_today()
        return {
            "pending": runtime.approvals.list_pending(),
            "browser_writes": {"used": used, "max": quota},
        }

    @router.post("/api/approvals/{approval_id}/resolve")
    def resolve_approval(approval_id: str, payload: ResolveIn) -> dict[str, Any]:
        ok = runtime.approvals.resolve(
            approval_id, payload.allowed, payload.reason
        )
        return {"ok": ok}

    return router
