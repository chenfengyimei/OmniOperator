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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import Field

from ..application.action_review import ActionReviewService
from ..application.background_review import BackgroundReviewService
from ..application.bilibili import BilibiliService
from ..application.configuration import ConfigurationService
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
from ..application.maintenance import MaintenanceService
from ..application.mcp import MCPService
from ..application.memory_organization import MemoryOrganizationService
from ..application.memory_semantics import MemorySemanticService
from ..application.objects import ObjectService
from ..application.operations import OperationsService
from ..application.people import PeopleService
from ..application.persona import PersonaGrowthService
from ..application.ports import Record
from ..application.qq import QQService
from ..application.reflections import ReflectionService
from ..application.routing import RoutingService
from ..application.scheduling import SchedulingService
from ..application.skills import SkillService
from ..application.terminal_review import TerminalReviewService
from ..application.terminals import TerminalService
from ..domain.agents import MAX_NAME, MAX_REVISION
from ..domain.capabilities import capability_report
from ..domain.context import CONTEXT_CHARS, SUMMARY_CALLS_PER_TURN, SUMMARY_TRIGGER_MESSAGES
from ..domain.contracts import BusinessError
from ..domain.objects import MAX_OBJECT_BYTES
from .diagnostics import bind_context
from .http_events import PAGE_SIZE as EVENT_PAGE_SIZE
from .http_events import EventStreams, continuation
from .http_routes import FeatureRoutes, StrictInput, bounded_configuration, configuration_input, mount_feature_routes
from .http_routes.extensions import extensions_router
from .http_routes.media import MediaJobInput as MediaJobInput
from .http_routes.media import MediaSourceInput as MediaSourceInput
from .http_routes.media import MediaUploadSourceInput as MediaUploadSourceInput
from .http_routes.skills import skills_router
from .log_query import LogQuery

log = logging.getLogger("omni.v2.http")
Payload = dict[str, object]


@dataclass
class HttpRuntime:
    application: Application
    authenticate: Callable[[str], Awaitable[bool]]
    describe: Callable[[], Record]
    close: Callable[[], Awaitable[Record]]
    logs: Callable[[LogQuery], Awaitable[Record]]
    assets: Path
    objects: ObjectService
    people: PeopleService
    configurations: ConfigurationService
    documents: DocumentService
    schedules: SchedulingService
    deliveries: DeliveryService
    operations: OperationsService
    credentials: CredentialService
    maintenance: MaintenanceService
    memory_organization: MemoryOrganizationService
    background_review: BackgroundReviewService
    reflections: ReflectionService
    persona_growth: PersonaGrowthService
    extensions: ExtensionService
    action_reviews: ActionReviewService
    terminals: TerminalService
    terminal_reviews: TerminalReviewService
    desktop: DesktopService
    desktop_portal: DesktopPortalService
    desktop_control: DesktopControlService
    desktop_actions: DesktopActions
    identity_proof: Callable[[str], Record] | None = None
    drain: Callable[[Record], Awaitable[Record]] | None = None
    token_sessions: Callable[[], Awaitable[list[Record]]] | None = None
    rotate_token: Callable[[], Awaitable[Record]] | None = None
    # 本机免粘贴引导：只在回环地址 + 同源时下发所有者令牌，让「启动即用」回到 V1 体验。
    # 默认 None 表示关闭；远程绑定与跨源请求一律拿不到，API 鉴权强度不变。
    local_owner_token: Callable[[], str] | None = None
    qq: QQService | None = None
    bilibili: BilibiliService | None = None
    mcp: MCPService | None = None
    feature_services: Mapping[str, object] = field(default_factory=dict[str, object])
    images: ImageService | None = None
    routing: RoutingService | None = None
    memory_semantics: MemorySemanticService | None = None
    skills: SkillService | None = None
    # 连接测试复用当前模型适配器；离线实例的适配器会明确拒绝真实调用。
    model_port: object | None = None


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


class RoutingInput(StrictInput):
    revision: int = Field(ge=0, strict=True)
    value: Payload


class SettingsInput(StrictInput):
    revision: int = Field(ge=1, strict=True)
    value: Payload


class ConfigurationInput(StrictInput):
    kind: Literal["model", "channel", "mcp", "preset"]
    name: str = Field(min_length=1, max_length=100)
    enabled: bool = Field(default=False, strict=True)
    value: Payload
    image_policy: Payload | None = None


class ConfigurationEdit(StrictInput):
    name: str = Field(min_length=1, max_length=100)
    enabled: bool = Field(strict=True)
    revision: int = Field(ge=1, strict=True)
    value: Payload
    image_policy: Payload | None = None


class DeliveryInput(StrictInput):
    message_id: uuid.UUID
    config_id: uuid.UUID
    target: str = Field(min_length=1, max_length=512)
    key: str = Field(min_length=1, max_length=200)
    parent_id: uuid.UUID | None = None
    acknowledge_duplicate: bool = Field(default=False, strict=True)


class QQBindingInput(StrictInput):
    config_id: uuid.UUID
    scene: Literal["private", "group"]
    peer_id: str = Field(min_length=1, max_length=19)
    enabled: bool = Field(strict=True)
    persona: str = Field(min_length=1, max_length=8000)
    cooldown_s: int = Field(ge=1, le=3600, strict=True)
    revision: int = Field(default=0, ge=0, strict=True)


class BilibiliProfileInput(StrictInput):
    enabled: bool = Field(strict=True)
    headless: bool = Field(default=True, strict=True)
    forms: Payload
    revision: int = Field(ge=0, strict=True)


class BilibiliReadInput(StrictInput):
    config_id: uuid.UUID
    operation: Literal[
        "account",
        "video",
        "comments",
        "replies",
        "search",
        "whois",
        "relation",
        "dynamic_list",
        "danmaku",
        "popular",
        "ranking",
        "following",
        "favorites",
        "fav_resources",
        "tags",
        "history",
        "msgfeed",
    ]
    target: str = Field(default="", max_length=100)
    limit: int = Field(default=10, ge=1, le=20, strict=True)


class BilibiliPublicationInput(StrictInput):
    config_id: uuid.UUID
    message_id: uuid.UUID
    kind: Literal["comment", "private_message"]
    target: str = Field(max_length=500)
    mentions: list[Payload] = Field(default_factory=cast(Callable[[], list[Payload]], list), max_length=10)
    key: str = Field(min_length=1, max_length=180)


class MCPConnectInput(StrictInput):
    revision: int = Field(ge=1, strict=True)


class MCPToolAuthorizationInput(StrictInput):
    tool_id: uuid.UUID
    read_only: bool = Field(strict=True)


class MCPAuthorizationInput(StrictInput):
    catalog_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    tools: list[MCPToolAuthorizationInput] = Field(
        default_factory=cast(Callable[[], list[MCPToolAuthorizationInput]], list), max_length=256
    )
    evidence: str = Field(min_length=1, max_length=200)


class MCPToolDecisionInput(StrictInput):
    catalog_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    read_only: bool = Field(default=False, strict=True)
    authorized: bool = Field(default=True, strict=True)
    evidence: str = Field(min_length=1, max_length=200)


class MCPOAuthStartInput(StrictInput):
    redirect_uri: str = Field(default="", max_length=2000)


class DeliveryDecision(StrictInput):
    allowed: bool = Field(strict=True)
    revision: int = Field(ge=1, strict=True)
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class DeliveryReview(StrictInput):
    revision: int = Field(ge=1, strict=True)
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: Literal["confirmed_sent", "confirmed_not_sent", "allow_future"]
    evidence: str = Field(min_length=1, max_length=2000)


class DebugInput(StrictInput):
    duration_s: int = Field(default=3600, ge=1, le=3600, strict=True)


class ScheduleInput(StrictInput):
    name: str = Field(min_length=1, max_length=100)
    enabled: bool = Field(default=False, strict=True)
    value: Payload
    revision: int = Field(default=0, ge=0, strict=True)


class ScheduleReview(StrictInput):
    occurrence_id: uuid.UUID
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


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


class MemoryInput(StrictInput):
    content: str = Field(min_length=1, max_length=20_000)
    message_id: uuid.UUID | None = None
    quote: str = Field(default="", max_length=20_000)
    action_id: uuid.UUID | None = None
    document_revision_id: uuid.UUID | None = None


class SourceDocumentInput(StrictInput):
    document_id: uuid.UUID | None = None
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=100000)
    revision: int = Field(default=0, ge=0, strict=True)


class SourceDocumentImport(StrictInput):
    title: str = Field(min_length=1, max_length=200)
    object_id: uuid.UUID
    document_id: uuid.UUID | None = None
    revision: int = Field(default=0, ge=0, strict=True)


class SourceIndexInput(StrictInput):
    kind: Literal["full_text", "vector"]


