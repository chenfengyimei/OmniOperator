"""Web 工作台入口：uv run omni-web（默认 http://127.0.0.1:8000）。

职责纪律：本文件只做 HTTP 适配（路由 + 请求模型 + 静态文件挂载），
领域操作全部通过 bootstrap.Runtime 门面，不构造核心对象。
审批策略：WebApprover——safe 直通，confirm 弹工作台确认横幅（阻塞等待，
超时自动拒绝），dangerous 一律拒绝。

身份（A07）：除配对接口外，所有 `/api/*` 都要求配对后下发的 HttpOnly 会话 cookie；
Host/Origin/Sec-Fetch-Site 三级校验挡住跨站与 DNS rebinding；终端 WS 握手同样要会话。
默认只监听回环地址，非回环必须显式开启（`OMNI_WEB_ALLOW_REMOTE=1`）。
"""
from __future__ import annotations

import asyncio
import contextlib
import functools
import datetime as dt
import json
import os
from io import BytesIO
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from PIL import Image

from ..bootstrap import Runtime, build_runtime
from ..config import load_env
from ..core.session import SessionBusy
from ..llm.gateway import GatewayError
from ..llm.usage import MICRO
from ..memory.cron import CronError, Job, compute_next_run, to_iso
from ..memory.store import PROFILE_MAX_CHARS
from ..safety.gates import WebApprover, private_target_allowed
from ..memory.state import StateConflict, StateUnavailable
from ..system.config_io import EDITABLE, ConfigEditError, apply_updates, safe_write
from ..system.services import ServiceError
from .terminal import TerminalManager
from .web_auth import COOKIE_NAME, LOOPBACK_HOSTS, WebAuth

_STATIC_DIR = Path(__file__).parent / "web_static"

# 只回"是否已配置"的密钥字段：接口不回显明文（A07）
_SECRET_KEYS = {("qq", "access_token")}


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


class CronCreateIn(BaseModel):
    name: str
    prompt: str
    every_minutes: int | None = None
    daily_at: str | None = None
    deliver_to_qq: int | None = None
    skills: list[str] = []
    context_from: str | None = None


class TodoToggleIn(BaseModel):
    session_id: int
    todo_id: int
    done: bool


class AndroidTapIn(BaseModel):
    x: int
    y: int
    stream_w: int
    stream_h: int


class AndroidElementIn(BaseModel):
    idx: int


class AndroidKeyIn(BaseModel):
    keycode: str


class ProfileLineIn(BaseModel):
    line_id: int
    content: str


class ResidentUpdateIn(BaseModel):
    operation: str  # add | replace | remove
    id: int | None = None
    content: str = ""


class PairIn(BaseModel):
    code: str


def _set_session_cookie(response, value: str, max_age: int):
    response.set_cookie(
        COOKIE_NAME,
        value,
        max_age=max_age,
        httponly=True,  # 前端脚本读不到，XSS 也偷不走
        samesite="strict",  # 跨站请求不携带 → CSRF 第一层
        path="/",
    )
    return response


