"""会话与待办 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException

from ...memory.store import PROFILE_MAX_CHARS
from .schemas import TodoToggleIn

if TYPE_CHECKING:
    from ...bootstrap import Runtime


def create_sessions_router(runtime: Runtime) -> APIRouter:
    router = APIRouter(tags=["sessions"])

    @router.get("/api/state")
    def get_state() -> dict[str, Any]:
        tools = []
        for name in runtime.registry.names():
            tool = runtime.registry.get(name)
            if tool is not None:
                tools.append(
                    {"name": tool.name, "toolset": tool.toolset, "risk": tool.risk.value}
                )
        sid = runtime.agent.session_id
        context = runtime.agent.context_overview(sid)
        context.pop('system_prompt',None)  # 常规状态只返回统计；完整固定提示由上下文页按需读取。
        return {
            "persona": runtime.config.persona.name,
            "model": runtime.agent.model,
            "session_id": sid,
            "tools": tools,
            "toolsets": runtime.registry.toolsets(),
            "fts_enabled": runtime.store.fts_enabled,
            "review_enabled": runtime.reviewer is not None,
            "profile": {
                "used": (
                    runtime.owner_memory.profile_usage_chars()
                    if runtime.owner_memory is not None
                    else runtime.store.profile_usage_chars()
                ),
                "max": PROFILE_MAX_CHARS,
            },
            "context": context,
        }

    @router.get("/api/sessions")
    def get_sessions() -> dict[str, Any]:
        return {
            "sessions": [
                {
                    "id": sid,
                    "title": title,
                    "created_at": created,
                    "active": sid == runtime.agent.session_id,
                }
                for sid, title, created in runtime.store.list_sessions(50)
            ]
        }

    @router.post("/api/sessions")
    def create_session() -> dict[str, Any]:
        return {"session_id": runtime.new_session()}

    @router.post("/api/sessions/{session_id}/select")
    def select_session(session_id: int) -> dict[str, Any]:
        if not runtime.use_session(session_id):
            raise HTTPException(status_code=404, detail="会话不存在")
        return {"session_id": session_id}

    @router.get("/api/sessions/{session_id}/messages")
    def get_messages(session_id: int, limit: int = 300) -> dict[str, Any]:
        if not runtime.store.session_exists(session_id):
            raise HTTPException(status_code=404, detail="会话不存在")
        rows = runtime.store.list_messages(session_id, min(limit, 1000))
        return {
            "messages": [
                {"role": role, "content": content, "created_at": created}
                for role, content, created in rows
            ]
        }

    @router.get("/api/sessions/{session_id}/todos")
    def get_todos(session_id: int) -> dict[str, Any]:
        """会话待办（todo 工具与工作台同源）。"""
        if not runtime.store.session_exists(session_id):
            raise HTTPException(status_code=404, detail="会话不存在")
        return {
            "todos": [
                {"id": tid, "content": content, "done": bool(done)}
                for tid, content, done in runtime.store.todo_list(session_id)
            ]
        }

    @router.post("/api/todos/toggle")
    def toggle_todo(payload: TodoToggleIn) -> dict[str, Any]:
        if not runtime.store.todo_set_done(
            payload.session_id, payload.todo_id, payload.done
        ):
            raise HTTPException(status_code=404, detail="待办不存在")
        return {"ok": True}

    return router
