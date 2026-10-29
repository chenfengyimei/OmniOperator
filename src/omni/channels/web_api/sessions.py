"""会话与待办 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。"""
from __future__ import annotations

import datetime as dt
import json
import math
from typing import Any, TYPE_CHECKING

from fastapi import APIRouter, HTTPException

from .schemas import TodoToggleIn
from ...memory.store import PROFILE_MAX_CHARS

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
        max_history = runtime.config.limits.max_history_messages
        history = runtime.store.recent_messages_ex(sid, max_history)
        summary = runtime.store.active_summary(sid)

        # Context is estimated from the exact material assembled for the next
        # turn. Chinese characters count roughly as one token, latin text as
        # ~3.5 characters per token; the UI labels this value as an estimate.
        def estimate(text: str) -> int:
            if not text:
                return 0
            cjk = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
            return max(1, cjk + math.ceil((len(text) - cjk) / 3.5))

        now = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %A")
        base_prompt = runtime.config.persona.system_prompt.replace("{now}", now)
        resident_block = runtime.resident.block() if runtime.resident is not None else ""
        profile_block = runtime.store.profile_block()
        tool_specs = json.dumps(runtime.registry.specs(), ensure_ascii=False, separators=(",", ":"))
        conversation_text = ((summary[3] if summary else "") +
                             "\n".join(content for _, _, content in history))
        segments = [
            {"key": "system", "label": "系统提示", "tokens": estimate(base_prompt)},
            {"key": "tools", "label": "工具规格", "tokens": estimate(tool_specs)},
            {"key": "memory", "label": "记忆", "tokens": estimate(resident_block + profile_block)},
            {"key": "conversation", "label": "对话", "tokens": estimate(conversation_text)},
        ]
        used_tokens = sum(item["tokens"] for item in segments)
        max_tokens = runtime.config.llm.capability(runtime.agent.model).context_tokens
        return {
            "persona": runtime.config.persona.name,
            "model": runtime.agent.model,
            "session_id": sid,
            "tools": tools,
            "toolsets": runtime.registry.toolsets(),
            "fts_enabled": runtime.store.fts_enabled,
            "review_enabled": runtime.reviewer is not None,
            "profile": {
                "used": runtime.store.profile_usage_chars(),
                "max": PROFILE_MAX_CHARS,
            },
            "context": {
                "estimated": True,
                "used_tokens": used_tokens,
                "max_tokens": max_tokens,
                "percent": round(used_tokens / max_tokens * 100, 1) if max_tokens else None,
                "segments": segments,
                "message_count": runtime.store.message_count(sid),
                "history_count": len(history),
                "max_history_messages": max_history,
            },
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