class CredentialInput(StrictInput):
    value: str = Field(min_length=1, max_length=65_536)


class CredentialRegistration(StrictInput):
    reference: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.:/-]+$")


class CredentialReview(StrictInput):
    revision: int = Field(ge=1, strict=True)
    authority_epoch: int = Field(ge=1, strict=True)
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy: str = Field(min_length=1, max_length=100)
    decision: Literal["allow_current", "revoke"]
    evidence: str = Field(min_length=1, max_length=2000)


class ObjectPinInput(StrictInput):
    pinned: bool = Field(strict=True)
    revision: int = Field(ge=1, strict=True)


class RunPinInput(StrictInput):
    pinned: bool = Field(strict=True)
    revision: int = Field(ge=1, strict=True)


class PersonInput(StrictInput):
    name: str = Field(min_length=1, max_length=100)


class PersonEdit(PersonInput):
    revision: int = Field(ge=1, strict=True)


class AccountInput(StrictInput):
    channel: str = Field(min_length=1, max_length=32)
    external_id: str = Field(min_length=1, max_length=200)


class DocumentVersion(StrictInput):
    entry_id: uuid.UUID | None
    revision: int = Field(ge=0, strict=True)


class DocumentInput(DocumentVersion):
    content: str = Field(max_length=8000)


class DocumentImport(DocumentVersion):
    object_id: uuid.UUID


