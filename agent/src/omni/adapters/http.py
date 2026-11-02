"""V1 HTTP contract for the V2 kernel. All business access goes through use cases."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..application.engine import Application
from ..domain.contracts import BusinessError
from .diagnostics import bind_context

log = logging.getLogger("omni.v2.http")


@dataclass
class HttpRuntime:
    application: Application
    authenticate: Callable[[str], Awaitable[bool]]
    describe: Callable[[], dict]
    close: Callable[[], Awaitable[dict]]
    logs: Callable[[int], Awaitable[list[dict]]]
    credential: Callable[[str, str | None], Awaitable[None]]
    assets: Path


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TurnInput(StrictInput):
    session_id: uuid.UUID | None = None
    key: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=200_000)


class SettingsInput(StrictInput):
    revision: int = Field(ge=1)
    value: dict


class ApprovalInput(StrictInput):
    allowed: bool
    policy_revision: int = Field(ge=1)


class MemoryInput(StrictInput):
    content: str = Field(min_length=1, max_length=20_000)
    message_id: uuid.UUID | None = None
    quote: str = Field(default="", max_length=20_000)


class CredentialInput(StrictInput):
    value: str = Field(min_length=1, max_length=65_536)


def create_app(factory: Callable[[], Awaitable[HttpRuntime]], *, allowed_origins: tuple[str, ...] = ()) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime = await factory()
        app.state.runtime = runtime
        try:
            yield
        finally:
            result = await runtime.close()
            if not result["complete"]:
                log.error("Shutdown has residual components", extra={"event": "runtime.shutdown_incomplete"})

    app = FastAPI(title="OmniOperator V2", version="2.0.0-alpha", lifespan=lifespan)

    def runtime(request: Request) -> HttpRuntime:
        return request.app.state.runtime

    async def authenticate(request: Request) -> None:
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer ") or not await runtime(request).authenticate(header[7:]):
            raise BusinessError("unauthenticated", "A valid owner token is required", status=401)

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        origin = request.headers.get("origin")
        same = str(request.base_url).rstrip("/")
        if origin and origin != same and origin not in allowed_origins:
            return JSONResponse(
                {"error": {"code": "origin_denied", "message": "Origin is not trusted", "request_id": request_id}},
                status_code=403,
            )
        with bind_context(request_id=request_id):
            response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        )
        return response

    @app.exception_handler(BusinessError)
    async def business_error(request: Request, exc: BusinessError):
        return JSONResponse(
            {
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": exc.retryable,
                    "request_id": getattr(request.state, "request_id", ""),
                }
            },
            status_code=exc.status,
        )

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception):
        log.error(
            "HTTP use case failed",
            exc_info=(type(exc), exc, exc.__traceback__),
            extra={"event": "http.failed", "error_code": "internal_error"},
        )
        return JSONResponse(
            {
                "error": {
                    "code": "internal_error",
                    "message": "Request failed; use its request ID for diagnostics",
                    "request_id": getattr(request.state, "request_id", ""),
                }
            },
            status_code=500,
        )

    @app.get("/health/live")
    async def live():
        return {"alive": True}

    @app.get("/health/ready")
    async def ready(request: Request):
        healthy = bool(runtime(request).describe()["ready"])
        return JSONResponse({"ready": healthy}, status_code=200 if healthy else 503)

    router = APIRouter(prefix="/api/v1", dependencies=[Depends(authenticate)])

    @router.get("/version")
    async def version(request: Request):
        return runtime(request).describe()

    @router.get("/agents")
    async def agents(request: Request):
        return {"items": await runtime(request).application.query("agents", "")}

    @router.post("/agents/{agent_id}/turns", status_code=202)
    async def turn(agent_id: uuid.UUID, data: TurnInput, request: Request):
        return await runtime(request).application.submit_turn(
            str(agent_id), str(data.session_id) if data.session_id else None, data.key, data.message
        )

    @router.get("/agents/{agent_id}/sessions")
    async def sessions(agent_id: uuid.UUID, request: Request):
        return {"items": await runtime(request).application.query("sessions", str(agent_id))}

    @router.get("/agents/{agent_id}/sessions/{session_id}/messages")
    async def messages(agent_id: uuid.UUID, session_id: uuid.UUID, request: Request):
        return {"items": await runtime(request).application.messages(str(agent_id), str(session_id))}

    @router.get("/agents/{agent_id}/runs")
    async def runs(agent_id: uuid.UUID, request: Request):
        return {"items": await runtime(request).application.query("runs", str(agent_id))}

    @router.get("/agents/{agent_id}/runs/{run_id}")
    async def run(agent_id: uuid.UUID, run_id: uuid.UUID, request: Request):
        return await runtime(request).application.get_run(str(agent_id), str(run_id))

    @router.get("/agents/{agent_id}/runs/{run_id}/events")
    async def events(
        agent_id: uuid.UUID,
        run_id: uuid.UUID,
        request: Request,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=200),
    ):
        application = runtime(request).application
        await application.get_run(str(agent_id), str(run_id))
        items = await application.query("events", str(agent_id), run_id=str(run_id), after=after, limit=limit)
        return {"items": items, "next_after": items[-1]["sequence"] if items else after}

    @router.post("/agents/{agent_id}/runs/{run_id}/cancel")
    async def cancel(agent_id: uuid.UUID, run_id: uuid.UUID, request: Request):
        await runtime(request).application.cancel(str(agent_id), str(run_id))
        return {"accepted": True}

    @router.get("/agents/{agent_id}/settings")
    async def settings(agent_id: uuid.UUID, request: Request):
        return await runtime(request).application.settings(str(agent_id))

    @router.put("/agents/{agent_id}/settings")
    async def save_settings(agent_id: uuid.UUID, data: SettingsInput, request: Request):
        return await runtime(request).application.save_settings(str(agent_id), data.value, data.revision)

    @router.get("/agents/{agent_id}/approvals")
    async def approvals(agent_id: uuid.UUID, request: Request):
        return {"items": await runtime(request).application.approvals(str(agent_id))}

    @router.post("/agents/{agent_id}/approvals/{approval_id}")
    async def approve(agent_id: uuid.UUID, approval_id: uuid.UUID, data: ApprovalInput, request: Request):
        await runtime(request).application.approve(str(agent_id), str(approval_id), data.allowed, data.policy_revision)
        return {"resolved": True}

    @router.get("/agents/{agent_id}/memory")
    async def memories(agent_id: uuid.UUID, request: Request, q: str = Query(default="", max_length=500)):
        application = runtime(request).application
        return {
            "items": await application.search_memory(str(agent_id), q)
            if q
            else await application.query("memory", str(agent_id))
        }

    @router.post("/agents/{agent_id}/memory", status_code=201)
    async def remember(agent_id: uuid.UUID, data: MemoryInput, request: Request):
        return await runtime(request).application.remember(
            str(agent_id), data.content, message_id=str(data.message_id) if data.message_id else None, quote=data.quote
        )

    @router.delete("/agents/{agent_id}/memory/{memory_id}")
    async def forget(agent_id: uuid.UUID, memory_id: uuid.UUID, request: Request):
        await runtime(request).application.forget(str(agent_id), str(memory_id))
        return {"deleted": True}

    @router.get("/agents/{agent_id}/audit")
    async def audit(agent_id: uuid.UUID, request: Request, after: int = Query(default=0, ge=0)):
        return {"items": await runtime(request).application.query("audit", str(agent_id), after=after)}

    @router.get("/system/logs")
    async def logs(request: Request, limit: int = Query(default=100, ge=1, le=500)):
        return {"items": await runtime(request).logs(limit)}

    @router.put("/credentials/{reference}")
    async def put_credential(reference: str, data: CredentialInput, request: Request):
        await runtime(request).credential(reference, data.value)
        return {"saved": True}

    @router.delete("/credentials/{reference}")
    async def delete_credential(reference: str, request: Request):
        await runtime(request).credential(reference, None)
        return {"deleted": True}

    app.include_router(router)

    @app.get("/", include_in_schema=False)
    async def index(request: Request):
        return FileResponse(runtime(request).assets / "v2/index.html")

    # Resources are fixed at composition, never selected by an HTTP file path.
    @app.get("/assets/{path:path}", include_in_schema=False)
    async def resource(path: str, request: Request):
        root = runtime(request).assets.resolve()
        target = (root / path).resolve()
        if (
            (root / path).is_symlink()
            or (root / path).is_junction()
            or not target.is_relative_to(root)
            or not target.is_file()
            or any(p.is_symlink() for p in (root / path).parents if p.is_relative_to(root))
        ):
            raise BusinessError("not_found", "Asset does not exist", status=404)
        return FileResponse(target)

    return app
