"""记忆与档案 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from fastapi import APIRouter, HTTPException

from .schemas import ProfileLineIn, ResidentUpdateIn
from ...memory.store import PROFILE_MAX_CHARS

if TYPE_CHECKING:
    from ...bootstrap import Runtime


def create_memory_router(runtime: Runtime) -> APIRouter:
    router = APIRouter(tags=["memory"])

    @router.get("/api/facts")
    def get_facts(q: str = "", limit: int = 50) -> dict[str, Any]:
        return {"facts": runtime.store.search_facts(q, limit=min(limit, 100))}

    @router.get("/api/facts/detail")
    def get_facts_detail(limit: int = 200) -> dict[str, Any]:
        """带来源/作用域/时间的事实列表（D12）：让人能判断"这条是哪来的"。"""
        return {"facts": runtime.store.fact_rows(limit=min(limit, 500))}

    @router.delete("/api/facts/{fact_id}")
    def delete_fact(fact_id: int) -> dict[str, Any]:
        """用户主动删除：与工具走的同一条遗忘路径（留墓碑 + 摘要失效）。"""
        if not runtime.store.forget_fact(fact_id, reason="工作台手动删除"):
            raise HTTPException(status_code=404, detail="记忆不存在")
        return {"ok": True, "fact_id": fact_id, "detail": "已删除，并标记为不再学习"}

    @router.get("/api/memory/status")
    def memory_status() -> dict[str, Any]:
        """记忆写入额度、review 队列与最近整理记录（D12 可观测面）。"""
        used, limit = (
            runtime.memory_guard.usage_today()
            if runtime.memory_guard is not None
            else (0, 0)
        )
        reviewer = runtime.reviewer
        reviews: list[dict[str, Any]] = []
        sid = runtime.agent.session_id
        if runtime.store.session_exists(sid):
            reviews = runtime.store.review_history(sid, limit=5)
        return {
            "writes": {"used": used, "limit": limit or None},
            "review": {
                "enabled": reviewer is not None,
                "active": reviewer.active if reviewer is not None else 0,
                "queue_depth": reviewer.queue_depth if reviewer is not None else 0,
                "stats": dict(reviewer.stats) if reviewer is not None else {},
                "recent": reviews,
            },
        }

    @router.get("/api/profile")
    def get_profile() -> dict[str, Any]:
        return {
            "lines": [
                {"id": lid, "content": content}
                for lid, content in runtime.store.profile_lines()
            ],
            "usage": runtime.store.profile_usage_chars(),
            "max": PROFILE_MAX_CHARS,
        }

    @router.put("/api/profile/line")
    def profile_line_update(payload: ProfileLineIn) -> dict[str, Any]:
        content = payload.content.strip()
        if not content:
            raise HTTPException(status_code=400, detail="内容不能为空")
        if not runtime.store.profile_edit(payload.line_id, content):
            raise HTTPException(status_code=404, detail="档案行不存在")
        return {"ok": True}

    @router.delete("/api/profile/line/{line_id}")
    def profile_line_delete(line_id: int) -> dict[str, Any]:
        if not runtime.store.profile_delete(line_id):
            raise HTTPException(status_code=404, detail="档案行不存在")
        return {"ok": True}

    @router.get("/api/resident")
    def get_resident() -> dict[str, Any]:
        return {
            "usage": runtime.resident.usage(),
            "limit": runtime.resident.limit,
            "entries": [
                {"id": i, "content": c} for i, c in runtime.resident.entries()
            ],
        }

    @router.post("/api/resident/update")
    def resident_update(payload: ResidentUpdateIn) -> dict[str, Any]:
        op = payload.operation
        # 来源记为工作台：修订历史里能分清"用户手改"与"模型写入"
        if op == "add":
            message = runtime.resident.add(payload.content, author="user", source="workbench")
        elif op == "replace":
            message = runtime.resident.replace(
                int(payload.id or 0), payload.content, author="user", source="workbench"
            )
        elif op == "remove":
            message = runtime.resident.remove(
                int(payload.id or 0), author="user", source="workbench"
            )
        else:
            raise HTTPException(status_code=400, detail=f"未知操作 {op}")
        if message.startswith("错误"):
            # 限额/注入/编号错误原样回显（含合并指引，前端展示给用户）
            raise HTTPException(status_code=400, detail=message)
        return {"ok": True, "message": message}

    @router.get("/api/reviews")
    def get_reviews(limit: int = 20) -> dict[str, Any]:
        rows = runtime.store.list_all_reviews(min(limit, 100))
        return {
            "reviews": [
                {"session_id": sid, "outcome": outcome, "created_at": created}
                for sid, outcome, created in rows
            ]
        }

    @router.get("/api/memory/retrieval")
    def retrieval_status() -> dict[str, Any]:
        if runtime.retrieval is None:
            return {"enabled": False, "mode": "fts"}
        return {"enabled": True, "mode": "hybrid", **runtime.retrieval.status()}

    return router
