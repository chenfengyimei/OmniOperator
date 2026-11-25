"""Versioned HTTP contract for the V2 kernel; business access goes through use cases."""

from __future__ import annotations

import asyncio
import html
import ipaddress
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal, cast

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import Field

from ..application.action_review import ActionReviewService
from ..application.android import AndroidService
from ..application.background_review import BackgroundReviewService
from ..application.bilibili import BilibiliService
from ..application.configuration import ConfigurationService, ConfigurationServices
from ..application.credentials import CredentialService
from ..application.delivery import DeliveryService
from ..application.desktop import DesktopService
from ..application.desktop_actions import DesktopActions
from ..application.desktop_control import DesktopControlService
from ..application.desktop_portal import DesktopPortalService
from ..application.documents import DocumentService
from ..application.engine import Application
from ..application.extensions import ExtensionService
from ..application.images import ImageService
from ..application.learning_services import LearningServices
from ..application.maintenance import MaintenanceService
from ..application.mcp import MCPService
from ..application.media import MediaService
from ..application.memory_organization import MemoryOrganizationService
from ..application.memory_semantics import MemorySemanticService
from ..application.memory_services import MemoryServices
from ..application.objects import ObjectService
from ..application.operations import OperationsService
from ..application.people import PeopleService, PeopleServices
from ..application.persona import PersonaGrowthService
from ..application.ports import Record
from ..application.qq import QQService
from ..application.reflections import ReflectionService
from ..application.routing import RoutingService
from ..application.scheduling import SchedulingService
from ..application.skills import SkillService
from ..application.terminal_review import TerminalReviewService
from ..application.terminal_services import TerminalServices
from ..application.terminals import TerminalService
from ..application.vision_services import VisionServices
from ..domain.agents import MAX_NAME, MAX_REVISION
from ..domain.capabilities import capability_report
from ..domain.context import CONTEXT_CHARS, SUMMARY_CALLS_PER_TURN, SUMMARY_TRIGGER_MESSAGES
from ..domain.contracts import BusinessError
from ..domain.objects import MAX_OBJECT_BYTES
from .diagnostics import bind_context
from .http_events import PAGE_SIZE as EVENT_PAGE_SIZE
from .http_events import EventStreams, continuation
from .http_routes import (
    FeatureRoutes,
    StrictInput,
    bounded_configuration,
    configuration_input,
    mount_feature_routes,
    registered_service,
)
from .http_routes.bilibili import BilibiliProfileInput as BilibiliProfileInput
from .http_routes.bilibili import BilibiliPublicationInput as BilibiliPublicationInput
from .http_routes.bilibili import BilibiliReadInput as BilibiliReadInput
from .http_routes.deliveries import DeliveryDecision as DeliveryDecision
from .http_routes.deliveries import DeliveryInput as DeliveryInput
from .http_routes.deliveries import DeliveryReview as DeliveryReview
from .http_routes.desktop import require_desktop_services
from .http_routes.documents import SourceDocumentImport as SourceDocumentImport
from .http_routes.documents import SourceDocumentInput as SourceDocumentInput
from .http_routes.documents import SourceIndexInput as SourceIndexInput
from .http_routes.extensions import require_extension_service
from .http_routes.media import MediaJobInput as MediaJobInput
from .http_routes.media import MediaSourceInput as MediaSourceInput
from .http_routes.media import MediaUploadSourceInput as MediaUploadSourceInput
from .http_routes.memory import MemoryInput as MemoryInput
from .http_routes.people import AccountInput as AccountInput
from .http_routes.people import DocumentImport as DocumentImport
from .http_routes.people import DocumentInput as DocumentInput
from .http_routes.people import DocumentVersion as DocumentVersion
from .http_routes.people import PersonEdit as PersonEdit
from .http_routes.people import PersonInput as PersonInput
from .http_routes.qq import QQBindingInput as QQBindingInput
from .log_query import LogQuery

