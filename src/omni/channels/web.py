"""Web 工作台入口：uv run omni-web（默认 http://127.0.0.1:8000）。

职责：应用生命周期、统一身份、领域路由装配与静态文件挂载。
领域适配与请求模型位于 web_api/；本入口只消费注入 Runtime，不构造核心对象。
审批策略：WebApprover——safe 直通，confirm 弹工作台确认横幅（阻塞等待，
超时自动拒绝），dangerous 一律拒绝。

身份（A07）：除配对接口外，所有 `/api/*` 都要求配对后下发的 HttpOnly 会话 cookie；
Host/Origin/Sec-Fetch-Site 三级校验挡住跨站与 DNS rebinding；终端 WS 握手同样要会话。
默认只监听回环地址，非回环必须显式开启（`OMNI_WEB_ALLOW_REMOTE=1`）。
"""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import uvicorn
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..bootstrap import Runtime, build_runtime
from ..config import load_env
from ..llm.gateway import GatewayError
from ..safety.gates import WebApprover
from ..system.config_io import (
    EDITABLE,
    ConfigEditError,
    apply_updates,
    read_config_text,
    read_env_secrets,
    safe_write,
    set_env_secret,
)
from .web_api import create_api_routers
from .web_api.capabilities import create_capabilities_router
from .web_api.schemas import (
    ChatIn,
    ResolveIn,
    NapCatStartIn,
    ConfigUpdateIn,
    ModelTestIn,
    MCPSchemaConfirmIn,
    BiliCookiesIn,
    CronNameIn,
    CronToggleIn,
    CronCreateIn,
    CronUpdateIn,
    TodoToggleIn,
    AndroidTapIn,
    AndroidElementIn,
    AndroidKeyIn,
    ProfileLineIn,
    ResidentUpdateIn,
    PairIn,
    SkillSaveIn,
    SkillRollbackIn,
    SkillAckIn,
)
from .terminal import TerminalManager
from .terminal.routes import create_terminal_router
from .workbench import create_workbench_router
from .web_auth import COOKIE_NAME, LOOPBACK_HOSTS, WebAuth

_STATIC_DIR = Path(__file__).parent / "web_static"

# 只回"是否已配置"的密钥字段：接口不回显明文（A07）
_SECRET_KEYS = {("qq", "access_token")}

class ModelKeyIn(BaseModel):
    """填 Key：`value` 为 None/空串表示清除该行，非空则替换。"""

    name: str
    value: str | None = None

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
    """密钥字段三态归一，返回 (过滤后的更新, 是否真的写入密钥明文)。

    - 键缺省 / 空串 → 保留旧值（前端拿到的是掩码，空值不能等于"清空"）
    - `null` → 显式清除（写空串）
    - 非空字符串 → 显式替换

    `touched` 只在**要写入非空明文**时为真：只有这种情形错误信息才可能带出密钥。
    以前只要请求里出现密钥键就置真，而前端每次都带一个空串的 `qq.access_token`，
    于是任何校验失败都被替换成"含密钥字段，详情已省略"，把真实原因彻底藏住。
    """
    cleaned: dict[str, dict[str, Any]] = {}
    touched = False
    for section, keys in (updates or {}).items():
        for key, value in (keys or {}).items():
            if (section, key) not in _SECRET_KEYS:
                cleaned.setdefault(section, {})[key] = value
                continue
            if value is None:
                cleaned.setdefault(section, {})[key] = ""  # 清除，写的是空串，不含明文
            elif isinstance(value, str) and not value.strip():
                continue  # 保留旧值
            else:
                cleaned.setdefault(section, {})[key] = value
                touched = True
    return {s: k for s, k in cleaned.items() if k}, touched

