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
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ..bootstrap import Runtime, build_runtime
from ..config import load_env, web_root
from ..safety.gates import WebApprover
from ..system.config_io import (
    apply_updates,  # noqa: F401  （有意再导出，兼容旧 import）
    read_config_text,  # noqa: F401  （有意再导出，兼容旧 import）
    safe_write,  # noqa: F401  （有意再导出，兼容旧 import）
)
from ..system.updates.archive import verify as verify_update_source
from ..system.updates.common import UpdateError, read_json, write_json
from .terminal import TerminalManager
from .terminal.routes import create_terminal_router
from .web_api import create_api_routers
from .web_api.capabilities import create_capabilities_router
from .web_api.schemas import (
    AndroidElementIn,  # noqa: F401  （有意再导出，兼容旧 import）
    AndroidKeyIn,  # noqa: F401  （有意再导出，兼容旧 import）
    AndroidTapIn,  # noqa: F401  （有意再导出，兼容旧 import）
    ChatIn,  # noqa: F401  （有意再导出，兼容旧 import）
    ConfigUpdateIn,  # noqa: F401 -- 兼容旧公开请求类型
    CronCreateIn,  # noqa: F401  （有意再导出，兼容旧 import）
    CronNameIn,  # noqa: F401  （有意再导出，兼容旧 import）
    CronToggleIn,  # noqa: F401  （有意再导出，兼容旧 import）
    CronUpdateIn,  # noqa: F401  （有意再导出，兼容旧 import）
    MCPSchemaConfirmIn,  # noqa: F401  （有意再导出，兼容旧 import）
    ModelKeyIn,  # noqa: F401 -- 兼容旧公开请求类型
    NapCatStartIn,  # noqa: F401  （有意再导出，兼容旧 import）
    PairIn,  # noqa: F401 -- 兼容旧公开请求类型
    ProfileLineIn,  # noqa: F401  （有意再导出，兼容旧 import）
    ResidentUpdateIn,  # noqa: F401  （有意再导出，兼容旧 import）
    ResolveIn,  # noqa: F401  （有意再导出，兼容旧 import）
    SkillAckIn,  # noqa: F401  （有意再导出，兼容旧 import）
    SkillRollbackIn,  # noqa: F401  （有意再导出，兼容旧 import）
    SkillSaveIn,  # noqa: F401  （有意再导出，兼容旧 import）
    TodoToggleIn,  # noqa: F401  （有意再导出，兼容旧 import）
)
from .web_api.settings import _SECRET_KEYS, _normalize_secret_updates, _set_session_cookie, create_settings_router  # noqa: F401 -- 兼容旧导入
from .web_auth import COOKIE_NAME, LOOPBACK_HOSTS, WebAuth
from .workbench import create_workbench_router

_STATIC_DIR = web_root()