log = logging.getLogger("omni.v2.http")


@dataclass
class HttpRuntime:
    application: Application
    authenticate: Callable[[str], Awaitable[bool]]
    describe: Callable[[], Record]
    close: Callable[[], Awaitable[Record]]
    observe: Callable[[str, str, float], None]
    assets: Path
    objects: ObjectService
    identity_proof: Callable[[str], Record] | None = None
    drain: Callable[[Record], Awaitable[Record]] | None = None
    token_sessions: Callable[[], Awaitable[list[Record]]] | None = None
    rotate_token: Callable[[], Awaitable[Record]] | None = None
    # 本机免粘贴引导：只在回环地址 + 同源时下发所有者令牌，让「启动即用」回到 V1 体验。
    # 默认 None 表示关闭；远程绑定与跨源请求一律拿不到，API 鉴权强度不变。
    local_owner_token: Callable[[], str] | None = None
    feature_services: Mapping[str, object] = field(default_factory=dict[str, object])
    upload_slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(4))
    # 连接测试复用当前模型适配器；离线实例的适配器会明确拒绝真实调用。
    model_port: object | None = None

    # Existing runtime accessors resolve the same services as the mounted feature.
    @property
    def configurations(self) -> ConfigurationService:
        return registered_service(self.feature_services, "configurations", ConfigurationServices).configurations

    @property
    def routing(self) -> RoutingService | None:
        feature = self.feature_services.get("configurations")
        return feature.routing if isinstance(feature, ConfigurationServices) else None

    @property
    def credentials(self) -> CredentialService:
        return registered_service(self.feature_services, "credentials", CredentialService)

    @property
    def operations(self) -> OperationsService:
        return registered_service(self.feature_services, "system", OperationsService)

    async def logs(self, query: LogQuery) -> Record:
        return await self.operations.logs(asdict(query))

    @property
    def media(self) -> MediaService | None:
        feature = self.feature_services.get("media")
        return feature if isinstance(feature, MediaService) else None

    @property
    def android(self) -> AndroidService | None:
        feature = self.feature_services.get("android")
        return feature if isinstance(feature, AndroidService) else None

    @property
    def bilibili(self) -> BilibiliService | None:
        feature = self.feature_services.get("bilibili")
        return feature if isinstance(feature, BilibiliService) else None

    @property
    def qq(self) -> QQService | None:
        feature = self.feature_services.get("qq")
        return feature if isinstance(feature, QQService) else None

    @property
    def schedules(self) -> SchedulingService:
        feature = self.feature_services.get("schedules")
        if not isinstance(feature, SchedulingService):
            raise BusinessError("scheduler_unavailable", "Scheduling service is unavailable", status=503)
        return feature

    @property
    def images(self) -> ImageService | None:
        feature = self.feature_services.get("vision")
        return feature.images if isinstance(feature, VisionServices) else None

    @property
    def memory_semantics(self) -> MemorySemanticService | None:
        feature = self.feature_services.get("memory")
        return feature.semantics if isinstance(feature, MemoryServices) else None

    @property
    def memory_organization(self) -> MemoryOrganizationService:
        return registered_service(self.feature_services, "memory", MemoryServices).organization

    @property
    def background_review(self) -> BackgroundReviewService:
        return registered_service(self.feature_services, "learning", LearningServices).review

    @property
    def reflections(self) -> ReflectionService:
        return registered_service(self.feature_services, "learning", LearningServices).reflections

    @property
    def maintenance(self) -> MaintenanceService:
        return registered_service(self.feature_services, "maintenance", MaintenanceService)

    @property
    def people(self) -> PeopleService:
        return registered_service(self.feature_services, "people", PeopleServices).people

    @property
    def persona_growth(self) -> PersonaGrowthService:
        return registered_service(self.feature_services, "people", PeopleServices).persona

    @property
    def documents(self) -> DocumentService:
        return registered_service(self.feature_services, "documents", DocumentService)

    @property
    def deliveries(self) -> DeliveryService:
        return registered_service(self.feature_services, "deliveries", DeliveryService)

    @property
    def action_reviews(self) -> ActionReviewService:
        return registered_service(self.feature_services, "action-reviews", ActionReviewService)

    @property
    def terminals(self) -> TerminalService:
        return registered_service(self.feature_services, "terminals", TerminalServices).terminals

    @property
    def terminal_reviews(self) -> TerminalReviewService:
        return registered_service(self.feature_services, "terminals", TerminalServices).reviews

    @property
    def skills(self) -> SkillService | None:
        service = self.feature_services.get("skills")
        return service if isinstance(service, SkillService) else None

    @property
    def extensions(self) -> ExtensionService:
        return require_extension_service(self.feature_services)

    @property
    def mcp(self) -> MCPService | None:
        service = self.feature_services.get("mcp")
        return service if isinstance(service, MCPService) else None

    @property
    def desktop(self) -> DesktopService:
        return require_desktop_services(self.feature_services).observations

    @property
    def desktop_portal(self) -> DesktopPortalService:
        return require_desktop_services(self.feature_services).portal

    @property
    def desktop_control(self) -> DesktopControlService:
        return require_desktop_services(self.feature_services).control

    @property
    def desktop_actions(self) -> DesktopActions:
        return require_desktop_services(self.feature_services).actions