def create_app(runtime: Runtime) -> FastAPI:
    """把 Runtime 暴露成 REST + 静态工作台（测试用 TestClient 直接调）。"""
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 10
            terminal_result = await asyncio.to_thread(terminal_manager.close_all, wait_s=max(0,deadline-loop.time()))
            result = await asyncio.to_thread(runtime.close, wait_s=max(0,deadline-loop.time()))
            if not terminal_result["complete"] or not result["complete"]:
                print(f"[工作台] 关闭残留：runtime={result['residual']} terminals={terminal_result['residual']}", flush=True)

    app = FastAPI(title="OmniOperator Workbench", lifespan=lifespan)
    terminal_manager = TerminalManager(**runtime.config.terminal.model_dump())
    app.state.terminal_manager = terminal_manager
    app.include_router(create_capabilities_router(runtime, terminal_manager))
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
            # 旧链接和真·码不对要说清区别：前者只是点了聊天记录里更早那条，
            # 后者才可能是别人在猜。混成一句话只会让人以为"链接又坏了"。
            if result.code == "pair_stale":
                tip = "这条链接是之前那版，已经被换掉了。工作台窗口里有当前可用的那条。"
            elif result.code == "pair_expired":
                tip = "链接超时了。工作台窗口里打印了新的那条，直接打开即可。"
            else:
                tip = "这条不是当前有效的配对链接。工作台窗口里打印了当前那条。"
            return HTMLResponse(
                f"<h3>配对失败</h3><p>{result.detail}</p><p>{tip}</p>",
                status_code=403,
            )
        response = RedirectResponse("/", status_code=303)
        return _set_session_cookie(response, result, auth.session_ttl)

    # 注意：必须声明在 /api/skills/{name} 之前，否则 "pending" 会被当成技能名。

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
            "journal": {
                "enabled": cfg.journal.enabled,
                "daily_time": cfg.journal.daily_time,
                "steps": cfg.journal.steps,
                "max_catchup": cfg.journal.max_catchup,
                "max_input_chars": cfg.journal.max_input_chars,
                "max_note_chars": cfg.journal.max_note_chars,
                "daily_prompt": cfg.journal.daily_prompt,
                "weekly_prompt": cfg.journal.weekly_prompt,
                "monthly_prompt": cfg.journal.monthly_prompt,
            },
            "transcript": {
                "max_entry_chars": cfg.transcript.max_entry_chars,
                "max_tool_args_chars": cfg.transcript.max_tool_args_chars,
                "max_tool_result_chars": cfg.transcript.max_tool_result_chars,
                "retention_days": cfg.transcript.retention_days,
                "dialogue_retention_days": cfg.transcript.dialogue_retention_days,
            },
            "people": {
                "person_card_chars": cfg.people.person_card_chars,
                "archive_max_chars": cfg.people.archive_max_chars,
                "resident_max_chars": cfg.people.resident_max_chars,
                "event_max_chars": cfg.people.event_max_chars,
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
                **{key: getattr(cfg.qq, key) for key in ("proactive_enabled", "owner_qq", "proactive_daily_limit",
                                                         "quiet_start", "quiet_end", "timezone", "group_reply_to_bot")},
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
            text = read_config_text(config_file)  # 保留原换行，避免整文件行尾被改写
            new_text = apply_updates(text, updates)
            from ..config import AppConfig
            AppConfig.model_validate(yaml.safe_load(new_text) or {})
            safe_write(config_file, new_text)
        except (ValueError, OSError, yaml.YAMLError) as exc:
            # 含密钥字段时只回笼统原因，避免把密钥值回显到错误信息/日志里
            detail = "配置更新被拒绝（含密钥字段，详情已省略）" if touched_secret else str(exc)
            raise HTTPException(status_code=400, detail=detail)
        return {
            "ok": True,
            "hint": "已写入 config.yaml；正在运行的服务重启后生效（见服务面板）",
        }

    @app.get("/api/system/qq-policy")
    def qq_policy_status() -> dict[str, Any]:
        from ..core.qq_policy import proactive_decision
        policy = runtime.qq_policy()
        decision = proactive_decision(policy, policy.owner_qq)
        return {"enabled": policy.proactive_enabled, "owner_qq": policy.owner_qq,
                "decision": decision.state, "reason": decision.reason,
                "used": runtime.state.budget_used("qq_proactive", decision.day) if decision.day else None,
                "limit": policy.proactive_daily_limit,
                "events": runtime.qq_messages.status() if runtime.qq_messages else {}}

    @app.post("/api/system/qq-policy/stop")
    def qq_policy_stop() -> dict[str, Any]:
        system_config_save(ConfigUpdateIn(updates={"qq": {"proactive_enabled": False}}))
        if runtime.outbox:
            for item in runtime.outbox.pending():
                runtime.outbox.discard(item["id"], note="用户一键停止主动提醒")
        return {"ok": True, "hint": "主动提醒已停用，尚未开始的投递已丢弃"}

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

    @app.get("/api/system/model-keys")
    def model_keys() -> dict[str, Any]:
        """各供应商密钥是否已配置 + 脱敏值；前端只用来显示"已填 / 未填"。"""
        return {"keys": read_env_secrets(runtime.paths.env)}

    @app.post("/api/system/model-key")
    def model_key_set(payload: ModelKeyIn) -> dict[str, Any]:
        """写一个密钥进 .env 并注入当前进程环境；空值等于清除该行。"""
        try:
            masked = set_env_secret(runtime.paths.env, payload.name, payload.value)
        except ConfigEditError as exc:
            # 只有配置类错误才回具体原因；这里不涉及密钥值本身
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"写入 .env 失败：{exc}") from exc
        return {
            "ok": True,
            "name": payload.name,
            "masked": masked,
            "configured": bool(masked),
        }

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

    for router in create_api_routers(runtime):
        app.include_router(router)

    app.include_router(create_terminal_router(terminal_manager, auth))
    app.include_router(create_workbench_router(runtime))

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