def _create_auth(runtime: Runtime) -> WebAuth:
    environ_host = os.environ.get("OMNI_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("OMNI_WEB_PORT", "8000"))
    return WebAuth(runtime.data_dir, bind_host=environ_host, port=port)


def _normalize_secret_updates(
    updates: dict[str, dict[str, Any]]
) -> tuple[dict[str, dict[str, Any]], bool]:
    """密钥字段三态归一，返回 (过滤后的更新, 是否触及密钥字段)。

    - 键缺省 / 空串 → 保留旧值（前端拿到的是掩码，空值不能等于"清空"）
    - `null` → 显式清除（写空串）
    - 非空字符串 → 显式替换
    """
    cleaned: dict[str, dict[str, Any]] = {}
    touched = False
    for section, keys in (updates or {}).items():
        for key, value in (keys or {}).items():
            if (section, key) not in _SECRET_KEYS:
                cleaned.setdefault(section, {})[key] = value
                continue
            touched = True
            if value is None:
                cleaned.setdefault(section, {})[key] = ""
            elif isinstance(value, str) and not value.strip():
                continue  # 保留旧值
            else:
                cleaned.setdefault(section, {})[key] = value
    return {s: k for s, k in cleaned.items() if k}, touched


def create_app(runtime: Runtime) -> FastAPI:
    """把 Runtime 暴露成 REST + 静态工作台（测试用 TestClient 直接调）。"""
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            # 先收浏览器（有界等待：它可能正卡在一个动作上），再收 MCP 子进程
            note = await asyncio.to_thread(runtime.close_browser, wait_s=8.0)
            if "已关闭" not in note and "未启动" not in note:
                print(f"[工作台] 关闭浏览器：{note}", flush=True)
            await asyncio.to_thread(runtime.close_mcp)

    app = FastAPI(title="OmniOperator Workbench", lifespan=lifespan)
    terminal_manager = TerminalManager()
    auth = _create_auth(runtime)
    app.state.web_auth = auth  # 供启动脚本打印配对链接 / 测试读取配对码

    @app.middleware("http")
    async def identity_guard(request: Request, call_next):
        """统一身份闸门：Host → CSRF → 会话。静态资源不走这里。"""
        path = request.url.path
        if not path.startswith("/api/"):
            if not auth.host_ok(request.headers.get("host")):
                return JSONResponse({"detail": "Host 不被允许"}, status_code=400)
            return await call_next(request)
        rejection = auth.check_request(
            method=request.method,
            path=path,
            host=request.headers.get("host"),
            origin=request.headers.get("origin"),
            fetch_site=request.headers.get("sec-fetch-site"),
            cookie=request.cookies.get(COOKIE_NAME),
        )
        if rejection is not None:
            status = 401 if rejection.code == "auth_required" else 403
            return JSONResponse(
                {"detail": rejection.detail, "code": rejection.code}, status_code=status
            )
        return await call_next(request)

    # ---------- 配对（唯一无需会话的写接口，本身也拒绝跨站） ----------

    @app.get("/api/auth/status")
    def auth_status(request: Request) -> dict[str, Any]:
        return {
            "auth_required": True,
            "paired": auth.verify_session(request.cookies.get(COOKIE_NAME)),
            "session_days": auth.session_ttl // 86400,
        }

    @app.post("/api/auth/pair")
    def auth_pair(payload: PairIn, request: Request) -> JSONResponse:
        # 身份建立接口自己不能允许跨站配对：Origin 必须是本机
        if not auth.origin_ok(request.headers.get("origin")):
            return JSONResponse(
                {"detail": "拒绝跨站配对", "code": "origin_rejected"}, status_code=403
            )
        result = auth.pair(payload.code)
        if not isinstance(result, str):
            return JSONResponse(
                {"detail": result.detail, "code": result.code}, status_code=403
            )
        response = JSONResponse({"ok": True, "session_days": auth.session_ttl // 86400})
        return _set_session_cookie(response, result, auth.session_ttl)

    @app.post("/api/auth/logout")
    def auth_logout() -> JSONResponse:
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    @app.get("/pair/{code}")
    def pair_link(code: str, request: Request):
        """控制台配对链接：设置会话 cookie 后跳回工作台（前端不需要改）。

        拒绝跨站导航（`Sec-Fetch-Site: cross-site`）——否则恶意页面可以把用户
        "导航"到这个链接来帮忙完成配对。
        """
        fetch_site = (request.headers.get("sec-fetch-site") or "").strip().lower()
        if fetch_site and fetch_site not in ("same-origin", "none"):
            return HTMLResponse(
                "<h3>拒绝跨站配对</h3><p>请直接打开控制台输出的配对链接。</p>",
                status_code=403,
            )
        result = auth.pair(code)
        if not isinstance(result, str):
            return HTMLResponse(
                f"<h3>配对失败</h3><p>{result.detail}</p>"
                "<p>配对链接在控制台（omni-web 窗口）里，重新打开即可。</p>",
                status_code=403,
            )
        response = RedirectResponse("/", status_code=303)
        return _set_session_cookie(response, result, auth.session_ttl)

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

    async def _run_turn(sid: int, message: str):
        """跑一个回合并返回 TurnResult（run_id 给界面查事件流）。

        仍然保留"同一会话串行 + 跨进程租约"：run 是账本，不是并发控制手段。
        """
        async with runtime.session_gate.turn(sid):
            with runtime.session_leases.hold(sid):
                return await asyncio.to_thread(
                    functools.partial(
                        runtime.agent.run_turn,
                        message,
                        session_id=sid,
                        channel="web",
                        principal="owner",
                    )
                )

    @app.post("/api/chat")
    async def chat(payload: ChatIn) -> dict[str, Any]:
        """一个回合只认请求里带来的会话（A06）。

        以前这里是"改 agent.session_id → chat() → 再读 agent.session_id"，两个并发请求
        会串话：回答、落库历史和回合后 review 都可能算到别人的会话上。现在：
        - 会话 id 在请求开始时确定，之后**不再读也不写**进程级的"当前会话"；
        - 同一会话串行（进程内闸门）+ 同一会话同一时刻只允许一个进程（state.db 租约）；
        - 排队满返回 429，不把请求无限挂着；
        - review 明确带上这个 sid，不回头猜"当前是谁"。
        """
        sid = payload.session_id if payload.session_id is not None else runtime.agent.session_id
        if not runtime.store.session_exists(sid):
            raise HTTPException(status_code=404, detail="会话不存在")
        try:
            turn = await _run_turn(sid, payload.message)
        except SessionBusy as exc:
            raise HTTPException(status_code=429, detail=str(exc))
        except GatewayError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        runtime.spawn_review(sid)  # 与 CLI 一致：回合后异步记忆整理（会话由本回合固定）
        return {
            "answer": turn.answer,
            "session_id": sid,
            "run_id": turn.run_id,
            "status": turn.status,
            "error_code": turn.error_code,
            "cost_micro": turn.cost_micro,
            "budget": turn.budget,
        }

    @app.post("/api/chat/stream")
    async def chat_stream(payload: ChatIn, request: Request) -> StreamingResponse:
        """同 /api/chat，但把增量文本按 SSE 推给浏览器（D10）。

        纪律与一次性接口一致：会话由请求固定、同会话串行、跨进程租约；
        另外多一条——**浏览器断开就取消这次运行**，不让页面关了还在后台烧钱。
        """
        sid = payload.session_id if payload.session_id is not None else runtime.agent.session_id
        if not runtime.store.session_exists(sid):
            raise HTTPException(status_code=404, detail="会话不存在")
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def on_delta(text: str) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, {"type": "delta", "text": text})

        async def worker() -> None:
            try:
                async with runtime.session_gate.turn(sid):
                    with runtime.session_leases.hold(sid):
                        turn = await asyncio.to_thread(
                            functools.partial(
                                runtime.agent.run_turn,
                                payload.message,
                                session_id=sid,
                                channel="web",
                                principal="owner",
                                on_delta=on_delta,
                            )
                        )
                runtime.spawn_review(sid)
                await queue.put(
                    {
                        "type": "done",
                        "run_id": turn.run_id,
                        "status": turn.status,
                        "error_code": turn.error_code,
                        "answer": turn.answer,
                        "cost_micro": turn.cost_micro,
                    }
                )
            except SessionBusy as exc:
                await queue.put({"type": "error", "code": "session_busy", "message": str(exc)})
            except GatewayError as exc:
                await queue.put({"type": "error", "code": exc.code, "message": str(exc)})
            except Exception as exc:  # noqa: BLE001 —— 流已经开了，只能用事件报错
                await queue.put(
                    {"type": "error", "code": "internal", "message": str(exc)[:300]}
                )
            finally:
                await queue.put(None)

        async def frames() -> AsyncIterator[bytes]:
            task = asyncio.create_task(worker())
            try:
                while True:
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=0.5)
                    except asyncio.TimeoutError:
                        if await request.is_disconnected():
                            break
                        yield b": keep-alive\n\n"  # 心跳：让代理与浏览器知道流还活着
                        continue
                    if item is None:
                        break
                    yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n".encode()
                    if item.get("type") == "error":
                        break
            finally:
                if await request.is_disconnected():
                    # 页面关了：取消本会话在本进程内的运行（协作式，不杀线程）
                    for record in runtime.active_runs.snapshot():
                        if record.session_id == sid and not record.finished:
                            runtime.active_runs.cancel(
                                record.run_id, "浏览器断开，取消本回合"
                            )
                if not task.done():
                    task.cancel()
                with contextlib.suppress(BaseException):
                    await task

        return StreamingResponse(
            frames(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    # ---------------------------------------------------------------- 运行（D09）

    @app.post("/api/runs")
    async def run_create(payload: ChatIn) -> dict[str, Any]:
        """显式运行入口：语义同 /api/chat，但调用方明确按 run 消费。"""
        sid = payload.session_id if payload.session_id is not None else runtime.agent.session_id
        if not runtime.store.session_exists(sid):
            raise HTTPException(status_code=404, detail="会话不存在")
        try:
            turn = await _run_turn(sid, payload.message)
        except SessionBusy as exc:
            raise HTTPException(status_code=429, detail=str(exc))
        except GatewayError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        runtime.spawn_review(sid)
        return {
            "run_id": turn.run_id,
            "session_id": sid,
            "status": turn.status,
            "answer": turn.answer,
            "error_code": turn.error_code,
            "cost_micro": turn.cost_micro,
            "budget": turn.budget,
        }

    @app.get("/api/runs")
    def run_list(session_id: int | None = None, limit: int = 20) -> dict[str, Any]:
        try:
            runs = runtime.runs.list_recent(session_id=session_id, limit=limit)
        except StateUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        return {"runs": [run.brief() for run in runs]}

    @app.get("/api/runs/{run_id}")
    def run_get(run_id: str) -> dict[str, Any]:
        record = runtime.runs.get(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail="运行不存在")
        return {"run": record.brief(), "live": runtime.active_runs.get(run_id) is not None}

    @app.get("/api/runs/{run_id}/events")
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

    @app.post("/api/runs/{run_id}/cancel")
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

    # ---------------------------------------------------------------- 费用（D10）

    @app.get("/api/llm/usage")
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

    @app.put("/api/profile/line")
    def profile_line_update(payload: ProfileLineIn) -> dict[str, Any]:
        content = payload.content.strip()
        if not content:
            raise HTTPException(status_code=400, detail="内容不能为空")
        if not runtime.store.profile_edit(payload.line_id, content):
            raise HTTPException(status_code=404, detail="档案行不存在")
        return {"ok": True}

    @app.delete("/api/profile/line/{line_id}")
    def profile_line_delete(line_id: int) -> dict[str, Any]:
        if not runtime.store.profile_delete(line_id):
            raise HTTPException(status_code=404, detail="档案行不存在")
        return {"ok": True}

    @app.get("/api/resident")
    def get_resident() -> dict[str, Any]:
        return {
            "usage": runtime.resident.usage(),
            "limit": runtime.resident.limit,
            "entries": [
                {"id": i, "content": c} for i, c in runtime.resident.entries()
            ],
        }

    @app.post("/api/resident/update")
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
    def get_skill(name: str, path: str | None = None) -> dict[str, Any]:
        try:
            content = runtime.skills.view(name, path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if content is None:
            raise HTTPException(status_code=404, detail="技能不存在")
        return {"content": content, "references": runtime.skills.references(name) if path is None else []}

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
            "approval": {"mode": cfg.approval.mode},
            "asr": {
                "model": cfg.asr.model,
                "device": cfg.asr.device,
                "compute_type": cfg.asr.compute_type,
            },
            "qq": {
                "enabled": cfg.qq.enabled,
                "ws_url": cfg.qq.ws_url,
                # 不回显明文：前端只能知道"是否已配置"，修改见 system_config_save 的三态规则
                "access_token": "",
                "access_token_configured": bool(cfg.qq.access_token),
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
        """改配置：密钥字段三态——缺省/空串=保留旧值，null=显式清除，非空=替换。"""
        updates, touched_secret = _normalize_secret_updates(payload.updates)
        try:
            config_file = runtime.paths.config  # 与 bootstrap 读取的是同一个文件
            text = config_file.read_text(encoding="utf-8")
            new_text = apply_updates(text, updates)
            safe_write(config_file, new_text)
        except (ConfigEditError, OSError) as exc:
            # 含密钥字段时只回笼统原因，避免把密钥值回显到错误信息/日志里
            detail = "配置更新被拒绝（含密钥字段，详情已省略）" if touched_secret else str(exc)
            raise HTTPException(status_code=400, detail=detail)
        return {
            "ok": True,
            "hint": "已写入 config.yaml；正在运行的服务重启后生效（见服务面板）",
        }

    # ---------- B站 Cookie（字幕/音频流需要登录态） ----------

    @app.get("/api/system/bili-cookies")
    def bili_cookies_status() -> dict[str, Any]:
        keys = {"SESSDATA": False, "bili_jct": False, "buvid3": False}
        cookies_path = runtime.paths.cookies  # 注入路径：不碰真机 data/
        try:
            data = json.loads(cookies_path.read_text(encoding="utf-8"))
            for k in keys:
                keys[k] = bool(str(data.get(k, "")).strip())
        except (OSError, json.JSONDecodeError):
            pass
        return {"configured": any(keys.values()), "keys": keys, "path": str(cookies_path)}

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
        cookies_path = runtime.paths.cookies
        cookies_path.parent.mkdir(parents=True, exist_ok=True)
        cookies_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {"ok": True, "saved": list(data), "path": str(cookies_path)}

    # ---------- 模型连通性测试 ----------

    @app.post("/api/system/model-test")
    def model_test(payload: ModelTestIn) -> dict[str, Any]:
        """连通性测试：错误码映射成可分辨的 HTTP 状态，错误文本已脱敏。"""
        try:
            reply = runtime.gateway.complete(
                [{"role": "user", "content": "这是一次连通性测试，请只回复：OK"}],
                model=payload.model,
                purpose="test",
            )
        except GatewayError as exc:
            status = {
                "auth": 401,
                "permission": 403,
                "rate_limit": 429,
                "budget_exhausted": 402,
                "price_unknown": 400,
                "unsupported_capability": 400,
                "cancelled": 409,
            }.get(exc.code, 400)
            raise HTTPException(
                status_code=status,
                detail={"code": exc.code, "message": str(exc), "hint": exc.hint},
            )
        return {
            "ok": True,
            "reply": reply.text[:120],
            "cost_micro": reply.cost_micro,
            "cost_source": reply.cost_source,
        }

    # ---------- 心跳任务管理（可视化） ----------

    @app.post("/api/system/cron/create")
    def cron_create(payload: CronCreateIn) -> dict[str, Any]:
        """工作台直建任务：与 agent 的 cron_add 走同一套 JobStore 校验。"""
        job = Job(
            name=payload.name.strip(),
            prompt=payload.prompt.strip(),
            every_minutes=payload.every_minutes,
            daily_at=(payload.daily_at.strip() if payload.daily_at else None),
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

    @app.get("/api/system/cron")
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

    @app.post("/api/system/cron/delete")
    def cron_delete(payload: CronNameIn) -> dict[str, Any]:
        try:
            return {"ok": runtime.cron_jobs.remove(payload.name)}
        except StateUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc))

    @app.get("/api/system/cron/history")
    def cron_history(limit: int = 50) -> dict[str, Any]:
        """执行账本：成功与失败都记账，新的在前。"""
        try:
            return {"history": runtime.cron_ledger.recent(min(limit, 100))}
        except StateUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc))

    @app.get("/api/system/state")
    def system_state() -> dict[str, Any]:
        """共享状态库自检：版本、完整性、迁移记录、被停用的能力（doctor 的雏形）。"""
        return runtime.state.health()

    # ---------- 安卓工作台（MJPEG 看屏 + UI 树 + 点击下发，NOTES §2.3 最低成本方案） ----------

    @app.get("/api/android/state")
    def android_state() -> dict[str, Any]:
        try:
            serial = runtime.android._adb.require_device()
            w, h = runtime.android.screen_size()
            return {"connected": True, "serial": serial, "width": w, "height": h}
        except Exception as exc:  # noqa: BLE001 —— adb 缺失/无设备/未授权都归为未连接
            return {"connected": False, "detail": str(exc)[:200]}

    @app.get("/api/android/tree")
    def android_tree() -> dict[str, Any]:
        try:
            tree = runtime.android.refresh_tree()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=409, detail=str(exc))
        return {
            "tree": tree,
            "elements": [
                {
                    "idx": e["idx"],
                    "label": e["text"] or e["content_desc"] or e["resource_id"],
                    "clickable": e["clickable"],
                }
                for e in runtime.android.elements()
            ],
        }

    @app.post("/api/android/tap")
    def android_tap(payload: AndroidTapIn) -> dict[str, Any]:
        """用户在看屏上点了一下：帧内坐标+帧尺寸 → wm size 反算 → adb tap。

        与 agent 的 CONFIRM 工具不同：这里的操作者就是用户本人（自己的点屏）。
        """
        try:
            return {
                "ok": True,
                "detail": runtime.android.tap_scaled(
                    payload.x, payload.y, payload.stream_w, payload.stream_h
                ),
            }
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=409, detail=str(exc))

    @app.post("/api/android/element")
    def android_element(payload: AndroidElementIn) -> dict[str, Any]:
        try:
            return {"ok": True, "detail": runtime.android.tap_element(payload.idx)}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=409, detail=str(exc))

    @app.post("/api/android/key")
    def android_key(payload: AndroidKeyIn) -> dict[str, Any]:
        try:
            return {"ok": True, "detail": runtime.android.key(payload.keycode)}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=409, detail=str(exc))

    @app.get("/api/android/stream")
    def android_stream() -> StreamingResponse:
        """MJPEG：原始截图转半分辨率 JPEG q40，降低本机看屏带宽。

        设备掉线即断流（前端 onerror 提示重开）；断开连接由 StreamingResponse 收尾。
        """
        import time as _time

        def _frames():
            while True:
                try:
                    png = runtime.android.screenshot_bytes()
                    with Image.open(BytesIO(png)) as source:
                        size = (max(1, source.width // 2), max(1, source.height // 2))
                        with source.convert("RGB") as rgb:
                            with rgb.resize(size, Image.Resampling.BILINEAR) as frame:
                                output = BytesIO()
                                frame.save(output, format="JPEG", quality=40)
                                jpeg = output.getvalue()
                except Exception:  # noqa: BLE001
                    break
                yield (
                    b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                    + str(len(jpeg)).encode()
                    + b"\r\n\r\n"
                    + jpeg
                    + b"\r\n"
                )
                _time.sleep(0.6)

        return StreamingResponse(
            _frames(), media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-store"},
        )

    # ---------- 内置终端（ConPTY，Windows；仅本机 127.0.0.1） ----------

    @app.websocket("/api/terminal/ws")
    async def terminal_ws(ws: WebSocket) -> None:
        # 握手前先验身份：未配对的连接直接拒绝（accept 之前 close = 403 握手失败）
        rejection = auth.check_websocket(
            host=ws.headers.get("host"),
            origin=ws.headers.get("origin"),
            cookie=ws.cookies.get(COOKIE_NAME),
        )
        if rejection is not None:
            await ws.close(code=1008)
            return
        await ws.accept()
        if not terminal_manager.available:
            await ws.send_text(
                f"终端不可用（需要 Windows + pywinpty）：{terminal_manager.unavailable_reason}"
            )
            await ws.close()
            return
        try:
            session = terminal_manager.open()
        except RuntimeError as exc:
            await ws.send_text(f"无法打开终端：{exc}")
            await ws.close()
            return

        async def pump() -> None:
            """PTY → WS：阻塞读放线程池；进程退出/连接断开都会收尾。"""
            try:
                while session.isalive():
                    data = await asyncio.to_thread(session.read)
                    if not data:
                        break
                    await ws.send_text(data)
            except Exception:  # noqa: BLE001 —— 断开与退出都是正常结束路径
                pass

        async def writer() -> None:
            """WS → PTY：纯文本为键盘输入；{"resize":[cols,rows]} 调整窗口。"""
            try:
                while True:
                    msg = await ws.receive_text()
                    if msg.startswith('{"resize"'):
                        try:
                            cols, rows = json.loads(msg)["resize"]
                            session.resize(int(cols), int(rows))
                        except (ValueError, TypeError, KeyError):
                            pass
                        continue
                    session.write(msg)
            except Exception:  # noqa: BLE001
                pass

        pump_task = asyncio.create_task(pump())
        writer_task = asyncio.create_task(writer())
        done, pending = await asyncio.wait(
            {pump_task, writer_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        terminal_manager.close(session)  # 杀 PTY 会解除阻塞中的 read 线程
        for task in pending:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="ui")
    return app


def _validate_bind_host(host: str) -> str:
    """默认只允许回环监听；非回环必须显式开启（`OMNI_WEB_ALLOW_REMOTE=1`）。"""
    if host in LOOPBACK_HOSTS:
        return host
    if os.environ.get("OMNI_WEB_ALLOW_REMOTE") == "1":
        return host
    raise SystemExit(
        f"拒绝在非回环地址 {host} 监听：工作台是本地单所有者工具，没有公网认证。"
        "确需远程访问请设 OMNI_WEB_ALLOW_REMOTE=1，并自行承担风险（建议走 SSH 隧道）"
    )


def main() -> None:
    load_env()
    host = (os.environ.get("OMNI_WEB_HOST") or "127.0.0.1").strip()
    port = int(os.environ.get("OMNI_WEB_PORT", "8000"))
    if host not in LOOPBACK_HOSTS:
        _validate_bind_host(host)  # 未显式开启会 SystemExit
        print(f"[工作台] 警告：正在非回环地址 {host} 监听（已由 OMNI_WEB_ALLOW_REMOTE=1 显式开启）")
    runtime = build_runtime(owner_kind="web")
    # Web 上下文的确认流：confirm 工具 → 工作台横幅（agent 线程阻塞等待用户点击）。
    # approval.mode=smart 时先经辅助模型预筛（明显安全放行/明显危险拒），拿不准仍走横幅。
    approver: Any = WebApprover(runtime.approvals)
    if runtime.config.approval.mode == "smart":
        from ..safety.smart import SmartApprover

        review_model = runtime.config.models.review.strip() or runtime.agent.model

        def _ask(system: str, user: str) -> str:
            reply = runtime.gateway.complete(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                model=review_model,
                tools=None,
            )
            return reply.text

        approver = SmartApprover(base=approver, ask_model=_ask)
    runtime.agent.approver = approver
    app = create_app(runtime)
    auth = app.state.web_auth
    print(f"OmniOperator 工作台已启动：http://{host}:{port}  （Ctrl+C 退出）")
    auth.announce_pairing()
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