def create_app(
    factory: Callable[[], Awaitable[HttpRuntime]],
    *,
    allowed_origins: tuple[str, ...] = (),
    feature_routes: tuple[FeatureRoutes, ...] = (),
) -> FastAPI:
    upload_slots = asyncio.Semaphore(4)
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
            runtime(request).operations.observe("request", "error", time.monotonic() - started)
            return JSONResponse(
                {"error": {"code": "origin_denied", "message": "Origin is not trusted", "request_id": request_id}},
                status_code=403,
            )
        with bind_context(request_id=request_id):
            try:
                response = await call_next(request)
            except Exception as exc:  # noqa: BLE001 -- final HTTP boundary logs once and returns a correlated error
                response = await internal_error(request, exc)
        runtime(request).operations.observe(
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

    def qq_service(request: Request) -> QQService:
        service = runtime(request).qq
        if service is None:
            raise BusinessError("qq_unavailable", "QQ channel is unavailable", status=503)
        return service

    def bilibili_service(request: Request) -> BilibiliService:
        service = runtime(request).bilibili
        if service is None:
            raise BusinessError("bilibili_unavailable", "Bilibili capability is unavailable", status=503)
        return service

    @router.get("/agents/{agent_id}/bilibili/profiles/{config_id}")
    async def bilibili_profile(agent_id: uuid.UUID, config_id: uuid.UUID, request: Request):
        return await bilibili_service(request).profile(str(agent_id), str(config_id))

    @router.put("/agents/{agent_id}/bilibili/profiles/{config_id}")
    async def bilibili_profile_save(agent_id: uuid.UUID, config_id: uuid.UUID, request: Request):
        data = configuration_input(BilibiliProfileInput, await bounded_configuration(request, 65536, strict=True))
        return await bilibili_service(request).save_profile(
            str(agent_id), str(config_id), data.enabled, data.forms, data.revision, data.headless
        )

    @router.get("/agents/{agent_id}/bilibili/risk/{config_id}")
    async def bilibili_risk_state(agent_id: uuid.UUID, config_id: uuid.UUID, request: Request):
        return await bilibili_service(request).risk_state(str(agent_id), str(config_id))

    @router.post("/agents/{agent_id}/bilibili/risk/{config_id}/resume")
    async def bilibili_risk_resume(agent_id: uuid.UUID, config_id: uuid.UUID, request: Request):
        return await bilibili_service(request).resume(str(agent_id), str(config_id))

    @router.post("/agents/{agent_id}/bilibili/read")
    async def bilibili_read(agent_id: uuid.UUID, request: Request):
        data = configuration_input(BilibiliReadInput, await bounded_configuration(request, 4096, strict=True))
        return await bilibili_service(request).read(
            str(agent_id), str(data.config_id), data.operation, data.target, data.limit
        )

    @router.post("/agents/{agent_id}/bilibili/publications", status_code=201)
    async def bilibili_prepare(agent_id: uuid.UUID, request: Request):
        data = configuration_input(BilibiliPublicationInput, await bounded_configuration(request, 16384, strict=True))
        return await bilibili_service(request).prepare(
            str(agent_id),
            str(data.message_id),
            str(data.config_id),
            data.kind,
            data.target,
            cast(list[Mapping[str, object]], data.mentions),
            data.key,
        )

    @router.get("/agents/{agent_id}/bilibili/publications")
    async def bilibili_publications(
        agent_id: uuid.UUID,
        request: Request,
        after: uuid.UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return await bilibili_service(request).page(str(agent_id), str(after) if after else "", limit)

    @router.get("/agents/{agent_id}/bilibili/publications/{identity}")
    async def bilibili_publication(agent_id: uuid.UUID, identity: uuid.UUID, request: Request):
        return await bilibili_service(request).get(str(agent_id), str(identity))

    def mcp_service(request: Request) -> MCPService:
        service = runtime(request).mcp
        if service is None:
            raise BusinessError("mcp_unavailable", "MCP capability is unavailable", status=503)
        return service

    @router.get("/agents/{agent_id}/mcp/servers")
    async def mcp_servers(agent_id: uuid.UUID, request: Request):
        return await mcp_service(request).servers(str(agent_id))

    @router.get("/agents/{agent_id}/mcp/servers/{config_id}")
    async def mcp_server(agent_id: uuid.UUID, config_id: uuid.UUID, request: Request):
        return await mcp_service(request).server(str(agent_id), str(config_id))

    @router.post("/agents/{agent_id}/mcp/servers/{config_id}/connect")
    async def mcp_connect(agent_id: uuid.UUID, config_id: uuid.UUID, request: Request):
        data = configuration_input(MCPConnectInput, await bounded_configuration(request, 1024, strict=True))
        return await mcp_service(request).connect(str(agent_id), str(config_id), data.revision)

    @router.post("/agents/{agent_id}/mcp/servers/{config_id}/disconnect")
    async def mcp_disconnect(agent_id: uuid.UUID, config_id: uuid.UUID, request: Request):
        return await mcp_service(request).disconnect(str(agent_id), str(config_id))

    @router.post("/agents/{agent_id}/mcp/servers/{config_id}/reset")
    async def mcp_reset(agent_id: uuid.UUID, config_id: uuid.UUID, request: Request):
        return await mcp_service(request).reset(str(agent_id), str(config_id))

    @router.get("/agents/{agent_id}/mcp/servers/{config_id}/catalog")
    async def mcp_catalog(
        agent_id: uuid.UUID,
        config_id: uuid.UUID,
        request: Request,
        after: int = Query(default=-1, ge=-1),
        limit: int = Query(default=20, ge=1, le=100),
    ):
        return await mcp_service(request).catalog(str(agent_id), str(config_id), after, limit)

    @router.put("/agents/{agent_id}/mcp/servers/{config_id}/authorization")
    async def mcp_authorize(agent_id: uuid.UUID, config_id: uuid.UUID, request: Request):
        data = configuration_input(MCPAuthorizationInput, await bounded_configuration(request, 65536, strict=True))
        return await mcp_service(request).authorize(
            str(agent_id),
            str(config_id),
            {
                "catalog_fingerprint": data.catalog_fingerprint,
                "tools": [{"tool_id": str(item.tool_id), "read_only": item.read_only} for item in data.tools],
                "evidence": data.evidence,
            },
        )

    @router.put("/agents/{agent_id}/mcp/servers/{config_id}/tools/{tool_id}/authorization")
    async def mcp_authorize_tool(agent_id: uuid.UUID, config_id: uuid.UUID, tool_id: uuid.UUID, request: Request):
        data = configuration_input(MCPToolDecisionInput, await bounded_configuration(request, 4096, strict=True))
        return await mcp_service(request).authorize_tool(
            str(agent_id),
            str(config_id),
            str(tool_id),
            authorized=data.authorized,
            read_only=data.read_only,
            catalog_fingerprint=data.catalog_fingerprint,
            evidence=data.evidence,
        )

    @router.get("/agents/{agent_id}/mcp/servers/{config_id}/calls")
    async def mcp_calls(
        agent_id: uuid.UUID,
        config_id: uuid.UUID,
        request: Request,
        after: uuid.UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return await mcp_service(request).calls(str(agent_id), str(config_id), str(after) if after else "", limit)

    @router.post("/agents/{agent_id}/mcp/servers/{config_id}/oauth/start")
    async def mcp_oauth_start(agent_id: uuid.UUID, config_id: uuid.UUID, request: Request):
        data = configuration_input(MCPOAuthStartInput, await bounded_configuration(request, 4096, strict=True))
        redirect = data.redirect_uri or str(request.base_url).rstrip("/") + "/oauth/callback"
        return await mcp_service(request).oauth_start(str(agent_id), str(config_id), redirect)

    @router.get("/agents/{agent_id}/mcp/health")
    async def mcp_health(agent_id: uuid.UUID, request: Request):
        return mcp_service(request).health()

    @router.get("/agents/{agent_id}/qq/bindings")
    async def qq_bindings(
        agent_id: uuid.UUID,
        request: Request,
        after: uuid.UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return await qq_service(request).page(str(agent_id), str(after) if after else "", limit)

    @router.get("/agents/{agent_id}/qq/status")
    async def qq_status(agent_id: uuid.UUID, request: Request):
        return await qq_service(request).status(str(agent_id))

    @router.post("/agents/{agent_id}/qq/bindings", status_code=201)
    async def qq_binding_create(agent_id: uuid.UUID, data: QQBindingInput, request: Request):
        if data.revision != 0:
            raise BusinessError("revision_conflict", "New QQ grants start at revision zero", status=422)
        return await qq_service(request).save(
            str(agent_id), str(data.config_id), data.model_dump(exclude={"config_id", "revision"})
        )

    @router.put("/agents/{agent_id}/qq/bindings/{binding_id}")
    async def qq_binding_edit(agent_id: uuid.UUID, binding_id: uuid.UUID, data: QQBindingInput, request: Request):
        return await qq_service(request).save(
            str(agent_id),
            str(data.config_id),
            data.model_dump(exclude={"config_id", "revision"}),
            str(binding_id),
            data.revision,
        )

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

    @router.get("/agents/{agent_id}/deliveries/channels")
    async def delivery_channels(
        agent_id: uuid.UUID,
        request: Request,
        after: uuid.UUID | None = None,
        limit: int = Query(default=100, ge=1, le=200),
    ):
        return await runtime(request).deliveries.channels(str(agent_id), str(after) if after else "", limit)

    @router.get("/agents/{agent_id}/deliveries/capabilities")
    async def delivery_capabilities(agent_id: uuid.UUID, request: Request):
        return await runtime(request).deliveries.capabilities(str(agent_id))

    @router.get("/agents/{agent_id}/deliveries")
    async def deliveries(
        agent_id: uuid.UUID,
        request: Request,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=200),
        include_archived: bool = False,
    ):
        rows = await runtime(request).deliveries.page(str(agent_id), after, limit, include_archived)
        return {"items": rows, "next_after": rows[-1]["queue_sequence"] if len(rows) == limit else None}

    @router.post("/agents/{agent_id}/deliveries", status_code=201)
    async def create_delivery(agent_id: uuid.UUID, request: Request):
        data = configuration_input(DeliveryInput, await bounded_configuration(request, 8192))
        return await runtime(request).deliveries.create(
            str(agent_id),
            str(data.message_id),
            str(data.config_id),
            data.target,
            data.key,
            str(data.parent_id) if data.parent_id else None,
            data.acknowledge_duplicate,
        )

    @router.get("/agents/{agent_id}/deliveries/{delivery_id}")
    async def delivery(agent_id: uuid.UUID, delivery_id: uuid.UUID, request: Request):
        return await runtime(request).deliveries.get(str(agent_id), str(delivery_id))

    @router.get("/agents/{agent_id}/deliveries/{delivery_id}/attempts")
    async def delivery_attempts(
        agent_id: uuid.UUID,
        delivery_id: uuid.UUID,
        request: Request,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=200),
    ):
        rows = await runtime(request).deliveries.attempts(str(agent_id), str(delivery_id), after, limit)
        return {"items": rows, "next_after": rows[-1]["seq"] if len(rows) == limit else None}

    @router.post("/agents/{agent_id}/deliveries/{delivery_id}/decision")
    async def decide_delivery(agent_id: uuid.UUID, delivery_id: uuid.UUID, request: Request):
        data = configuration_input(DeliveryDecision, await bounded_configuration(request, 2048))
        return await runtime(request).deliveries.decide(
            str(agent_id), str(delivery_id), data.allowed, data.revision, data.fingerprint
        )

    @router.post("/agents/{agent_id}/deliveries/{delivery_id}/cancel")
    async def cancel_delivery(agent_id: uuid.UUID, delivery_id: uuid.UUID, request: Request):
        return await runtime(request).deliveries.cancel(str(agent_id), str(delivery_id))

    @router.post("/agents/{agent_id}/deliveries/{delivery_id}/review")
    async def review_delivery(agent_id: uuid.UUID, delivery_id: uuid.UUID, request: Request):
        data = configuration_input(DeliveryReview, await bounded_configuration(request, 16384))
        return await runtime(request).deliveries.review(
            str(agent_id), str(delivery_id), data.revision, data.fingerprint, data.decision, data.evidence
        )

    @router.delete("/agents/{agent_id}/deliveries/{delivery_id}")
    async def archive_delivery(
        agent_id: uuid.UUID, delivery_id: uuid.UUID, request: Request, revision: int = Query(ge=1)
    ):
        await runtime(request).deliveries.archive(str(agent_id), str(delivery_id), revision)
        return {"ok": True}

    @router.get("/agents/{agent_id}/schedules")
    async def schedules(
        agent_id: uuid.UUID,
        request: Request,
        after: uuid.UUID | None = None,
        limit: int = Query(default=100, ge=1, le=200),
        include_archived: bool = False,
    ):
        items = await runtime(request).schedules.page(
            str(agent_id), str(after) if after else "", limit, include_archived
        )
        return {"items": items, "next_after": items[-1]["id"] if len(items) == limit else None}

    @router.post("/agents/{agent_id}/schedules", status_code=201)
    async def create_schedule(agent_id: uuid.UUID, request: Request):
        data = configuration_input(ScheduleInput, await bounded_configuration(request, 100000))
        return await runtime(request).schedules.save(
            str(agent_id), data.name, data.enabled, data.value, revision=data.revision
        )

    @router.get("/agents/{agent_id}/schedules/{schedule_id}")
    async def schedule(agent_id: uuid.UUID, schedule_id: uuid.UUID, request: Request):
        return await runtime(request).schedules.get(str(agent_id), str(schedule_id))

    @router.put("/agents/{agent_id}/schedules/{schedule_id}")
    async def save_schedule(agent_id: uuid.UUID, schedule_id: uuid.UUID, request: Request):
        data = configuration_input(ScheduleInput, await bounded_configuration(request, 100000))
        return await runtime(request).schedules.save(
            str(agent_id), data.name, data.enabled, data.value, str(schedule_id), data.revision
        )

    @router.delete("/agents/{agent_id}/schedules/{schedule_id}")
    async def archive_schedule(
        agent_id: uuid.UUID, schedule_id: uuid.UUID, request: Request, revision: int = Query(ge=1)
    ):
        await runtime(request).schedules.archive(str(agent_id), str(schedule_id), revision)
        return {"archived": True}

    @router.get("/agents/{agent_id}/schedules/{schedule_id}/history")
    async def schedule_history(
        agent_id: uuid.UUID,
        schedule_id: uuid.UUID,
        request: Request,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=200),
    ):
        items = await runtime(request).schedules.history(str(agent_id), str(schedule_id), after, limit)
        return {"items": items, "next_after": items[-1]["revision"] if len(items) == limit else None}

    @router.get("/agents/{agent_id}/schedules/{schedule_id}/occurrences")
    async def schedule_occurrences(
        agent_id: uuid.UUID,
        schedule_id: uuid.UUID,
        request: Request,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=200),
    ):
        items = await runtime(request).schedules.occurrences(str(agent_id), str(schedule_id), after, limit)
        return {"items": items, "next_after": items[-1]["sequence"] if len(items) == limit else None}

    @router.post("/agents/{agent_id}/schedules/{schedule_id}/review")
    async def schedule_review(agent_id: uuid.UUID, schedule_id: uuid.UUID, request: Request):
        data = configuration_input(ScheduleReview, await bounded_configuration(request, 1000))
        await runtime(request).schedules.review(
            str(agent_id), str(schedule_id), str(data.occurrence_id), data.fingerprint
        )
        return {"reviewed": True, "unknown_records_preserved": True}

    @router.post("/agents/{agent_id}/schedules/{schedule_id}/occurrences/{occurrence_id}/cancel")
    async def schedule_cancel(agent_id: uuid.UUID, schedule_id: uuid.UUID, occurrence_id: uuid.UUID, request: Request):
        await runtime(request).schedules.cancel(str(agent_id), str(schedule_id), str(occurrence_id))
        return {"accepted": True}

    @router.get("/agents/{agent_id}/configurations")
    async def configurations(
        agent_id: uuid.UUID,
        request: Request,
        kind: Literal["model", "channel", "mcp", "preset"] = "model",
        after: uuid.UUID | None = None,
        limit: int = Query(default=100, ge=1, le=200),
    ):
        return await runtime(request).configurations.page(str(agent_id), kind, str(after) if after else "", limit)

    def routing_service(request: Request) -> RoutingService:
        service = runtime(request).routing
        if service is None:
            raise BusinessError("routing_unavailable", "Routing service is unavailable", status=503)
        return service

    @router.get("/agents/{agent_id}/routing")
    async def route_policy(agent_id: uuid.UUID, request: Request):
        return await routing_service(request).get(str(agent_id))

    @router.put("/agents/{agent_id}/routing")
    async def save_route_policy(agent_id: uuid.UUID, data: RoutingInput, request: Request):
        return await routing_service(request).save(str(agent_id), cast(Record, data.value), data.revision)

    @router.get("/agents/{agent_id}/runs/{run_id}/routing")
    async def route_report(agent_id: uuid.UUID, run_id: uuid.UUID, request: Request):
        return await routing_service(request).report(str(agent_id), str(run_id))

    @router.post("/agents/{agent_id}/configurations", status_code=201)
    async def create_configuration(agent_id: uuid.UUID, request: Request):
        data = configuration_input(ConfigurationInput, await bounded_configuration(request))
        return await runtime(request).configurations.create(
            str(agent_id),
            data.kind,
            data.name,
            data.enabled,
            cast(Record, data.value),
            cast(Record | None, data.image_policy),
        )

    @router.get("/agents/{agent_id}/configurations/template")
    async def configuration_template(
        agent_id: uuid.UUID, request: Request, kind: Literal["model", "channel", "mcp", "preset"] = "model"
    ):
        await runtime(request).application.settings(str(agent_id))
        return {"schema": 1, **runtime(request).configurations.template(kind)}

    @router.post("/agents/{agent_id}/configurations/preview")
    async def preview_configuration(agent_id: uuid.UUID, request: Request):
        await runtime(request).application.settings(str(agent_id))
        return runtime(request).configurations.preview(await bounded_configuration(request))

    @router.get("/agents/{agent_id}/configurations/{config_id}")
    async def get_configuration(agent_id: uuid.UUID, config_id: uuid.UUID, request: Request):
        return await runtime(request).configurations.get(str(agent_id), str(config_id))

    @router.put("/agents/{agent_id}/configurations/{config_id}")
    async def save_configuration(agent_id: uuid.UUID, config_id: uuid.UUID, request: Request):
        data = configuration_input(ConfigurationEdit, await bounded_configuration(request))
        return await runtime(request).configurations.save(
            str(agent_id),
            str(config_id),
            data.name,
            data.enabled,
            cast(Record, data.value),
            data.revision,
            cast(Record | None, data.image_policy),
        )

    @router.delete("/agents/{agent_id}/configurations/{config_id}")
    async def archive_configuration(
        agent_id: uuid.UUID, config_id: uuid.UUID, request: Request, revision: int = Query(ge=1)
    ):
        await runtime(request).configurations.archive(str(agent_id), str(config_id), revision)
        return {"archived": True, "history_retained": True}

    @router.get("/agents/{agent_id}/configurations/{config_id}/revisions")
    async def configuration_history(
        agent_id: uuid.UUID,
        config_id: uuid.UUID,
        request: Request,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return await runtime(request).configurations.history(str(agent_id), str(config_id), after, limit)

    @router.get("/agents/{agent_id}/configurations/{config_id}/diagnosis")
    async def configuration_diagnosis(agent_id: uuid.UUID, config_id: uuid.UUID, request: Request):
        return await runtime(request).configurations.diagnose(str(agent_id), str(config_id))

    @router.get("/agents/{agent_id}/configurations/{config_id}/preset")
    async def apply_preset(agent_id: uuid.UUID, config_id: uuid.UUID, request: Request):
        return await runtime(request).configurations.preset(str(agent_id), str(config_id))

    @router.post("/agents/{agent_id}/configurations/{config_id}/probe")
    async def configuration_probe(agent_id: uuid.UUID, config_id: uuid.UUID, request: Request):
        """连接测试：用所存凭据向端点发起一次最小真实调用，验证 Key/端点/模型可用。

        不创建运行、不落库调用台账；离线实例的适配器会明确拒绝（offline_model_denied）。
        先经 diagnose 校验凭据授权与配置启用，避免未授权凭据被尝试使用。
        """
        rt = runtime(request)
        row = await rt.configurations.get(str(agent_id), str(config_id))
        if row["kind"] != "model":
            raise BusinessError("probe_not_model", "Only model configurations can be probed", status=422)
        diagnosis = await rt.configurations.diagnose(str(agent_id), str(config_id))
        if not diagnosis.get("configuration_enabled"):
            raise BusinessError("probe_disabled", "Enable the model configuration before probing", status=409)
        if not diagnosis.get("available"):
            raise BusinessError(
                "probe_unavailable",
                f"Model is not ready: {diagnosis.get('state', 'unknown')}",
                status=409,
            )
        port = rt.model_port
        if port is None:
            raise BusinessError("probe_unavailable", "No model adapter is wired in this instance", status=503)
        fields = dict(cast(Payload, row["value"]))
        # 最小调用：输出上限压到 16 token，避免测试产生明显费用。
        fields["output_tokens"] = min(int(cast(int, fields.get("output_tokens", 16))), 16)
        settings: Payload = {
            "persona": "",
            "workspace": "",
            "daily_budget_micro": 0,
            "call_reservation_micro": 1,
            "max_steps": 1,
            "disabled_tools": [],
            "model_profile_id": str(config_id),
            "model_revision_id": str(row.get("revision_id", "")),
            "model_permission_epoch": 1,
            **fields,
        }
        from ..domain.settings import settings_value

        resolved = settings_value(settings)
        complete = getattr(port, "complete", None)
        if complete is None:
            raise BusinessError("probe_unavailable", "Model adapter does not support probing", status=503)
        reply = await complete([{"role": "user", "content": "Reply with the single word: ok"}], [], resolved)
        return {
            "ok": True,
            "model": fields.get("model", ""),
            "content": str(getattr(reply, "content", "") or "")[:120],
            "completion_reason": getattr(reply, "completion_reason", "stop"),
        }

    @router.get("/agents/{agent_id}/settings/revisions")
    async def settings_history(
        agent_id: uuid.UUID,
        request: Request,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return await runtime(request).configurations.settings_history(str(agent_id), after, limit)

    @router.get("/agents/{agent_id}/runs/{run_id}/configuration")
    async def run_configuration(agent_id: uuid.UUID, run_id: uuid.UUID, request: Request):
        return await runtime(request).application.run_configuration(str(agent_id), str(run_id))

    @router.get("/agents/{agent_id}/documents")
    async def source_documents(
        agent_id: uuid.UUID,
        request: Request,
        after: uuid.UUID | None = None,
        limit: int = Query(default=100, ge=1, le=200),
    ):
        return await runtime(request).documents.page(str(agent_id), str(after) if after else "", limit)

    @router.post("/agents/{agent_id}/documents", status_code=201)
    async def source_document_create(agent_id: uuid.UUID, request: Request):
        data = configuration_input(SourceDocumentInput, await bounded_configuration(request, 1048576))
        return await runtime(request).documents.save(
            str(agent_id),
            data.title,
            data.content,
            document_id=str(data.document_id) if data.document_id else None,
            revision=data.revision,
        )

    @router.post("/agents/{agent_id}/documents/import", status_code=201)
    async def source_document_import(agent_id: uuid.UUID, request: Request):
        data = configuration_input(SourceDocumentImport, await bounded_configuration(request))
        return await runtime(request).documents.import_object(
            str(agent_id),
            data.title,
            str(data.object_id),
            str(data.document_id) if data.document_id else None,
            data.revision,
        )

    @router.get("/agents/{agent_id}/documents/search")
    async def source_document_search(
        agent_id: uuid.UUID,
        request: Request,
        q: str = Query(min_length=1, max_length=500),
        limit: int = Query(default=20, ge=1, le=20),
    ):
        return await runtime(request).documents.search(str(agent_id), q, limit)

    @router.get("/agents/{agent_id}/documents/{document_id}")
    async def source_document_get(
        agent_id: uuid.UUID, document_id: uuid.UUID, request: Request, revision_id: uuid.UUID | None = None
    ):
        return await runtime(request).documents.get(
            str(agent_id), str(document_id), str(revision_id) if revision_id else None
        )

    @router.put("/agents/{agent_id}/documents/{document_id}")
    async def source_document_save(agent_id: uuid.UUID, document_id: uuid.UUID, request: Request):
        data = configuration_input(SourceDocumentInput, await bounded_configuration(request, 1048576))
        if data.document_id and data.document_id != document_id:
            raise BusinessError("invalid_document", "Document identity must match the request target", status=422)
        return await runtime(request).documents.save(
            str(agent_id), data.title, data.content, str(document_id), data.revision
        )

    @router.delete("/agents/{agent_id}/documents/{document_id}")
    async def source_document_erase(
        agent_id: uuid.UUID, document_id: uuid.UUID, request: Request, revision: int = Query(ge=1)
    ):
        await runtime(request).documents.erase(str(agent_id), str(document_id), revision)
        return {"erased": True}

    @router.get("/agents/{agent_id}/documents/{document_id}/revisions")
    async def source_document_history(
        agent_id: uuid.UUID,
        document_id: uuid.UUID,
        request: Request,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return await runtime(request).documents.history(str(agent_id), str(document_id), after, limit)

    @router.get("/agents/{agent_id}/documents/{document_id}/indexes")
    async def source_document_indexes(
        agent_id: uuid.UUID,
        document_id: uuid.UUID,
        request: Request,
        after: uuid.UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return await runtime(request).documents.index_status(
            str(agent_id), str(document_id), str(after) if after else "", limit
        )

    @router.post("/agents/{agent_id}/documents/{document_id}/indexes")
    async def source_document_rebuild(agent_id: uuid.UUID, document_id: uuid.UUID, request: Request):
        data = configuration_input(SourceIndexInput, await bounded_configuration(request))
        return await runtime(request).documents.rebuild(str(agent_id), str(document_id), data.kind)

    @router.get("/agents/{agent_id}/people/owner")
    async def owner_person(agent_id: uuid.UUID, request: Request):
        return await runtime(request).people.owner(str(agent_id))

    @router.get("/agents/{agent_id}/people")
    async def list_people(
        agent_id: uuid.UUID,
        request: Request,
        after: uuid.UUID | None = None,
        limit: int = Query(default=100, ge=1, le=200),
    ):
        return await runtime(request).people.page(str(agent_id), str(after) if after else "", limit)

    @router.post("/agents/{agent_id}/people", status_code=201)
    async def create_person(agent_id: uuid.UUID, data: PersonInput, request: Request):
        return await runtime(request).people.create(str(agent_id), data.name)

    @router.get("/agents/{agent_id}/people/{person_id}")
    async def get_person(agent_id: uuid.UUID, person_id: uuid.UUID, request: Request):
        return await runtime(request).people.get(str(agent_id), str(person_id))

    @router.put("/agents/{agent_id}/people/{person_id}")
    async def rename_person(agent_id: uuid.UUID, person_id: uuid.UUID, data: PersonEdit, request: Request):
        return await runtime(request).people.rename(str(agent_id), str(person_id), data.name, data.revision)

    @router.delete("/agents/{agent_id}/people/{person_id}")
    async def erase_person(agent_id: uuid.UUID, person_id: uuid.UUID, request: Request, revision: int = Query(ge=1)):
        await runtime(request).people.erase(str(agent_id), str(person_id), revision)
        return {"erased": True}

    @router.get("/agents/{agent_id}/people/{person_id}/accounts")
    async def person_accounts(
        agent_id: uuid.UUID,
        person_id: uuid.UUID,
        request: Request,
        after: uuid.UUID | None = None,
        limit: int = Query(default=100, ge=1, le=200),
    ):
        return await runtime(request).people.accounts(str(agent_id), str(person_id), str(after) if after else "", limit)

    @router.post("/agents/{agent_id}/people/{person_id}/accounts", status_code=201)
    async def bind_account(agent_id: uuid.UUID, person_id: uuid.UUID, data: AccountInput, request: Request):
        return await runtime(request).people.bind(str(agent_id), str(person_id), data.channel, data.external_id)

    @router.delete("/agents/{agent_id}/people/{person_id}/accounts/{account_id}")
    async def unbind_account(
        agent_id: uuid.UUID, person_id: uuid.UUID, account_id: uuid.UUID, request: Request, revision: int = Query(ge=1)
    ):
        await runtime(request).people.unbind(str(agent_id), str(person_id), str(account_id), revision)
        return {"unlinked": True}

    @router.get("/agents/{agent_id}/people/{person_id}/documents/{kind}")
    async def personal_document(
        agent_id: uuid.UUID, person_id: uuid.UUID, kind: Literal["profile", "resident"], request: Request
    ):
        return await runtime(request).people.document(str(agent_id), str(person_id), kind)

    @router.put("/agents/{agent_id}/people/{person_id}/documents/{kind}")
    async def save_personal_document(
        agent_id: uuid.UUID,
        person_id: uuid.UUID,
        kind: Literal["profile", "resident"],
        data: DocumentInput,
        request: Request,
    ):
        return await runtime(request).people.save(
            str(agent_id),
            str(person_id),
            kind,
            data.content,
            str(data.entry_id) if data.entry_id else None,
            data.revision,
        )

    @router.post("/agents/{agent_id}/people/{person_id}/documents/{kind}/import")
    async def import_personal_document(
        agent_id: uuid.UUID,
        person_id: uuid.UUID,
        kind: Literal["profile", "resident"],
        data: DocumentImport,
        request: Request,
    ):
        return await runtime(request).people.import_object(
            str(agent_id),
            str(person_id),
            kind,
            str(data.object_id),
            str(data.entry_id) if data.entry_id else None,
            data.revision,
        )

    @router.get("/agents/{agent_id}/memory/{memory_id}/revisions")
    async def memory_revisions(
        agent_id: uuid.UUID,
        memory_id: uuid.UUID,
        request: Request,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return await runtime(request).people.history(str(agent_id), str(memory_id), after, limit)

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

    def image_service(request: Request) -> ImageService:
        service = runtime(request).images
        if service is None:
            raise BusinessError("image_protocol_unavailable", "Image admission is unavailable", status=503)
        return service

    @router.post("/agents/{agent_id}/images", status_code=201)
    async def upload_image(agent_id: uuid.UUID, request: Request):
        service = image_service(request)
        if upload_slots.locked():
            raise BusinessError("upload_busy", "Upload slots are full", status=503)
        await upload_slots.acquire()
        try:
            content = bytearray()
            async with asyncio.timeout(30):
                async for chunk in request.stream():
                    if len(content) + len(chunk) > MAX_OBJECT_BYTES:
                        raise BusinessError("image_too_large", "Image exceeds the 16 MiB limit", status=413)
                    content.extend(chunk)
            return await service.upload(str(agent_id), bytes(content))
        except TimeoutError as exc:
            raise BusinessError("upload_timeout", "Upload exceeded its time budget", status=408) from exc
        finally:
            upload_slots.release()

    @router.post("/agents/{agent_id}/images/{object_id}/prepare")
    async def prepare_image(agent_id: uuid.UUID, object_id: uuid.UUID, request: Request):
        return await image_service(request).prepare(str(agent_id), str(object_id))

    @router.get("/agents/{agent_id}/runs/{run_id}/images")
    async def run_images(agent_id: uuid.UUID, run_id: uuid.UUID, request: Request):
        return await image_service(request).report(str(agent_id), str(run_id))

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

    @router.post("/agents/{agent_id}/maintenance", status_code=202)
    async def maintenance_submit(agent_id: uuid.UUID, request: Request):
        return await runtime(request).maintenance.submit(str(agent_id), await bounded_configuration(request, 4096))

    @router.get("/agents/{agent_id}/maintenance")
    async def maintenance_list(
        agent_id: uuid.UUID,
        request: Request,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return await runtime(request).maintenance.page(str(agent_id), after, limit)

    @router.get("/agents/{agent_id}/maintenance/{job_id}")
    async def maintenance_get(agent_id: uuid.UUID, job_id: uuid.UUID, request: Request):
        return await runtime(request).maintenance.get(str(agent_id), str(job_id))

    @router.post("/agents/{agent_id}/maintenance/{job_id}/cancel")
    async def maintenance_cancel(agent_id: uuid.UUID, job_id: uuid.UUID, request: Request):
        data = await bounded_configuration(request, 2048)
        if set(data) != {"revision"} or type(cast(int, data["revision"])) is not int or cast(int, data["revision"]) < 1:
            raise BusinessError("maintenance_invalid", "Cancel requires the current positive revision", status=422)
        return await runtime(request).maintenance.cancel(str(agent_id), str(job_id), cast(int, data["revision"]))

    @router.get("/agents/{agent_id}/events/retention-preview")
    async def event_retention_preview(
        agent_id: uuid.UUID,
        request: Request,
        cursor: str = Query(default="", max_length=1024),
        limit: int = Query(default=20, ge=1, le=50),
        as_of: str = Query(default="", max_length=64),
    ):
        if "after" in request.query_params:
            raise BusinessError(
                "invalid_event_retention_query", "Use the event-preview cursor instead of after", status=422
            )
        return await runtime(request).application.event_retention_preview(
            str(agent_id), cursor=cursor, limit=limit, as_of=as_of
        )

    @router.get("/agents/{agent_id}/audit/retention-preview")
    async def audit_retention_preview(
        agent_id: uuid.UUID,
        request: Request,
        cursor: str = Query(default="", max_length=1024),
        limit: int = Query(default=20, ge=1, le=50),
        as_of: str = Query(default="", max_length=64),
    ):
        if "after" in request.query_params:
            raise BusinessError("invalid_audit_retention_query", "Use the preview cursor instead of after", status=422)
        return await runtime(request).application.audit_retention_preview(
            str(agent_id), cursor=cursor, limit=limit, as_of=as_of
        )

    @router.get("/agents/{agent_id}/runs/{run_id}/retention")
    async def run_retention(agent_id: uuid.UUID, run_id: uuid.UUID, request: Request):
        return await runtime(request).application.run_retention(str(agent_id), str(run_id))

    @router.put("/agents/{agent_id}/runs/{run_id}/retention-pin")
    async def run_retention_pin(agent_id: uuid.UUID, run_id: uuid.UUID, request: Request):
        data = configuration_input(RunPinInput, await bounded_configuration(request, 2048))
        return await runtime(request).application.pin_run(str(agent_id), str(run_id), data.pinned, data.revision)

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

    @router.get("/agents/{agent_id}/settings")
    async def settings(agent_id: uuid.UUID, request: Request):
        return await runtime(request).application.settings(str(agent_id))

    @router.put("/agents/{agent_id}/settings")
    async def save_settings(agent_id: uuid.UUID, request: Request):
        data = configuration_input(SettingsInput, await bounded_configuration(request))
        return await runtime(request).application.save_settings(str(agent_id), cast(Record, data.value), data.revision)

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

    @router.get("/agents/{agent_id}/desktop")
    async def desktop_status(agent_id: uuid.UUID, request: Request):
        return await runtime(request).desktop.status(str(agent_id))

    @router.post("/agents/{agent_id}/desktop/view")
    async def desktop_begin(agent_id: uuid.UUID, request: Request):
        return await runtime(request).desktop.begin(str(agent_id), await bounded_configuration(request, 512))

    @router.post("/agents/{agent_id}/desktop/probe")
    async def desktop_probe(agent_id: uuid.UUID, request: Request):
        return await runtime(request).desktop.probe(str(agent_id), await bounded_configuration(request, 512))

    @router.post("/agents/{agent_id}/desktop/targets")
    async def desktop_targets(agent_id: uuid.UUID, request: Request):
        return await runtime(request).desktop.targets(str(agent_id), await bounded_configuration(request, 512))

    @router.post("/agents/{agent_id}/desktop/verify")
    async def desktop_verify(agent_id: uuid.UUID, request: Request):
        return await runtime(request).desktop.verify(str(agent_id), await bounded_configuration(request, 512))

    @router.post("/agents/{agent_id}/desktop/observe")
    async def desktop_observe(agent_id: uuid.UUID, request: Request):
        return await runtime(request).desktop.observe(str(agent_id), await bounded_configuration(request, 512))

    @router.post("/agents/{agent_id}/desktop/capture")
    async def desktop_capture(agent_id: uuid.UUID, request: Request):
        return await runtime(request).desktop.capture(str(agent_id), await bounded_configuration(request, 512))

    @router.post("/agents/{agent_id}/desktop/portal", status_code=202)
    async def desktop_portal_open(agent_id: uuid.UUID, request: Request):
        return await runtime(request).desktop_portal.open(str(agent_id), await bounded_configuration(request, 512))

    @router.get("/agents/{agent_id}/desktop/portal")
    async def desktop_portal_status(agent_id: uuid.UUID, view_id: uuid.UUID, request: Request):
        return await runtime(request).desktop_portal.status(str(agent_id), str(view_id))

    @router.post("/agents/{agent_id}/desktop/portal/capture")
    async def desktop_portal_capture(agent_id: uuid.UUID, request: Request):
        return await runtime(request).desktop_portal.capture(str(agent_id), await bounded_configuration(request, 512))

    @router.delete("/agents/{agent_id}/desktop/portal")
    async def desktop_portal_close(agent_id: uuid.UUID, view_id: uuid.UUID, grant_id: uuid.UUID, request: Request):
        return await runtime(request).desktop_portal.close_grant(str(agent_id), str(view_id), str(grant_id))

    @router.post("/agents/{agent_id}/desktop/control", status_code=202)
    async def desktop_control_open(agent_id: uuid.UUID, request: Request):
        return await runtime(request).desktop_control.open(str(agent_id), await bounded_configuration(request, 512))

    @router.get("/agents/{agent_id}/desktop/control")
    async def desktop_control_status(agent_id: uuid.UUID, view_id: uuid.UUID, request: Request):
        return await runtime(request).desktop_control.status(str(agent_id), str(view_id))

    @router.delete("/agents/{agent_id}/desktop/control")
    async def desktop_control_close(agent_id: uuid.UUID, view_id: uuid.UUID, grant_id: uuid.UUID, request: Request):
        return await runtime(request).desktop_control.close_grant(str(agent_id), str(view_id), str(grant_id))

    @router.post("/agents/{agent_id}/desktop/actions", status_code=202)
    async def desktop_action(agent_id: uuid.UUID, request: Request):
        instance = runtime(request)
        receipt = await instance.desktop_actions.submit(str(agent_id), await bounded_configuration(request, 4096))
        instance.application.notify()
        return receipt

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

    @router.delete("/agents/{agent_id}/desktop/snapshot")
    async def desktop_forget(agent_id: uuid.UUID, view_id: uuid.UUID, request: Request):
        return await runtime(request).desktop.forget(str(agent_id), str(view_id))

    @router.get("/agents/{agent_id}/terminal-reviews")
    async def terminal_review_list(
        agent_id: uuid.UUID, request: Request, after: str = "", limit: int = Query(20, ge=1, le=50)
    ):
        return await runtime(request).terminal_reviews.page(str(agent_id), after, limit)

    @router.get("/agents/{agent_id}/terminal-reviews/{operation_id}")
    async def terminal_review_detail(agent_id: uuid.UUID, operation_id: uuid.UUID, request: Request):
        return await runtime(request).terminal_reviews.get(str(agent_id), str(operation_id))

    @router.get("/agents/{agent_id}/terminal-reviews/{operation_id}/history")
    async def terminal_review_history(
        agent_id: uuid.UUID,
        operation_id: uuid.UUID,
        request: Request,
        after: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=50),
    ):
        return await runtime(request).terminal_reviews.history(str(agent_id), str(operation_id), after, limit)

    @router.post("/agents/{agent_id}/terminal-reviews/{operation_id}", status_code=201)
    async def terminal_review_record(agent_id: uuid.UUID, operation_id: uuid.UUID, request: Request):
        return await runtime(request).terminal_reviews.record(
            str(agent_id), str(operation_id), await bounded_configuration(request, 32768)
        )

    @router.delete("/agents/{agent_id}/terminal-reviews/{operation_id}/history/{review_id}/evidence")
    async def terminal_review_erase_evidence(
        agent_id: uuid.UUID, operation_id: uuid.UUID, review_id: uuid.UUID, request: Request
    ):
        if await bounded_configuration(request, 128) != {}:
            raise BusinessError("terminal_review_invalid", "Evidence erasure requires an empty object", status=422)
        return await runtime(request).terminal_reviews.erase_evidence(str(agent_id), str(operation_id), str(review_id))

    @router.get("/agents/{agent_id}/terminals/options")
    async def terminal_options(agent_id: uuid.UUID, request: Request):
        return await runtime(request).terminals.options(str(agent_id))

    @router.get("/agents/{agent_id}/terminals")
    async def terminal_list(
        agent_id: uuid.UUID,
        request: Request,
        after: int = Query(default=0, ge=0, le=9223372036854775807),
        limit: int = Query(default=20, ge=1, le=50),
    ):
        return await runtime(request).terminals.page(str(agent_id), after, limit)

    @router.post("/agents/{agent_id}/terminals", status_code=201)
    async def terminal_open(agent_id: uuid.UUID, request: Request):
        return await runtime(request).terminals.open(str(agent_id), await bounded_configuration(request, 4096))

    @router.get("/agents/{agent_id}/terminal-operations/{operation_id}")
    async def terminal_lookup(agent_id: uuid.UUID, operation_id: uuid.UUID, request: Request):
        return await runtime(request).terminals.lookup(str(agent_id), str(operation_id))

    @router.get("/agents/{agent_id}/terminals/{terminal_id}")
    async def terminal_detail(agent_id: uuid.UUID, terminal_id: uuid.UUID, request: Request):
        return await runtime(request).terminals.get(str(agent_id), str(terminal_id))

    @router.get("/agents/{agent_id}/terminals/{terminal_id}/operations/{operation_id}")
    async def terminal_receipt(agent_id: uuid.UUID, terminal_id: uuid.UUID, operation_id: uuid.UUID, request: Request):
        return await runtime(request).terminals.receipt(str(agent_id), str(terminal_id), str(operation_id))

    @router.post("/agents/{agent_id}/terminals/{terminal_id}/attach")
    async def terminal_attach(agent_id: uuid.UUID, terminal_id: uuid.UUID, request: Request):
        if await bounded_configuration(request, 1024) != {}:
            raise BusinessError("terminal_invalid", "Attach requires an empty object", status=422)
        return await runtime(request).terminals.attach(str(agent_id), str(terminal_id))

    @router.post("/agents/{agent_id}/terminals/{terminal_id}/detach")
    async def terminal_detach(agent_id: uuid.UUID, terminal_id: uuid.UUID, request: Request):
        data = await bounded_configuration(request, 1024)
        if set(data) != {"connection_id"} or not isinstance(data["connection_id"], str):
            raise BusinessError("terminal_invalid", "Detach requires its captured connection identity", status=422)
        return await runtime(request).terminals.detach(str(agent_id), str(terminal_id), data["connection_id"])

    @router.get("/agents/{agent_id}/terminals/{terminal_id}/output")
    async def terminal_output(
        agent_id: uuid.UUID,
        terminal_id: uuid.UUID,
        request: Request,
        connection_id: uuid.UUID,
        stream_id: uuid.UUID,
        after: int = Query(default=0, ge=0, le=9223372036854775807),
    ):
        return await runtime(request).terminals.output(
            str(agent_id), str(terminal_id), str(connection_id), str(stream_id), after
        )

    @router.post("/agents/{agent_id}/terminals/{terminal_id}/input")
    async def terminal_input(agent_id: uuid.UUID, terminal_id: uuid.UUID, request: Request):
        return await runtime(request).terminals.input(
            str(agent_id), str(terminal_id), await bounded_configuration(request, 65536)
        )

    @router.post("/agents/{agent_id}/terminals/{terminal_id}/resize")
    async def terminal_resize(agent_id: uuid.UUID, terminal_id: uuid.UUID, request: Request):
        return await runtime(request).terminals.resize(
            str(agent_id), str(terminal_id), await bounded_configuration(request, 2048)
        )

    @router.post("/agents/{agent_id}/terminals/{terminal_id}/close")
    async def terminal_close(agent_id: uuid.UUID, terminal_id: uuid.UUID, request: Request):
        return await runtime(request).terminals.end(
            str(agent_id), str(terminal_id), await bounded_configuration(request, 2048)
        )

    @router.get("/agents/{agent_id}/action-reviews")
    async def action_review_list(
        agent_id: uuid.UUID,
        request: Request,
        after: int = Query(default=0, ge=0, le=9223372036854775807),
        limit: int = Query(default=20, ge=1, le=50),
    ):
        return await runtime(request).action_reviews.page(str(agent_id), after, limit)

    @router.get("/agents/{agent_id}/actions/{action_id}/review")
    async def action_review_detail(agent_id: uuid.UUID, action_id: uuid.UUID, request: Request):
        return await runtime(request).action_reviews.get(str(agent_id), str(action_id))

    @router.get("/agents/{agent_id}/actions/{action_id}/reviews")
    async def action_review_history(
        agent_id: uuid.UUID,
        action_id: uuid.UUID,
        request: Request,
        after: int = Query(default=0, ge=0, le=9223372036854775807),
        limit: int = Query(default=20, ge=1, le=50),
    ):
        return await runtime(request).action_reviews.history(str(agent_id), str(action_id), after, limit)

    @router.post("/agents/{agent_id}/actions/{action_id}/review")
    async def action_review_commit(agent_id: uuid.UUID, action_id: uuid.UUID, request: Request):
        return await runtime(request).action_reviews.review(
            str(agent_id), str(action_id), await bounded_configuration(request, 16384)
        )

    router.include_router(extensions_router(runtime, bounded_configuration))
    router.include_router(skills_router(runtime))

    @router.get("/agents/{agent_id}/memory")
    async def memories(agent_id: uuid.UUID, request: Request, q: str = Query(default="", max_length=500)):
        semantic = runtime(request).memory_semantics
        if q and semantic is not None:
            return await semantic.search(str(agent_id), q)
        application = runtime(request).application
        return {
            "items": await application.search_memory(str(agent_id), q)
            if q
            else await application.query("memory", str(agent_id))
        }

    @router.get("/agents/{agent_id}/memory/decay")
    async def memory_decay_state(agent_id: uuid.UUID, request: Request):
        return await runtime(request).application.memory_decay_state(str(agent_id))

    @router.post("/agents/{agent_id}/memory/decay")
    async def memory_decay(agent_id: uuid.UUID, request: Request):
        data = await bounded_configuration(request, 2048, strict=True)
        if set(data) - {"force"} or ("force" in data and type(data["force"]) is not bool):
            raise BusinessError("memory_decay_invalid", "Only an explicit boolean force is accepted", status=422)
        return await runtime(request).application.memory_decay(str(agent_id), bool(data.get("force", False)))

    def memory_semantic_service(request: Request) -> MemorySemanticService:
        service = runtime(request).memory_semantics
        if service is None:
            raise BusinessError("memory_semantics_unavailable", "Memory semantic service is unavailable", status=503)
        return service

    @router.get("/agents/{agent_id}/memory/semantics")
    async def semantic_batches(
        agent_id: uuid.UUID, request: Request, after: str = "", limit: int = Query(default=20, ge=1, le=32)
    ):
        return await memory_semantic_service(request).page(str(agent_id), after, limit)

    @router.post("/agents/{agent_id}/memory/index")
    async def memory_index(
        agent_id: uuid.UUID, request: Request, after: str = "", limit: int = Query(default=32, ge=1, le=32)
    ):
        return await memory_semantic_service(request).index(str(agent_id), after, limit)

    @router.post("/agents/{agent_id}/memory/semantics", status_code=201)
    async def semantic_propose(agent_id: uuid.UUID, request: Request):
        return await memory_semantic_service(request).propose(str(agent_id), await bounded_configuration(request, 8192))

    @router.get("/agents/{agent_id}/memory/semantics/{batch_id}")
    async def semantic_detail(agent_id: uuid.UUID, batch_id: uuid.UUID, request: Request):
        return await memory_semantic_service(request).get(str(agent_id), str(batch_id))

    @router.post("/agents/{agent_id}/memory/semantic-links/{link_id}/decision")
    async def semantic_decide(agent_id: uuid.UUID, link_id: uuid.UUID, request: Request):
        return await memory_semantic_service(request).decide(
            str(agent_id), str(link_id), await bounded_configuration(request, 2048)
        )

    @router.post("/agents/{agent_id}/memory/organizations", status_code=202)
    async def memory_organization_create(agent_id: uuid.UUID, request: Request):
        return await runtime(request).memory_organization.submit(
            str(agent_id), await bounded_configuration(request, 8192)
        )

    @router.get("/agents/{agent_id}/memory/organizations")
    async def memory_organizations(
        agent_id: uuid.UUID,
        request: Request,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=20, ge=1, le=50),
    ):
        return await runtime(request).memory_organization.page(str(agent_id), after, limit)

    @router.get("/agents/{agent_id}/memory/organizations/{batch_id}")
    async def memory_organization_detail(agent_id: uuid.UUID, batch_id: uuid.UUID, request: Request):
        return await runtime(request).memory_organization.get(str(agent_id), str(batch_id))

    @router.post("/agents/{agent_id}/memory/organizations/{batch_id}/cancel")
    async def memory_organization_cancel(agent_id: uuid.UUID, batch_id: uuid.UUID, request: Request):
        data = await bounded_configuration(request, 2048)
        if set(data) != {"revision"} or type(data.get("revision")) is not int or cast(int, data["revision"]) < 1:
            raise BusinessError(
                "memory_organization_invalid", "A positive cancellation revision is required", status=422
            )
        return await runtime(request).memory_organization.cancel(
            str(agent_id), str(batch_id), cast(int, data["revision"])
        )

    @router.post("/agents/{agent_id}/memory/organizations/candidates/{candidate_id}/decision")
    async def memory_organization_decision(agent_id: uuid.UUID, candidate_id: uuid.UUID, request: Request):
        return await runtime(request).memory_organization.decide(
            str(agent_id), str(candidate_id), await bounded_configuration(request, 2048)
        )

    @router.get("/agents/{agent_id}/reflections")
    async def reflections(
        agent_id: uuid.UUID,
        request: Request,
        after: str = "",
        limit: int = Query(20, ge=1, le=50),
        approved: bool = False,
    ):
        return await runtime(request).reflections.page(str(agent_id), after, limit, approved)

    @router.get("/agents/{agent_id}/reflections/{journal_id}")
    async def reflection_detail(agent_id: uuid.UUID, journal_id: uuid.UUID, request: Request):
        return await runtime(request).reflections.get(str(agent_id), str(journal_id))

    @router.put("/agents/{agent_id}/reflections/{journal_id}")
    async def reflection_edit(agent_id: uuid.UUID, journal_id: uuid.UUID, request: Request):
        return await runtime(request).reflections.edit(
            str(agent_id), str(journal_id), await bounded_configuration(request, 480000)
        )

    @router.post("/agents/{agent_id}/reflections/candidates/{candidate_id}/decision")
    async def reflection_decision(agent_id: uuid.UUID, candidate_id: uuid.UUID, request: Request):
        return await runtime(request).reflections.decide(
            str(agent_id), str(candidate_id), await bounded_configuration(request, 2048)
        )

    @router.get("/agents/{agent_id}/persona-growth")
    async def persona_growth(agent_id: uuid.UUID, request: Request):
        return await runtime(request).persona_growth.read(str(agent_id))

    @router.post("/agents/{agent_id}/persona-growth/rebuild")
    async def persona_growth_rebuild(agent_id: uuid.UUID, request: Request):
        return await runtime(request).persona_growth.rebuild(str(agent_id))

    @router.post("/agents/{agent_id}/memory/reviews", status_code=202)
    async def background_review_create(agent_id: uuid.UUID, request: Request):
        return await runtime(request).background_review.submit(
            str(agent_id), await bounded_configuration(request, 2048)
        )

    @router.get("/agents/{agent_id}/memory/reviews")
    async def background_reviews(
        agent_id: uuid.UUID,
        request: Request,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=20, ge=1, le=50),
    ):
        return await runtime(request).background_review.page(str(agent_id), after, limit)

    @router.get("/agents/{agent_id}/memory/reviews/{batch_id}")
    async def background_review_detail(agent_id: uuid.UUID, batch_id: uuid.UUID, request: Request):
        return await runtime(request).background_review.get(str(agent_id), str(batch_id))

    @router.post("/agents/{agent_id}/memory/reviews/{batch_id}/cancel")
    async def background_review_cancel(agent_id: uuid.UUID, batch_id: uuid.UUID, request: Request):
        data = await bounded_configuration(request, 2048)
        if set(data) != {"revision"} or type(data.get("revision")) is not int or cast(int, data["revision"]) < 1:
            raise BusinessError("background_review_invalid", "A positive cancellation revision is required", status=422)
        return await runtime(request).background_review.cancel(
            str(agent_id), str(batch_id), cast(int, data["revision"])
        )

    @router.post("/agents/{agent_id}/memory/reviews/candidates/{candidate_id}/decision")
    async def background_review_decision(agent_id: uuid.UUID, candidate_id: uuid.UUID, request: Request):
        return await runtime(request).background_review.decide(
            str(agent_id), str(candidate_id), await bounded_configuration(request, 2048)
        )

    @router.post("/agents/{agent_id}/memory", status_code=201)
    async def remember(agent_id: uuid.UUID, data: MemoryInput, request: Request):
        return await runtime(request).application.remember(
            str(agent_id),
            data.content,
            message_id=str(data.message_id) if data.message_id else None,
            quote=data.quote,
            action_id=str(data.action_id) if data.action_id else None,
            document_revision_id=str(data.document_revision_id) if data.document_revision_id else None,
        )

    @router.delete("/agents/{agent_id}/memory/{memory_id}")
    async def forget(agent_id: uuid.UUID, memory_id: uuid.UUID, request: Request, revision: int = Query(ge=1)):
        await runtime(request).application.forget(str(agent_id), str(memory_id), revision)
        return {"deleted": True}

    @router.get("/agents/{agent_id}/audit")
    async def audit(
        agent_id: uuid.UUID,
        request: Request,
        cursor: str = Query(default="", max_length=1024),
        limit: int = Query(default=100, ge=1, le=200),
    ):
        if "after" in request.query_params:
            raise BusinessError("invalid_history_query", "Use a history cursor instead of after", status=422)
        return await runtime(request).application.history("audit", str(agent_id), cursor=cursor, limit=limit)

    @router.get("/system/metrics")
    async def metrics(request: Request):
        return await runtime(request).operations.status()

    @router.get("/agents/{agent_id}/debug")
    async def debug_status(agent_id: uuid.UUID, request: Request):
        return await runtime(request).operations.debug_status(str(agent_id))

    @router.post("/agents/{agent_id}/debug")
    async def debug_start(agent_id: uuid.UUID, request: Request):
        data = configuration_input(DebugInput, await bounded_configuration(request, 1024))
        return await runtime(request).operations.debug_start(str(agent_id), data.duration_s)

    @router.delete("/agents/{agent_id}/debug/{operation_id}")
    async def debug_stop(agent_id: uuid.UUID, operation_id: uuid.UUID, request: Request):
        return await runtime(request).operations.debug_stop(str(agent_id), str(operation_id))

    @router.get("/agents/{agent_id}/debug/{operation_id}/logs")
    async def debug_logs(
        agent_id: uuid.UUID,
        operation_id: uuid.UUID,
        request: Request,
        limit: int = Query(default=100, ge=1, le=500),
        cursor: str = Query(default="", max_length=256),
    ):
        return await runtime(request).operations.debug_logs(
            str(agent_id), str(operation_id), {"limit": limit, "cursor": cursor}
        )

    @router.get("/system/diagnostics")
    async def diagnostic_bundle(request: Request):
        content = await runtime(request).operations.export()
        return Response(
            content,
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="omni-diagnostics.zip"'},
        )

    @router.get("/system/logs")
    async def logs(
        request: Request,
        limit: int = Query(default=100, ge=1, le=500),
        cursor: str = Query(default="", max_length=256),
        level: str = Query(default="", max_length=10),
        component: str = Query(default="", max_length=200),
        run_id: str = Query(default="", max_length=100),
        since: str = Query(default="", max_length=40),
        until: str = Query(default="", max_length=40),
    ):
        return await runtime(request).logs(LogQuery(limit, cursor, level, component, run_id, since, until))

    @router.get("/credentials")
    async def credential_list(
        request: Request, after: str = Query(default="", max_length=36), limit: int = Query(default=100, ge=1, le=200)
    ):
        return await runtime(request).credentials.page(after, limit)

    @router.post("/credentials/register")
    async def credential_register(request: Request):
        data = configuration_input(CredentialRegistration, await bounded_configuration(request, 2048))
        return await runtime(request).credentials.register(data.reference)

    @router.get("/credentials/{identity}")
    async def credential_get(identity: uuid.UUID, request: Request):
        return await runtime(request).credentials.get(str(identity))

    @router.get("/credentials/{identity}/operations")
    async def credential_operations(
        identity: uuid.UUID,
        request: Request,
        after: str = Query(default="", max_length=36),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return await runtime(request).credentials.history(str(identity), after, limit)

    @router.get("/credentials/{identity}/reviews")
    async def credential_reviews(
        identity: uuid.UUID,
        request: Request,
        after: str = Query(default="", max_length=36),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return await runtime(request).credentials.history(str(identity), after, limit, reviews=True)

    @router.post("/credentials/{identity}/review")
    async def credential_review(identity: uuid.UUID, request: Request):
        data = configuration_input(CredentialReview, await bounded_configuration(request, 16384))
        return await runtime(request).credentials.review(
            str(identity),
            data.revision,
            data.authority_epoch,
            data.fingerprint,
            data.policy,
            data.decision,
            data.evidence,
        )

    @router.put("/credentials")
    async def put_credential(
        request: Request, reference: str = Query(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.:/-]+$")
    ):
        data = configuration_input(CredentialInput, await bounded_configuration(request, 266240))
        await runtime(request).credentials.change(reference, data.value)
        return {"saved": True}

    @router.delete("/credentials")
    async def delete_credential(
        request: Request, reference: str = Query(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.:/-]+$")
    ):
        await runtime(request).credentials.change(reference, None)
        return {"deleted": True}

    @router.get("/system/audit")
    async def instance_audit(
        request: Request,
        cursor: str = Query(default="", max_length=1024),
        limit: int = Query(default=100, ge=1, le=200),
    ):
        if "after" in request.query_params:
            raise BusinessError("invalid_history_query", "Use a history cursor instead of after", status=422)
        return await runtime(request).credentials.audit(cursor, limit)

    mount_feature_routes(router, feature_routes, runtime)
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