def _create_auth(runtime: Runtime) -> WebAuth:
    environ_host = os.environ.get("OMNI_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("OMNI_WEB_PORT", "8000"))
    return WebAuth(runtime.data_dir, bind_host=environ_host, port=port)


def create_app(runtime: Runtime) -> FastAPI:
    """把 Runtime 暴露成 REST + 静态工作台（测试用 TestClient 直接调）。"""

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        runtime.updates.start_checks()

        async def update_watch():
            announced = False
            while True:
                await asyncio.sleep(0.2)
                probe = os.environ.get("OMNI_UPDATE_PROBE", "")
                marker = runtime.updates.gate.state()
                if probe and not announced and getattr(runtime.updates, "is_serving", lambda: False)():
                    health = runtime.state.health()
                    if health["degraded"]:
                        raise RuntimeError("升级启动检查发现停用的数据能力")
                    operation = runtime.updates.control / "operations" / marker["operation"]
                    plan = read_json(operation / "plan.json")
                    await asyncio.to_thread(verify_update_source, runtime.updates.root, plan["receipt"])
                    identity = runtime.updates.version()
                    if identity["disk_changed"] or identity["commit"] != plan["receipt"]["commit"]:
                        raise RuntimeError("启动期间源码发生变化")
                    write_json(operation / "ready.json", {"token": probe, "commit": identity["commit"]})
                    announced = True
                if probe and announced and not marker:
                    runtime.updates.shutdown()
                    return

        watcher = asyncio.create_task(update_watch())
        # 常驻服务（心跳调度/QQ 通道/NapCat）由守护线程拉起并一直盯着：
        # 工作台一起来它们就在线，不需要用户点"启动"。隔离实例里守护是 disabled，
        # 这里等于空操作——绝不能因为装配问题让测试去拉起真机进程。
        if runtime.supervisor is not None:
            try:
                await asyncio.to_thread(runtime.supervisor.start)
            except Exception as exc:  # noqa: BLE001 —— 守护没起来不该让工作台打不开
                print(f"[工作台] 服务守护启动失败：{type(exc).__name__}: {exc}", flush=True)
        try:
            yield
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 10
            if runtime.supervisor is not None:
                try:
                    # 默认只交出守护权、不停服务：关掉工作台不该打断正在跑的定时任务
                    await asyncio.to_thread(
                        runtime.supervisor.stop,
                        stop_services=not runtime.supervisor.keep_running_after_exit(),
                        wait_s=max(0.0, min(3.0, deadline - loop.time())),
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"[工作台] 服务守护停止失败：{type(exc).__name__}: {exc}", flush=True)
            terminal_result = await asyncio.to_thread(terminal_manager.close_all, wait_s=max(0, deadline - loop.time()))
            result = await asyncio.to_thread(runtime.close, wait_s=max(0, deadline - loop.time()))
            if not terminal_result["complete"] or not result["complete"]:
                print(
                    f"[工作台] 关闭残留：runtime={result['residual']} terminals={terminal_result['residual']}",
                    flush=True,
                )

    app = FastAPI(title="OmniOperator Workbench", lifespan=lifespan)
    terminal_manager = TerminalManager(**runtime.config.terminal.model_dump())
    app.state.terminal_manager = terminal_manager
    runtime.updates.terminal_busy = lambda: terminal_manager.snapshot()["active"]
    terminal_manager.admission = runtime.updates.gate.accepting
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
            return JSONResponse({"detail": rejection.detail, "code": rejection.code}, status_code=status)
        updates = runtime.updates
        probe_read = request.method in {"GET", "HEAD"} and (
            path in {"/api/system/version", "/api/system/updates"} or path.startswith("/api/system/updates/operations/")
        )
        if updates.probe_mode and not path.startswith("/api/auth/") and not probe_read:
            return JSONResponse({"detail": "新版本验证中，等待工作台恢复", "code": "starting"}, status_code=503)
        writing = request.method not in {"GET", "HEAD", "OPTIONS"} and not path.startswith("/api/auth/")
        exempt = path == "/api/system/updates/install"
        finishing = bool(
            re.fullmatch(r"/api/(approvals/[^/]+/resolve|runs/[^/]+/cancel|command-jobs/[^/]+/cancel)", path)
        )
        if updates.gate.active() and writing and not exempt and not finishing:
            return JSONResponse({"detail": "系统正在升级，暂时停止接收新操作", "code": "maintenance"}, status_code=503)
        try:
            if writing and not exempt:
                with updates.gate.work(allow_drain=finishing):
                    return await call_next(request)
            return await call_next(request)
        except UpdateError as exc:
            return JSONResponse({"detail": str(exc), "code": exc.code}, status_code=503)

    app.include_router(create_settings_router(runtime, auth))
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
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    runtime.updates.shutdown = lambda: setattr(server, "should_exit", True)
    runtime.updates.is_serving = lambda: server.started
    server.run()
    # 探测启动不运行模型 worker 或插件；事务提交后再按普通启动恢复完整运行态。
    if os.environ.get("OMNI_UPDATE_PROBE") and not runtime.updates.gate.active():
        os.environ.pop("OMNI_UPDATE_PROBE", None)
        main()


if __name__ == "__main__":
    main()