class AgentInput(StrictInput):
    name: str = Field(min_length=1, max_length=MAX_NAME)


class AgentRenameInput(AgentInput):
    revision: int = Field(ge=1, le=MAX_REVISION, strict=True)


class TurnInput(StrictInput):
    session_id: uuid.UUID | None = None
    key: str = Field(min_length=1, max_length=200)
    message: str = Field(default="", max_length=200_000)
    image_ids: list[uuid.UUID] = Field(default_factory=cast(Callable[[], list[uuid.UUID]], list), max_length=4)
    mode: Literal["auto", "chat", "retrieval", "agent"] | None = None
    skill_ids: list[uuid.UUID] = Field(default_factory=cast(Callable[[], list[uuid.UUID]], list), max_length=4)


class ApprovalInput(StrictInput):
    allowed: bool
    policy_revision: int = Field(ge=1)


class ClarifyRespondInput(StrictInput):
    answers: dict[str, object]
    revision: int = Field(ge=1)
    policy_revision: int = Field(ge=1)


class ClarifyCancelInput(StrictInput):
    revision: int = Field(ge=1)
    notice: str = Field(default="", max_length=2000)


class ObjectPinInput(StrictInput):
    pinned: bool = Field(strict=True)
    revision: int = Field(ge=1, strict=True)


