"""Web 工作台入口：uv run omni-web（默认 http://127.0.0.1:8000）。

职责纪律：本文件只做 HTTP 适配（路由 + 请求模型 + 静态文件挂载），
领域操作全部通过 bootstrap.Runtime 门面，不构造核心对象。
审批策略：web 上下文用 DenyApprover（fail-closed）——safe 工具直通，
confirm/dangerous 在 P3 引入 UI 确认流之前一律拒绝。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..bootstrap import Runtime, build_runtime
from ..config import load_env
from ..llm.gateway import GatewayError
from ..memory.store import PROFILE_MAX_CHARS
from ..safety.gates import DenyApprover

_STATIC_DIR = Path(__file__).parent / "web_static"


class ChatIn(BaseModel):
    message: str
    session_id: int | None = None


def create_app(runtime: Runtime) -> FastAPI:
    """把 Runtime 暴露成 REST + 静态工作台（测试用 TestClient 直接调）。"""
    app = FastAPI(title="OmniOperator Workbench")

    @app.get("/api/state")
    def get_state() -> dict[str, Any]:
        tools = []
        for name in runtime.registry.names():
            tool = runtime.registry.get(name)
            if tool is not None:
                tools.append(
                    {"name": tool.name, "toolset": tool.toolset, "risk": tool.risk.value}
                )
        return {
            "persona": runtime.config.persona.name,
            "model": runtime.agent.model,
            "session_id": runtime.agent.session_id,
            "tools": tools,
            "toolsets": runtime.registry.toolsets(),
            "fts_enabled": runtime.store.fts_enabled,
            "review_enabled": runtime.reviewer is not None,
            "profile": {
                "used": runtime.store.profile_usage_chars(),
                "max": PROFILE_MAX_CHARS,
            },
        }

    @app.get("/api/sessions")
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

    @app.post("/api/sessions")
    def create_session() -> dict[str, Any]:
        return {"session_id": runtime.new_session()}

    @app.post("/api/sessions/{session_id}/select")
    def select_session(session_id: int) -> dict[str, Any]:
        if not runtime.use_session(session_id):
            raise HTTPException(status_code=404, detail="会话不存在")
        return {"session_id": session_id}

    @app.get("/api/sessions/{session_id}/messages")
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

    @app.post("/api/chat")
    def chat(payload: ChatIn) -> dict[str, Any]:
        if payload.session_id is not None and payload.session_id != runtime.agent.session_id:
            if not runtime.use_session(payload.session_id):
                raise HTTPException(status_code=404, detail="会话不存在")
        try:
            answer = runtime.agent.chat(payload.message)
        except GatewayError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        runtime.spawn_review()  # 与 CLI 一致：回合后异步记忆整理
        return {"answer": answer, "session_id": runtime.agent.session_id}

    @app.get("/api/facts")
    def get_facts(q: str = "", limit: int = 50) -> dict[str, Any]:
        return {"facts": runtime.store.search_facts(q, limit=min(limit, 100))}

    @app.get("/api/profile")
    def get_profile() -> dict[str, Any]:
        return {
            "lines": [
                {"id": lid, "content": content}
                for lid, content in runtime.store.profile_lines()
            ],
            "usage": runtime.store.profile_usage_chars(),
            "max": PROFILE_MAX_CHARS,
        }

    @app.get("/api/reviews")
    def get_reviews(limit: int = 20) -> dict[str, Any]:
        rows = runtime.store.list_all_reviews(min(limit, 100))
        return {
            "reviews": [
                {"session_id": sid, "outcome": outcome, "created_at": created}
                for sid, outcome, created in rows
            ]
        }

    app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="ui")
    return app


def main() -> None:
    load_env()
    runtime = build_runtime(approver=DenyApprover())
    app = create_app(runtime)
    host = os.environ.get("OMNI_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("OMNI_WEB_PORT", "8000"))
    print(f"OmniOperator 工作台已启动：http://{host}:{port}  （Ctrl+C 退出）")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
