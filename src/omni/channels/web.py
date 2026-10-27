"""Web 工作台入口：uv run omni-web（默认 http://127.0.0.1:8000）。

职责纪律：本文件只做 HTTP 适配（路由 + 请求模型 + 静态文件挂载），
领域操作全部通过 bootstrap.Runtime 门面，不构造核心对象。
审批策略：WebApprover——safe 直通，confirm 弹工作台确认横幅（阻塞等待，
超时自动拒绝），dangerous 一律拒绝。
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..bootstrap import Runtime, build_runtime
from ..config import BILI_COOKIES_PATH, CONFIG_PATH, load_env
from ..llm.gateway import GatewayError
from ..memory.cron import compute_next_run, to_iso
from ..memory.store import PROFILE_MAX_CHARS
from ..safety.gates import WebApprover
from ..system.config_io import EDITABLE, ConfigEditError, apply_updates, safe_write
from ..system.services import ServiceError

_STATIC_DIR = Path(__file__).parent / "web_static"


class ChatIn(BaseModel):
    message: str
    session_id: int | None = None


class ResolveIn(BaseModel):
    allowed: bool
    reason: str = ""


class NapCatStartIn(BaseModel):
    qq: str = ""  # 可选：快速登录的 QQ 号


class ConfigUpdateIn(BaseModel):
    updates: dict[str, dict[str, Any]]


class ModelTestIn(BaseModel):
    model: str


class BiliCookiesIn(BaseModel):
    sessdata: str = ""
    bili_jct: str = ""
    buvid3: str = ""


class CronNameIn(BaseModel):
    name: str


class CronToggleIn(BaseModel):
    name: str
    enabled: bool


class TodoToggleIn(BaseModel):
    session_id: int
    todo_id: int
    done: bool


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

    @app.get("/api/sessions/{session_id}/todos")
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

    @app.post("/api/todos/toggle")
    def toggle_todo(payload: TodoToggleIn) -> dict[str, Any]:
        if not runtime.store.todo_set_done(
            payload.session_id, payload.todo_id, payload.done
        ):
            raise HTTPException(status_code=404, detail="待办不存在")
        return {"ok": True}

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

    @app.get("/api/skills")
    def get_skills() -> dict[str, Any]:
        return {
            "skills": [
                {
                    "name": s["name"],
                    "description": s["description"],
                    "version": s["version"],
                }
                for s in runtime.skills.list()
            ]
        }

    @app.get("/api/skills/{name}")
    def get_skill(name: str) -> dict[str, Any]:
        content = runtime.skills.view(name)
        if content is None:
            raise HTTPException(status_code=404, detail="技能不存在")
        return {"content": content}

    # ---------- 确认流（confirm 级工具的人工审批） ----------

    @app.get("/api/approvals")
    def get_approvals() -> dict[str, Any]:
        used, quota = runtime.write_budget.usage_today()
        return {
            "pending": runtime.approvals.list_pending(),
            "browser_writes": {"used": used, "max": quota},
        }

    @app.post("/api/approvals/{approval_id}/resolve")
    def resolve_approval(approval_id: str, payload: ResolveIn) -> dict[str, Any]:
        ok = runtime.approvals.resolve(
            approval_id, payload.allowed, payload.reason
        )
        return {"ok": ok}

    # ---------- 系统控制台（本地服务 / 配置，可视化运维） ----------

    @app.get("/api/system/overview")
    def system_overview() -> dict[str, Any]:
        return runtime.services.overview()

    @app.post("/api/system/services/{name}/start")
    def system_service_start(
        name: str, payload: NapCatStartIn | None = None
    ) -> dict[str, Any]:
        try:
            return runtime.services.start(name, qq=(payload.qq if payload else ""))
        except ServiceError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @app.post("/api/system/services/{name}/stop")
    def system_service_stop(name: str) -> dict[str, Any]:
        try:
            return runtime.services.stop(name)
        except ServiceError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @app.get("/api/system/napcat/qr")
    def system_napcat_qr() -> dict[str, Any]:
        return runtime.services.napcat_qr()

    @app.get("/api/system/logs/{name}")
    def system_logs(name: str, lines: int = 80) -> dict[str, Any]:
        return {"log": runtime.services.log_tail(name, min(lines, 300))}

    @app.get("/api/system/config")
    def system_config() -> dict[str, Any]:
        cfg = runtime.config
        return {
            "persona": {"name": cfg.persona.name, "system_prompt": cfg.persona.system_prompt},
            "models": {
                "chat": cfg.models.chat,
                "vision": cfg.models.vision,
                "review": cfg.models.review,
            },
            "limits": {
                "max_history_messages": cfg.limits.max_history_messages,
                "max_agent_steps": cfg.limits.max_agent_steps,
            },
            "review": {
                "enabled": cfg.review.enabled,
                "max_messages": cfg.review.max_messages,
                "steps": cfg.review.steps,
            },
            "asr": {
                "model": cfg.asr.model,
                "device": cfg.asr.device,
                "compute_type": cfg.asr.compute_type,
            },
            "qq": {
                "enabled": cfg.qq.enabled,
                "ws_url": cfg.qq.ws_url,
                "access_token": cfg.qq.access_token,
                "allowed_users": cfg.qq.allowed_users,
                "allowed_groups": cfg.qq.allowed_groups,
                "reply_delay_s": cfg.qq.reply_delay_s,
            },
            "browser": {
                "headless": cfg.browser.headless,
                "slow_mo_ms": cfg.browser.slow_mo_ms,
                "max_writes_per_day": cfg.browser.max_writes_per_day,
                "approval_timeout_s": cfg.browser.approval_timeout_s,
            },
            "editable": sorted(f"{s}.{k}" for s, k in EDITABLE),
        }

    @app.post("/api/system/config")
    def system_config_save(payload: ConfigUpdateIn) -> dict[str, Any]:
        try:
            text = CONFIG_PATH.read_text(encoding="utf-8")
            new_text = apply_updates(text, payload.updates)
            safe_write(CONFIG_PATH, new_text)
        except (ConfigEditError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {
            "ok": True,
            "hint": "已写入 config.yaml；正在运行的服务重启后生效（见服务面板）",
        }

    # ---------- B站 Cookie（字幕/音频流需要登录态） ----------

    @app.get("/api/system/bili-cookies")
    def bili_cookies_status() -> dict[str, Any]:
        keys = {"SESSDATA": False, "bili_jct": False, "buvid3": False}
        try:
            data = json.loads(BILI_COOKIES_PATH.read_text(encoding="utf-8"))
            for k in keys:
                keys[k] = bool(str(data.get(k, "")).strip())
        except (OSError, json.JSONDecodeError):
            pass
        return {"configured": any(keys.values()), "keys": keys, "path": str(BILI_COOKIES_PATH)}

    @app.post("/api/system/bili-cookies")
    def save_bili_cookies(payload: BiliCookiesIn) -> dict[str, Any]:
        data: dict[str, str] = {}
        for name, value in (
            ("SESSDATA", payload.sessdata),
            ("bili_jct", payload.bili_jct),
            ("buvid3", payload.buvid3),
        ):
            value = value.strip()
            if value:
                data[name] = value
        if not data:
            raise HTTPException(status_code=400, detail="至少要填一项 Cookie")
        BILI_COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
        BILI_COOKIES_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {"ok": True, "saved": list(data), "path": str(BILI_COOKIES_PATH)}

    # ---------- 模型连通性测试 ----------

    @app.post("/api/system/model-test")
    def model_test(payload: ModelTestIn) -> dict[str, Any]:
        try:
            reply = runtime.gateway.complete(
                [{"role": "user", "content": "这是一次连通性测试，请只回复：OK"}],
                model=payload.model,
            )
        except GatewayError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "reply": reply.text[:120]}

    # ---------- 心跳任务管理（可视化） ----------

    @app.get("/api/system/cron")
    def cron_list() -> dict[str, Any]:
        jobs = []
        for j in runtime.cron_jobs.list():
            jobs.append(
                {
                    "name": j.name,
                    "prompt": j.prompt,
                    "schedule": (
                        f"每 {j.every_minutes} 分钟"
                        if j.every_minutes
                        else f"每天 {j.daily_at}"
                    ),
                    "deliver_to_qq": j.deliver_to_qq,
                    "context_from": j.context_from,
                    "enabled": j.enabled,
                    "next_run_at": j.next_run_at,
                    "last_run_at": j.last_run_at,
                    "last_result": j.last_result,
                }
            )
        return {"jobs": jobs}

    @app.post("/api/system/cron/toggle")
    def cron_toggle(payload: CronToggleIn) -> dict[str, Any]:
        job = runtime.cron_jobs.get(payload.name)
        if job is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        job.enabled = payload.enabled
        if payload.enabled:
            job.next_run_at = to_iso(compute_next_run(job, dt.datetime.now().astimezone()))
        runtime.cron_jobs.upsert(job, validate=False)
        return {"ok": True, "enabled": job.enabled, "next_run_at": job.next_run_at}

    @app.post("/api/system/cron/delete")
    def cron_delete(payload: CronNameIn) -> dict[str, Any]:
        return {"ok": runtime.cron_jobs.remove(payload.name)}

    app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="ui")
    return app


def main() -> None:
    load_env()
    runtime = build_runtime()
    # Web 上下文的确认流：confirm 工具 → 工作台横幅（agent 线程阻塞等待用户点击）
    runtime.agent.approver = WebApprover(runtime.approvals)
    app = create_app(runtime)
    host = os.environ.get("OMNI_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("OMNI_WEB_PORT", "8000"))
    print(f"OmniOperator 工作台已启动：http://{host}:{port}  （Ctrl+C 退出）")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