def create_app(
    factory: Callable[[], Awaitable[HttpRuntime]],
    *,
    allowed_origins: tuple[str, ...] = (),
    feature_routes: tuple[FeatureRoutes, ...] = (),
) -> FastAPI:
    event_streams = EventStreams()

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
    app.state.event_streams = event_streams

    def runtime(request: Request) -> HttpRuntime:
        return request.app.state.runtime

    async def authenticate(request: Request) -> None:
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer ") or not await runtime(request).authenticate(header[7:]):
            raise BusinessError("unauthenticated", "A valid owner token is required", status=401)

    @app.middleware("http")
    async def boundary(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        started = time.monotonic()
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        origin = request.headers.get("origin")
        same = str(request.base_url).rstrip("/")
        if origin and origin != same and origin not in allowed_origins:
            runtime(request).observe("request", "error", time.monotonic() - started)
            return JSONResponse(
                {"error": {"code": "origin_denied", "message": "Origin is not trusted", "request_id": request_id}},
                status_code=403,
            )
        with bind_context(request_id=request_id):
            try:
                response = await call_next(request)
            except Exception as exc:  # noqa: BLE001 -- final HTTP boundary logs once and returns a correlated error
                response = await internal_error(request, exc)
        runtime(request).observe(
            "request", "error" if response.status_code >= 400 else "ok", time.monotonic() - started
        )
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
        with bind_context(request_id=getattr(request.state, "request_id", "")):
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

    @app.get("/health/identity")
    async def identity_proof(request: Request, challenge: str = Query(pattern=r"^[a-f0-9]{64}$", max_length=64)):
        try:
            local = request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
        except ValueError:
            local = False
        proof = runtime(request).identity_proof
        if not local or proof is None:
            raise BusinessError("service_proof_unavailable", "Readiness proof is only available locally", status=403)
        return proof(challenge)

    @app.post("/api/v1/service/drain")
    async def drain(request: Request):
        try:
            local = request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
        except ValueError:
            local = False
        current = runtime(request)
        if not local or current.drain is None:
            raise BusinessError("service_drain_unavailable", "Reviewed boot control is unavailable here", status=403)
        return await current.drain(await bounded_configuration(request, 4096, strict=True))

    router = APIRouter(prefix="/api/v1", dependencies=[Depends(authenticate)])

    @app.get("/oauth/callback", include_in_schema=False)
    async def oauth_callback(
        request: Request,
        state: str = Query(default="", max_length=200),
        code: str = Query(default="", max_length=4096),
        error: str = Query(default="", max_length=200),
    ):
        """Loopback-only OAuth return target; the state is single use and the code never echoes."""

        def page(message: str) -> HTMLResponse:
            body = (
                '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
                "<title>OmniOperator MCP 授权</title></head><body><main><h1>MCP 授权</h1><p>"
                + html.escape(message)
                + "</p></main></body></html>"
            )
            return HTMLResponse(body)

        service = runtime(request).mcp
        if service is None:
            return page("本实例未启用 MCP。")
        if error:
            return page("授权服务返回了错误，请回到工作台重新发起。")
        try:
            await service.oauth_callback(state, code)
        except BusinessError as exc:
            response = page("授权未完成：" + exc.message)
            response.status_code = exc.status
            return response
        return page("授权已完成。请回到工作台重新连接并复查工具。")

    @router.get("/version")
    async def version(request: Request):
        return runtime(request).describe()

    @router.get("/capabilities")
    async def capabilities(request: Request):
        return capability_report(runtime(request).describe())

    # 注意：router 整体挂了 Depends(authenticate)，引导端点必须注册在 app 上而非 router，
    # 否则会被它要引导的那道鉴权自己拦住。它自身有下面三重本机闸门。
    @app.get("/api/v1/owner-token/local")
    async def owner_token_local(request: Request):
        """本机免粘贴引导：让「启动服务后直接可用」回到 V1 的体验。

        V1 靠配对 Cookie 自动携带凭证；V2 改用 Bearer 令牌后，前端却没有获取通道，
        使用者每次都要去实例目录翻 owner.token 再手工粘贴。此端点补上这条通道。

        三重闸门，缺一不可：
        ① 装配时显式提供 local_owner_token（远程部署不提供，直接 503）；
        ② 客户端 IP 必须是回环地址，且没有代理转发头（反代后一律拒绝）；
        ③ 必须同源——浏览器地址栏直开无 Origin，脚本跨源请求的 Origin 会被拒。
        令牌只在此响应里出现一次，前端仅存内存，不写任何浏览器存储。
        """
        operation = runtime(request).local_owner_token
        if operation is None:
            raise BusinessError("owner_token_unavailable", "Local token bootstrap is unavailable", status=503)
        if any(header in request.headers for header in ("x-forwarded-for", "x-real-ip", "forwarded")):
            raise BusinessError("local_only", "Local bootstrap is not available through a proxy", status=403)
        client = request.client.host if request.client else ""
        if client not in {"127.0.0.1", "::1", "localhost"}:
            raise BusinessError("local_only", "Local bootstrap requires a loopback client", status=403)
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise BusinessError("local_only", "Local bootstrap requires a same-origin request", status=403)
        return {"token": operation(), "scope": "loopback"}

    @router.get("/owner-token/sessions")
    async def owner_token_sessions(request: Request):
        operation = runtime(request).token_sessions
        if operation is None:
            raise BusinessError("owner_token_unavailable", "Owner token sessions are unavailable", status=503)
        return {"items": await operation()}

    @router.post("/owner-token/rotate")
    async def owner_token_rotate(request: Request):
        operation = runtime(request).rotate_token
        if operation is None:
            raise BusinessError("owner_token_unavailable", "Owner token rotation is unavailable", status=503)
        return await operation()

    @router.get("/agents")
    async def agents(request: Request):
        return {"items": await runtime(request).application.query("agents", "")}

    @router.post("/agents", status_code=201)
    async def create_agent(request: Request):
        data = configuration_input(AgentInput, await bounded_configuration(request, 2048, strict=True))
        return await runtime(request).application.create_agent(data.name)

    @router.get("/agents/{agent_id}")
    async def get_agent(agent_id: uuid.UUID, request: Request):
        return await runtime(request).application.get_agent(str(agent_id))

    @router.put("/agents/{agent_id}")
    async def rename_agent(agent_id: uuid.UUID, request: Request):
        data = configuration_input(AgentRenameInput, await bounded_configuration(request, 2048, strict=True))
        return await runtime(request).application.rename_agent(str(agent_id), data.name, data.revision)

    @router.get("/agents/{agent_id}/runs/{run_id}/context")
    async def run_context(agent_id: uuid.UUID, run_id: uuid.UUID, request: Request):
        return await runtime(request).application.context_report(str(agent_id), str(run_id))

    @router.delete("/agents/{agent_id}/messages/{message_id}")
    async def erase_message(agent_id: uuid.UUID, message_id: uuid.UUID, request: Request):
        await runtime(request).application.erase_message(str(agent_id), str(message_id))
        return {"erased": True}

    @router.get("/agents/{agent_id}/summaries/{summary_id}")
    async def summary(agent_id: uuid.UUID, summary_id: uuid.UUID, request: Request):
        return await runtime(request).application.summary(str(agent_id), str(summary_id))

    @router.post("/agents/{agent_id}/objects", status_code=201)
    async def upload_object(agent_id: uuid.UUID, request: Request, pinned: bool = True):
        upload_slots = runtime(request).upload_slots
        if upload_slots.locked():
            raise BusinessError("upload_busy", "Upload slots are full", retryable=True, status=503)
        await upload_slots.acquire()
        try:
            content = bytearray()
            async with asyncio.timeout(30):
                async for chunk in request.stream():
                    if len(content) + len(chunk) > MAX_OBJECT_BYTES:
                        raise BusinessError("object_too_large", "Object exceeds the 16 MiB limit", status=413)
                    content.extend(chunk)
            mime = request.headers.get("content-type", "application/octet-stream").split(";", 1)[0].strip().lower()
            return await runtime(request).objects.publish(str(agent_id), bytes(content), mime, pinned=pinned)
        except TimeoutError as exc:
            raise BusinessError("upload_timeout", "Upload exceeded its time budget", status=408) from exc
        finally:
            upload_slots.release()

    @router.get("/agents/{agent_id}/objects")
    async def list_objects(
        agent_id: uuid.UUID,
        request: Request,
        after: uuid.UUID | None = None,
        limit: int = Query(default=100, ge=1, le=200),
    ):
        return await runtime(request).objects.page(str(agent_id), after=str(after) if after else "", limit=limit)

    @router.get("/agents/{agent_id}/objects/{object_id}")
    async def describe_object(agent_id: uuid.UUID, object_id: uuid.UUID, request: Request):
        return await runtime(request).objects.describe(str(agent_id), str(object_id))

    @router.get("/agents/{agent_id}/objects/{object_id}/content")
    async def download_object(agent_id: uuid.UUID, object_id: uuid.UUID, request: Request):
        _, content = await runtime(request).objects.get(str(agent_id), str(object_id))
        return Response(
            content,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{object_id}.bin"'},
        )

    @router.put("/agents/{agent_id}/objects/{object_id}/pin")
    async def pin_object(agent_id: uuid.UUID, object_id: uuid.UUID, data: ObjectPinInput, request: Request):
        return await runtime(request).objects.pin(str(agent_id), str(object_id), data.pinned, data.revision)

    @router.post("/agents/{agent_id}/turns", status_code=202)
    async def turn(agent_id: uuid.UUID, data: TurnInput, request: Request):
        return await runtime(request).application.submit_turn(
            str(agent_id),
            str(data.session_id) if data.session_id else None,
            data.key,
            data.message,
            tuple(str(x) for x in data.image_ids),
            data.mode,
            tuple(str(x) for x in data.skill_ids),
        )

    @router.get("/agents/{agent_id}/sessions")
    async def sessions(agent_id: uuid.UUID, request: Request):
        return {"items": await runtime(request).application.query("sessions", str(agent_id))}

    @router.get("/agents/{agent_id}/sessions/{session_id}/messages")
    async def messages(agent_id: uuid.UUID, session_id: uuid.UUID, request: Request):
        return {"items": await runtime(request).application.messages(str(agent_id), str(session_id))}

    @router.get("/agents/{agent_id}/sessions/{session_id}/todos")
    async def session_todos(agent_id: uuid.UUID, session_id: uuid.UUID, request: Request):
        return await runtime(request).application.session_todos(str(agent_id), str(session_id))

    @router.get("/agents/{agent_id}/runs/{run_id}/delegations")
    async def run_delegations(agent_id: uuid.UUID, run_id: uuid.UUID, request: Request):
        return {"items": await runtime(request).application.delegations(str(agent_id), str(run_id))}

    @router.get("/agents/{agent_id}/runs")
    async def runs(agent_id: uuid.UUID, request: Request):
        return {"items": await runtime(request).application.query("runs", str(agent_id))}

    @router.get("/agents/{agent_id}/runs/{run_id}")
    async def run(agent_id: uuid.UUID, run_id: uuid.UUID, request: Request):
        return await runtime(request).application.get_run(str(agent_id), str(run_id))

    @router.get("/agents/{agent_id}/runs/{run_id}/live-output")
    async def model_live_output(agent_id: uuid.UUID, run_id: uuid.UUID, request: Request):
        return await runtime(request).application.live_output(str(agent_id), str(run_id))

    @router.get("/agents/{agent_id}/events/stream")
    async def event_stream(
        agent_id: uuid.UUID,
        request: Request,
        run_id: uuid.UUID | None = None,
        cursor: str = Query(default="", max_length=1024),
    ):
        if "after" in request.query_params:
            raise BusinessError("invalid_history_query", "Use a history cursor instead of after", status=422)
        position = continuation(cursor, request.headers.get("last-event-id", ""))
        current = runtime(request)
        token = request.headers.get("authorization", "")[7:]

        async def history(value: str) -> Record:
            return await current.application.history(
                "events", str(agent_id), run_id=str(run_id) if run_id else "", cursor=value, limit=EVENT_PAGE_SIZE
            )

        return await event_streams.open(
            history,
            lambda: current.authenticate(token),
            lambda: current.describe().get("closing") is True,
            request.is_disconnected,
            position,
        )

    @router.get("/agents/{agent_id}/runs/{run_id}/events")
    async def events(
        agent_id: uuid.UUID,
        run_id: uuid.UUID,
        request: Request,
        cursor: str = Query(default="", max_length=1024),
        limit: int = Query(default=100, ge=1, le=200),
    ):
        application = runtime(request).application
        if "after" in request.query_params:
            raise BusinessError("invalid_history_query", "Use a history cursor instead of after", status=422)
        return await application.history("events", str(agent_id), run_id=str(run_id), cursor=cursor, limit=limit)

    @router.post("/agents/{agent_id}/runs/{run_id}/cancel")
    async def cancel(agent_id: uuid.UUID, run_id: uuid.UUID, request: Request):
        await runtime(request).application.cancel(str(agent_id), str(run_id))
        return {"accepted": True}

    @router.get("/agents/{agent_id}/context/constants")
    async def context_constants(agent_id: uuid.UUID, request: Request):
        """只读暴露 token 驱动压缩的固定边界，供上下文系统页展示；不调用模型。"""
        return {
            "context_chars": CONTEXT_CHARS,
            "summary_trigger_messages": SUMMARY_TRIGGER_MESSAGES,
            "summary_calls_per_turn": SUMMARY_CALLS_PER_TURN,
        }

    @router.get("/agents/{agent_id}/sessions/{session_id}/context-overview")
    async def session_context_overview(agent_id: uuid.UUID, session_id: uuid.UUID, request: Request):
        """会话级上下文总览（对齐 V1 上下文页）：窗口估算/分解/缓存统计/固定提示词，只读。"""
        return await runtime(request).application.session_context_overview(str(agent_id), str(session_id))

    @router.get("/agents/{agent_id}/approvals")
    async def approvals(agent_id: uuid.UUID, request: Request):
        return {"items": await runtime(request).application.approvals(str(agent_id))}

    @router.post("/agents/{agent_id}/approvals/{approval_id}")
    async def approve(agent_id: uuid.UUID, approval_id: uuid.UUID, data: ApprovalInput, request: Request):
        await runtime(request).application.approve(str(agent_id), str(approval_id), data.allowed, data.policy_revision)
        return {"resolved": True}

    @router.get("/agents/{agent_id}/clarifications")
    async def clarifications(agent_id: uuid.UUID, request: Request):
        return {"items": await runtime(request).application.clarifications(str(agent_id))}

    @router.post("/agents/{agent_id}/clarifications/{clarification_id}/respond")
    async def respond_clarify(
        agent_id: uuid.UUID, clarification_id: uuid.UUID, data: ClarifyRespondInput, request: Request
    ):
        result = await runtime(request).application.respond_clarify(
            str(agent_id), str(clarification_id), data.answers, data.revision, data.policy_revision
        )
        return {"resolved": True, "state": result["state"], "revision": result["revision"]}

    @router.post("/agents/{agent_id}/clarifications/{clarification_id}/cancel")
    async def cancel_clarify(
        agent_id: uuid.UUID, clarification_id: uuid.UUID, data: ClarifyCancelInput, request: Request
    ):
        result = await runtime(request).application.cancel_clarify(
            str(agent_id), str(clarification_id), data.revision, data.notice
        )
        return {"resolved": True, "state": result["state"], "revision": result["revision"]}

    @router.get("/agents/{agent_id}/sessions/{session_id}/captures")
    async def session_captures(
        agent_id: uuid.UUID,
        session_id: uuid.UUID,
        request: Request,
        after: uuid.UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return await runtime(request).objects.captures(
            str(agent_id), str(session_id), str(after) if after else "", limit
        )

    @router.delete("/agents/{agent_id}/sessions/{session_id}/captures/{capture_id}", status_code=204)
    async def release_capture(agent_id: uuid.UUID, session_id: uuid.UUID, capture_id: uuid.UUID, request: Request):
        await runtime(request).objects.release_capture(str(agent_id), str(session_id), str(capture_id))

    mount_feature_routes(router, feature_routes, runtime, host_routes=app.routes)
    app.include_router(router)

    @app.get("/", include_in_schema=False)
    async def index(request: Request):
        return FileResponse(runtime(request).assets / "ui/v2/index.html")

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
