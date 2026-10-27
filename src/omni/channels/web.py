"""Web 工作台入口：uv run omni-web（默认 http://127.0.0.1:8000）。

职责纪律：本文件只做 HTTP 适配（路由 + 请求模型 + 静态文件挂载），
领域操作全部通过 bootstrap.Runtime 门面，不构造核心对象。
审批策略：WebApprover——safe 直通，confirm 弹工作台确认横幅（阻塞等待，
超时自动拒绝），dangerous 一律拒绝。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
from io import BytesIO
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from PIL import Image

from ..bootstrap import Runtime, build_runtime
from ..config import BILI_COOKIES_PATH, CONFIG_PATH, load_env
from ..llm.gateway import GatewayError
from ..memory.cron import CronError, Job, compute_next_run, to_iso
from ..memory.store import PROFILE_MAX_CHARS
from ..safety.gates import WebApprover, private_target_allowed
from ..system.config_io import EDITABLE, ConfigEditError, apply_updates, safe_write
from ..system.services import ServiceError
from .terminal import TerminalManager

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


def create_app(runtime: Runtime) -> FastAPI:
    """把 Runtime 暴露成 REST + 静态工作台（测试用 TestClient 直接调）。"""
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await asyncio.to_thread(runtime.close_mcp)

    app = FastAPI(title="OmniOperator Workbench", lifespan=lifespan)
    terminal_manager = TerminalManager()

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
        if op == "add":
            message = runtime.resident.add(payload.content)
        elif op == "replace":
            message = runtime.resident.replace(int(payload.id or 0), payload.content)
        elif op == "remove":
            message = runtime.resident.remove(int(payload.id or 0))
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
        return {"ok": True, "name": job.name, "next_run_at": job.next_run_at}

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

    @app.get("/api/system/cron/history")
    def cron_history(limit: int = 50) -> dict[str, Any]:
        """执行账本：成功与失败都记账，新的在前。"""
        return {"history": runtime.cron_ledger.recent(min(limit, 100))}

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


def main() -> None:
    load_env()
    runtime = build_runtime()
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
    host = os.environ.get("OMNI_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("OMNI_WEB_PORT", "8000"))
    print(f"OmniOperator 工作台已启动：http://{host}:{port}  （Ctrl+C 退出）")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
