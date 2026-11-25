"""Build one isolated instance; never imports or opens the V1 runtime."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sys
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import Span, StatusCode

from omni.adapters.platform.runtime.paths import InstanceLock, InstancePaths
from omni.adapters.platform.runtime.runtime_identity import RuntimeIdentityFiles, canonical_proof, process_identity
from omni.adapters.platform.runtime.secrets import FileVault
from omni.adapters.platform.runtime.workspaces import WorkspacePolicy
from omni.adapters.platform.services.service_launch import consume_launch

from ..adapters.build_identity import CODE_CACHE_DIRS, CODE_SUFFIXES, LAYERS
from ..adapters.credentials import Credentials
from ..adapters.diagnostic_bundle import DiagnosticFiles
from ..adapters.diagnostics import JsonLogSink, LoggingSession, bind_context
from ..adapters.extensions.hosts import ExtensionHosts
from ..adapters.extensions.packages import ExtensionPackages
from ..adapters.images import ImageCodec
from ..adapters.local_vectors import configured_vectors
from ..adapters.mcp.hosts import MCPHosts
from ..adapters.mcp.oauth import OAuthFlow
from ..adapters.metrics import Metrics
from ..adapters.native_credentials import NativeCredentials
from ..adapters.object_store import ObjectFiles
from ..adapters.persistence.database import Database
from ..adapters.persistence.deployed_instance import verify_deployed_instance
from ..adapters.persona_files import PersonaGrowthFiles
from ..adapters.primitives import SystemClock, UUIDs
from ..adapters.process_diagnostics import ProcessDiagnostics
from ..adapters.skill_packages import SkillPackages
from ..adapters.token_counter import ConservativeTokenCounter
from ..adapters.visual import ImageObjectFrames, MediaFrameSheets, ModelCallsVisualModelFactory
from ..application.action_review import ActionReviewService
from ..application.android import AndroidPort, AndroidService
from ..application.background_review import BackgroundReviewService
from ..application.bilibili import BilibiliPort, BilibiliService
from ..application.capabilities import Capability, CapabilityPlugin
from ..application.clarify import ClarifyService
from ..application.configuration import ConfigurationService
from ..application.context import ContextService
from ..application.credentials import CredentialService
from ..application.debugging import DebugService
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
from ..application.mcp import MCPService, MCPTransportPort
from ..application.media import MediaProcessingPort, MediaService
from ..application.memory_organization import MemoryOrganizationService
from ..application.memory_semantics import MemorySemanticService
from ..application.model_calls import ModelCalls
from ..application.objects import ObjectService
from ..application.operations import OperationsService
from ..application.people import PeopleService
from ..application.persona import PersonaGrowthService
from ..application.ports import (
    AsyncSecretPort,
    Clock,
    DeliveryPort,
    DesktopBackendPort,
    DesktopControlPort,
    DesktopPortalPort,
    LocalVectorizer,
    MetricsPort,
    ModelPort,
    ModelStreamingPort,
    Record,
    SecretStore,
    TerminalBackendPort,
    TokenCounter,
    UnitOfWork,
)
from ..application.qq import QQService, QQTransportPort
from ..application.reflections import ReflectionService
from ..application.routing import RoutingService
from ..application.scheduling import SchedulingService
from ..application.skills import SkillService
from ..application.terminal_review import TerminalReviewService
from ..application.terminals import TerminalService
from ..application.todos import TodoService
from ..application.visual_subagent import VISUAL_SUBAGENT, VisualSubagentCapabilities, VisualSubagentService
from ..domain.background_review import READ_TOOLS
from ..domain.contracts import JSON, ActionResult, BusinessError, InvocationContext, Outcome
from ..domain.desktop_actions import SPEC as DESKTOP_ACTION
from ..domain.mcp import ServerTarget
from ..domain.memory_decay import DECAY_DAILY_HOUR
from ..domain.skills import SKILL_SPECS
from .capabilities import builtin_plugins
from .channel_providers import register_channel_providers
from .features import FeatureContext, FeaturePlugin, KernelFeatureContext, install_features
from .features.catalog import BUILTIN_FEATURES, BUILTIN_KERNEL_FEATURES
from .lifecycle import Lifetime, close_sync
from .model_providers import register_model_transport
from .platform_providers import platform_provider


@dataclass
class SpanMeasurement:
    span: Span
    outcome: Outcome = Outcome.OK
    explicit: bool = False

    def result(self, outcome: Outcome) -> None:
        self.outcome, self.explicit = outcome, True
        self.span.set_attribute("omni.outcome", outcome.value)


class Observation:
    def __init__(self, metrics: MetricsPort, clock: Clock) -> None:
        self._metrics, self._clock = metrics, clock
        self.provider = TracerProvider()
        self.tracer = self.provider.get_tracer("omni.v2")

    @contextmanager
    def context(self, **fields: str) -> Generator[None, None, None]:
        with bind_context(**fields):
            yield

    @contextmanager
    def span(self, name: str) -> Generator[SpanMeasurement, None, None]:
        operation = {
            "agent.turn": "turn",
            "model.complete": "model",
            "model.summary": "summary",
            "outbound.send": "delivery",
            "maintenance.document_index_rebuild": "maintenance",
            "maintenance.document_erasure_resume": "maintenance",
            "memory.organization": "maintenance",
            "desktop.probe": "desktop",
            "desktop.targets": "desktop",
            "desktop.verify": "desktop",
            "desktop.observe": "desktop",
            "desktop.capture": "desktop",
        }.get(name, "tool" if name.startswith("tool.") else "")
        started = self._clock.monotonic()
        with self.tracer.start_as_current_span(name, record_exception=False, set_status_on_exception=False) as span:
            report = SpanMeasurement(span)
            try:
                yield report
            except BaseException as exc:
                if not report.explicit:
                    report.result(Outcome.CANCELLED if isinstance(exc, asyncio.CancelledError) else Outcome.ERROR)
                span.set_attribute("error.type", type(exc).__name__)
                raise
            finally:
                if report.outcome in {Outcome.ERROR, Outcome.UNKNOWN}:
                    span.set_status(StatusCode.ERROR)
                if operation:
                    duration = max(0, self._clock.monotonic() - started)
                    self._metrics.observe(operation, report.outcome.value, duration)
                    logging.getLogger("omni.v2.observation").debug(
                        "Execution measurement",
                        extra={
                            "event": "span.completed",
                            "operation": operation,
                            "outcome": report.outcome.value,
                            "duration_ms": duration * 1000,
                        },
                    )


def _record(value: object) -> Record:
    """组合层把适配器健康/度量快照（dict[str, Any]）收敛为显式 JSON 记录视图。"""
    return cast(Record, value)


@dataclass
class Instance:
    application: Application
    paths: InstancePaths

    @property
    def database(self) -> Database:
        """组合层只读访问：单写者数据库 actor。"""
        return self._database

    @property
    def logging(self) -> LoggingSession:
        """组合层只读访问：日志会话（红actioner 与 sink 由其自身维护）。"""
        return self._logging

    @property
    def identity_files(self) -> RuntimeIdentityFiles:
        """组合层只读访问：运行身份文件（排空与指纹由其自身维护）。"""
        return self._identity_files

    @property
    def lifetime(self) -> Lifetime:
        """组合层只读访问：有界关停协调器。"""
        return self._lifetime

    _database: Database
    _lock: InstanceLock
    _logging: LoggingSession
    _observation: Observation
    boot_id: str
    build_id: str
    identity: Record
    _identity_files: RuntimeIdentityFiles
    objects: ObjectService
    images: ImageService
    visual: VisualSubagentService
    people: PeopleService
    configurations: ConfigurationService
    routing: RoutingService
    memory_semantics: MemorySemanticService
    skills: SkillService
    documents: DocumentService
    schedules: SchedulingService
    deliveries: DeliveryService
    qq: QQService
    bilibili: BilibiliService
    media: MediaService
    mcp: MCPService
    android: AndroidService
    operations: OperationsService
    credentials: CredentialService
    _credential_io: AsyncSecretPort
    _lifetime: Lifetime
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
    feature_services: Mapping[str, object]
    _process_diagnostics: ProcessDiagnostics | None = None
    _closed: bool = False
    # 连接测试端口：组合层注入当前模型适配器（真实或离线桩），供 HTTP 层做一次最小调用。
    model_port: ModelPort | None = None

    def identity_proof(self, challenge: str) -> Record:
        return canonical_proof(self.health(), challenge, self._identity_files.key)

    def health(self) -> Record:
        health: Record = {
            **self.application.health(),
            **self.identity,
            "boot_id": self.boot_id,
            "build_id": self.build_id,
            "api_version": 1,
            "schema_version": self._database.schema_version,
            "database": _record(self._database.metrics),
            "logs": _record(self._logging.sink.snapshot()),
            "documents": self.documents.health(),
            "memory_semantics": self.memory_semantics.health(),
            "skills": self.skills.health(),
            "schedules": self.schedules.health(),
            "deliveries": self.deliveries.health(),
            "qq": _record(self.qq.health()),
            "bilibili": _record(self.bilibili.health()),
            "media": self.media.health(),
            "visual": self.visual.health(),
            "mcp": self.mcp.health(),
            "android": self.android.health(),
            "credentials": self.credentials.health(),
            "action_reviews": self.action_reviews.health(),
            "terminals": self.terminals.health(),
            "terminal_reviews": self.terminal_reviews.health(),
            "desktop": self.desktop.health(),
            "desktop_portal": self.desktop_portal.health(),
            "desktop_control": self.desktop_control.health(),
            "desktop_actions": self.desktop_actions.health(),
            "maintenance": self.maintenance.health(),
            "memory_organization": self.memory_organization.health(),
            "background_review": self.background_review.health(),
            "extensions": self.extensions.health(),
            "images": _record(self.images.health()),
            "shutdown_residual": list(self._lifetime.residual),
            "platform_acceptance": "pending",
            "unhandled_exceptions": dict(self._process_diagnostics.counts) if self._process_diagnostics else {},
        }
        health["ready"] = bool(
            health["ready"]
            and self.schedules.health()["ready"]
            and self.deliveries.health()["ready"]
            and self.maintenance.health()["ready"]
            and not self.memory_organization.health()["closing"]
            and not self.background_review.health()["closing"]
            and not self.memory_semantics.health()["closing"]
            and not self.skills.health()["closing"]
            and not self.media.health()["closing"]
            and not self.mcp.health()["closing"]
            and not self._lifetime.closing
            and not self._database.metrics["closed"]
            and not self._database.metrics["error_code"]
        )
        return health

    async def close(self, timeout_s: float = 30) -> Record:
        result = await self._lifetime.close(timeout_s)
        self._closed = result["complete"] is True
        return result


async def open_instance(
    home: Path | None = None,
    *,
    model: ModelPort | None = None,
    model_streamer: ModelStreamingPort | None = None,
    token_counter: TokenCounter | None = None,
    vectorizer: LocalVectorizer | None = None,
    delivery_ports: dict[str, DeliveryPort] | None = None,
    qq_transport: QQTransportPort | None = None,
    bilibili_backend: BilibiliPort | None = None,
    media_processor: MediaProcessingPort | None = None,
    mcp_transport: MCPTransportPort | None = None,
    android_backend: AndroidPort | None = None,
    capabilities: tuple[Capability, ...] | None = None,
    capability_plugins: tuple[CapabilityPlugin, ...] = (),
    feature_plugins: tuple[FeaturePlugin[FeatureContext], ...] = (),
    delegation_enabled: bool | None = None,
    verify_on_stop: bool = False,
    model_recovery: bool = False,
    master_key_file: Path | None = None,
    stderr_only: bool = False,
    process_diagnostics: bool = False,
    clock: Clock | None = None,
    credential_backend: SecretStore | None = None,
    terminal_backend: TerminalBackendPort | None = None,
    desktop_backend: DesktopBackendPort | None = None,
    portal_backend: DesktopPortalPort | None = None,
    control_backend: DesktopControlPort | None = None,
    deployment: Record | None = None,
) -> Instance:
    initial_plugins = (*BUILTIN_FEATURES, *feature_plugins)
    declared_plugins = (*initial_plugins, *BUILTIN_KERNEL_FEATURES)
    if len({plugin.id for plugin in declared_plugins}) != len(declared_plugins):
        raise ValueError("Duplicate feature plugin IDs")
    paths = InstancePaths.resolve(home)
    if master_key_file and master_key_file.resolve().is_relative_to(paths.home):
        raise BusinessError("unsafe_master_key", "Master key must be outside the instance home")
    paths.initialize()
    lock = InstanceLock(paths)
    lock.acquire()
    lifetime = Lifetime()
    lifetime.own("instance_lock", 6, lambda _: close_sync(lock.close))
    try:
        if deployment is not None:
            verify_deployed_instance(paths.database, str(deployment["instance_id"]))
        ids, clock = UUIDs(), clock or SystemClock()
        boot_id = consume_launch(paths.control, deployment)
        source = Path(__file__).resolve().parents[1]
        digest = hashlib.sha256()
        for path in sorted(
            p
            for part in LAYERS
            for p in (source / part).rglob("*")
            if p.is_file() and p.suffix in CODE_SUFFIXES and not CODE_CACHE_DIRS.intersection(p.parts)
        ):
            digest.update(path.relative_to(source).as_posix().encode())
            digest.update(path.read_bytes())
        build_id = digest.hexdigest()
        sink = JsonLogSink(
            paths.logs, boot_id=boot_id, build_id=build_id, stderr_only=stderr_only, monotonic=clock.monotonic
        )
        logs = LoggingSession(sink)
        lifetime.own("log_writer", 5, lambda budget: close_sync(lambda: logs.close(budget)))
        measurements = Metrics()
        observation = Observation(measurements, clock)
        lifetime.own("tracing", 4, lambda _: close_sync(observation.provider.shutdown))
        host_diagnostics = ProcessDiagnostics(sink, asyncio.get_running_loop()) if process_diagnostics else None
        if host_diagnostics:
            lifetime.own("process_diagnostics", 4, lambda _: close_sync(host_diagnostics.close))
        database = Database(
            paths.database, clock, ids, objects_root=paths.objects, boot_id=boot_id, metrics=measurements
        )
        lifetime.own("database", 3, lambda _: database.close())
        receipt_path = paths.home / "installation.json"
        if receipt_path.is_symlink():
            raise BusinessError("installation_invalid", "Installation receipt cannot be a link")
        if receipt_path.is_file():
            from ..domain.installation import validate_installation

            try:
                receipt = validate_installation(json.loads(receipt_path.read_text(encoding="utf-8")))
            except (ValueError, UnicodeError, BusinessError) as exc:
                raise BusinessError("installation_invalid", "Installation receipt is invalid") from exc
            if receipt["schema_version"] != database.schema_version:
                raise BusinessError("installation_mismatch", "Installation receipt does not match the database schema")
        if deployment is not None:
            persisted = await database.read(lambda u: u.instance_identity())
            if persisted["instance_id"] != deployment["instance_id"] or build_id != deployment["build_id"]:
                raise BusinessError("service_identity_mismatch", "Deployment does not match this program and instance")
        files = ObjectFiles(paths.objects)
        lifetime.own("object_store", 2, lambda _: files.close())
        objects = ObjectService(database, files, clock)
        lifetime.own("object_store", 2, objects.close)
        skill_packages = SkillPackages()
        lifetime.own("skill_packages", 1, skill_packages.close)
        platform = platform_provider(sys.platform)
        skills = SkillService(database, objects, skill_packages, platform.skill_platform)
        lifetime.own("skills", 0, skills.close, skills.stop_accepting)
        image_codec = ImageCodec()
        lifetime.own("image_codec", 1, image_codec.close, image_codec.stop_accepting)
        images = ImageService(database, objects, image_codec)
        lifetime.own("images", 0, images.close, images.stop_accepting)
        extension_packages = ExtensionPackages(paths.home / "cache" / "extensions")
        lifetime.own("extension_packages", 1, extension_packages.close)
        extension_hosts = ExtensionHosts(str(paths.control / "extension-artifacts"))
        lifetime.own("extension_hosts", 0, extension_hosts.close, extension_hosts.stop_accepting)
        extensions = ExtensionService(database, objects, extension_packages, extension_hosts)
        lifetime.own("extensions", 0, extensions.close, extensions.stop_accepting)
        people = PeopleService(database, objects)
        validate_workspace = WorkspacePolicy(paths.home, source).validate
        platform_io = platform.register(
            lifetime,
            paths,
            validate_workspace,
            terminal_backend=terminal_backend,
            desktop_backend=desktop_backend,
            portal_backend=portal_backend,
            control_backend=control_backend,
        )
        terminals = TerminalService(database, platform_io.terminal, clock, ids)
        lifetime.own("terminals", 0, terminals.close, terminals.stop_accepting)
        terminal_reviews = TerminalReviewService(
            database,
            observation,
            native_quiescent=terminals.review_quiescent,
            while_quiescent=terminals.while_quiescent,
        )
        lifetime.own("terminal_reviews", 0, terminal_reviews.close, terminal_reviews.stop_accepting)
        desktop_portal: DesktopPortalService | None = None
        desktop_control: DesktopControlService | None = None

        async def detach_portal(agent_id: str, view_id: str) -> None:
            failures: list[Exception] = []
            for service in (desktop_portal, desktop_control):
                if service is not None:
                    try:
                        await service.detach(agent_id, view_id)
                    except Exception as caught:  # noqa: BLE001 -- one pending release cannot prevent another from being requested
                        failures.append(caught)
            if failures:
                raise failures[0]

        desktop = DesktopService(
            database, platform_io.desktop, clock, ids, boot_id, observation, objects, on_detach=detach_portal
        )
        lifetime.own("desktop_observations", 0, desktop.close, desktop.stop_accepting)
        desktop_portal = DesktopPortalService(
            database, platform_io.portal, ids, boot_id, observation, objects, authorize=desktop.require_view
        )
        lifetime.own("desktop_portal", 0, desktop_portal.close, desktop_portal.stop_accepting)
        desktop_control = DesktopControlService(
            database, platform_io.control, ids, boot_id, observation, authorize=desktop.require_view
        )
        lifetime.own("desktop_control", 0, desktop_control.close, desktop_control.stop_accepting)
        application: Application | None = None

        def accepting() -> bool:
            return application is not None and bool(application.health()["ready"])

        desktop_actions = DesktopActions(database, desktop, platform_io.desktop, clock, ids, accepting=accepting)
        lifetime.own("desktop_actions", 0, desktop_actions.close, desktop_actions.stop_accepting)
        local_vectors = vectorizer if vectorizer is not None else await configured_vectors(paths.home)
        persona_growth = PersonaGrowthService(database, PersonaGrowthFiles(paths.persona))
        documents = DocumentService(database, objects, local_vectors, persona=persona_growth)
        lifetime.own("source_documents", 1, documents.close)
        memory_semantics = MemorySemanticService(database, local_vectors)
        lifetime.own("memory_semantics", 0, memory_semantics.close, memory_semantics.stop_accepting)
        memory_organization = MemoryOrganizationService(database, observation)
        lifetime.own("memory_organization", 0, memory_organization.close, memory_organization.stop_accepting)
        namespace = hashlib.sha256(str(paths.home).encode()).hexdigest()[:20]
        credential_io: AsyncSecretPort
        if credential_backend is not None:
            credential_io = Credentials(credential_backend, sink.redactor)
        elif master_key_file:
            credential_io = Credentials(FileVault(paths.home / "private/vault", master_key_file), sink.redactor)
        else:
            credential_io = NativeCredentials(namespace, sink.redactor)
        lifetime.own("credential_io", 1, credential_io.close, credential_io.stop_accepting)
        counter = token_counter if token_counter is not None else ConservativeTokenCounter()
        configurations = ConfigurationService(database, counter)
        routing = RoutingService(database)
        model_transport = register_model_transport(
            lifetime,
            credential_io,
            sink.redactor,
            model=model,
            streamer=model_streamer,
            configure_logging=host_diagnostics.capture if host_diagnostics else None,
        )
        calls = ModelCalls(
            database,
            model_transport.model,
            observation,
            counter,
            preparer=model_transport.preparer,
            streamer=model_transport.streamer,
            images=images,
        )
        contexts = ContextService(database, calls, images)

        channels = register_channel_providers(
            lifetime,
            database,
            credential_io,
            sink.redactor,
            qq_transport=qq_transport,
            bilibili_backend=bilibili_backend,
            delivery_ports=delivery_ports,
        )
        features = await install_features(
            initial_plugins,
            FeatureContext(
                lifetime=lifetime,
                database=database,
                control=paths.control,
                objects=objects,
                images=images,
                persona=persona_growth,
                clock=clock,
                ids=ids,
                secrets=credential_io,
                channels=channels,
                accepting=accepting,
                default_tools=capabilities is None,
                android_backend=android_backend,
                media_processor=media_processor,
            ),
        )
        media = features.require("media", MediaService)
        android = features.require("android", AndroidService)
        bilibili = features.require("bilibili", BilibiliService)
        visual = VisualSubagentService(
            database,
            ImageObjectFrames(objects, images),
            ModelCallsVisualModelFactory(database, calls),
            media=MediaFrameSheets(media, objects, images),
        )
        lifetime.own("visual", 0, visual.close, visual.stop_accepting)
        deliveries = DeliveryService(database, clock, observation, channels.delivery)
        lifetime.own("deliveries", 0, deliveries.drain, deliveries.stop_accepting)
        lifetime.own("delivery_leases", 2, deliveries.release_boot_leases)
        credentials = CredentialService(database, credential_io, ids, deliveries.cancel_active, observation)
        lifetime.own("credential_changes", 0, credentials.close, credentials.stop_accepting)

        oauth_flow = OAuthFlow(credential_io, redact=sink.redactor.register)

        async def mcp_authorization(target: ServerTarget) -> str:
            if target.auth_mode == "none":
                return ""
            if target.auth_mode == "oauth":
                token = await oauth_flow.tokens.access_token(target.server_id)
                if token:
                    return token
            if not target.credential_ref:
                return ""
            raw = await credential_io.get(target.credential_ref)
            if not raw:
                return ""
            try:
                parsed: object = json.loads(raw)
            except (ValueError, TypeError):
                return raw
            if isinstance(parsed, dict):
                fields = cast(dict[str, object], parsed)
                return str(fields.get("access_token") or "")
            return raw

        mcp = MCPService(
            database,
            mcp_transport
            if mcp_transport is not None
            else MCPHosts(mcp_authorization, cwd=str(paths.control), oauth=oauth_flow),
            clock,
        )
        lifetime.own("mcp", 0, mcp.close, mcp.stop_accepting)
        # Calls owned by the previous process never replay: a prepared call was not
        # dispatched and a dispatched call stays unknown until the owner verifies it.
        mcp_recovered = await mcp.recover()
        if any(mcp_recovered.values()):
            logging.getLogger("omni.v2.startup").warning(
                "MCP records from the previous process were reconciled",
                extra={"event": "mcp.restart_reconciled", **mcp_recovered},
            )
        desktop_plugin = CapabilityPlugin(
            "desktop",
            owner_tools=(Capability(DESKTOP_ACTION, desktop_actions, desktop_actions.quiescent),),
            own_adapters=False,
        )
        plugins = (desktop_plugin, *features.capabilities)
        if capabilities is None:
            plugins = (
                *builtin_plugins(database, documents, memory_semantics),
                CapabilityPlugin(
                    "skills",
                    tuple(Capability(spec, skills, lambda: skills.health()["pending"] == 0) for spec in SKILL_SPECS),
                    own_adapters=False,
                ),
                *plugins,
                CapabilityPlugin(
                    "vision",
                    (
                        Capability(
                            VISUAL_SUBAGENT, VisualSubagentCapabilities(visual), lambda: visual.health()["pending"] == 0
                        ),
                    ),
                    own_adapters=False,
                ),
            )
        plugins = (*plugins, *capability_plugins)
        agent_capabilities = (*(capabilities or ()), *(cap for plugin in plugins for cap in plugin.tools))
        review_read_caps = {cap.spec.name: cap for cap in agent_capabilities if cap.spec.name in READ_TOOLS}

        async def review_read(name: str, arguments: Mapping[str, JSON], context: InvocationContext) -> ActionResult:
            cap = review_read_caps.get(name)
            if cap is None:
                return ActionResult(Outcome.ERROR, "Unknown read tool", error_code="tool_not_found")
            return await cap.adapter.invoke(name, arguments, operation_id=context.run_id, context=context)

        reflections = ReflectionService(database)
        background_review = BackgroundReviewService(
            database,
            calls,
            clock,
            observation,
            read_tools=tuple(cap.spec for cap in agent_capabilities if cap.spec.name in READ_TOOLS),
            read_dispatch=review_read,
            skills=skills,
        )
        lifetime.own("background_review", 0, background_review.close, background_review.stop_accepting)
        maintenance = MaintenanceService(
            database,
            documents,
            clock,
            observation,
            memory_organization,
            background_review,
            decay_daily_hour=DECAY_DAILY_HOUR if capabilities is None else None,
        )
        memory_organization.on_queued(maintenance.notify)
        background_review.on_queued(maintenance.notify)
        lifetime.own("maintenance", 0, maintenance.close, maintenance.stop_accepting)
        application = Application(
            database,
            calls,
            contexts,
            capabilities if capabilities is not None else (),
            clock,
            observation,
            workspace_validator=validate_workspace,
            delivery_cancellation=deliveries.cancel_active,
            extensions=extensions,
            mcp=mcp,
            images=images,
            memory_semantics=memory_semantics,
            skills=skills,
            todos=TodoService(database) if capabilities is None else None,
            reflections=reflections if capabilities is None else None,
            delegation_enabled=capabilities is None if delegation_enabled is None else delegation_enabled,
            background_review=background_review if capabilities is None else None,
            clarify=ClarifyService(database) if capabilities is None else None,
            verify_on_stop=verify_on_stop,
            model_recovery=model_recovery,
            plugins=plugins,
        )
        lifetime.own(
            "runs", 0, lambda budget: application.close(budget, close_database=False), application.stop_accepting
        )
        kernel_features = await install_features(
            BUILTIN_KERNEL_FEATURES,
            KernelFeatureContext(
                lifetime=lifetime,
                database=database,
                clock=clock,
                channels=channels,
                notify_run=application.notify,
                cancel_run=application.cancel,
                skill_platform=platform.skill_platform,
                redact=sink.redactor.text,
                default_tools=capabilities is None,
            ),
        )
        schedules = kernel_features.require("schedules", SchedulingService)
        qq = kernel_features.require("qq", QQService)
        for plugin in kernel_features.capabilities:
            application.attach_plugin(plugin)
        action_reviews = ActionReviewService(database, application.action_review_blockers, schedules.notify)
        lifetime.own("action_reviews", 0, action_reviews.close, action_reviews.stop_accepting)
        diagnostic = DiagnosticFiles(paths, sink)
        lifetime.own("operations", 0, diagnostic.close)

        def operational_health() -> Record:
            health = current_instance.health()
            deliveries = _record(health["deliveries"])
            maintenance = _record(health["maintenance"])
            memory_organization = _record(health["memory_organization"])
            extensions = _record(health["extensions"])
            hosts = _record(extensions["hosts"])
            terminals = _record(health["terminals"])
            media = _record(health["media"])
            mcp_transports = _record(_record(health["mcp"])["transports"])
            sessions = mcp_transports["sessions"]
            desktop = _record(health["desktop"])
            desktop_backend = _record(desktop["backend"])
            portal = _record(health["desktop_portal"])
            portal_backend = _record(portal["backend"])
            control = _record(health["desktop_control"])
            control_backend_health = _record(control["backend"])
            terminal_reviews = _record(health["terminal_reviews"])
            schedules = _record(health["schedules"])
            documents = _record(health["documents"])
            vector = _record(documents["vector"])
            residual = health["shutdown_residual"]
            return {
                "ready": health["ready"],
                "closing": health["closing"],
                "active_runs": health["active_runs"],
                "active_deliveries": deliveries["active"],
                "active_maintenance": maintenance["active"],
                "memory_organization_pending": memory_organization["pending"],
                "extension_hosts": hosts["resident"],
                "extension_cleanup_pending": hosts["cleanup_pending"],
                "active_terminals": terminals["active"],
                "media_jobs_pending": media["pending"],
                "mcp_sessions": mcp_transports["resident"],
                "mcp_cleanup_pending": sum(
                    1
                    for session in (sessions if isinstance(sessions, list) else [])
                    if isinstance(session, dict) and cast(dict[str, object], session).get("cleanup_pending")
                ),
                "desktop_observations": desktop["pending"],
                "desktop_portal_pending": portal["pending"],
                "desktop_portal_resident": portal_backend["resident"],
                "desktop_portal_cleanup_pending": portal_backend["cleanup_pending"],
                "desktop_control_pending": control["pending"],
                "desktop_control_resident": control_backend_health["resident"],
                "desktop_control_cleanup_pending": control_backend_health["cleanup_pending"],
                "desktop_cleanup_pending": desktop_backend.get("cleanup_pending", 0),
                "desktop_capture_staging": desktop_backend.get("capture_staging"),
                "terminal_cleanup_pending": terminals["cleanup_pending"],
                "terminal_state_pending": terminals["state_pending"],
                "terminal_reviews_pending": terminal_reviews["pending"],
                "database": health["database"],
                "scheduler_ready": schedules["ready"],
                "delivery_ready": deliveries["ready"],
                "document_queries": documents["pending"],
                "vector_available": vector["available"],
                "installed_delivery_transports": deliveries["installed_transports"],
                "stalled_deliveries": deliveries["stalled"],
                "credentials": health["credentials"],
                "shutdown_residual_count": len(residual) if isinstance(residual, list) else 0,
            }

        debugging = DebugService(database, diagnostic, clock, ids)
        operations = OperationsService(database, measurements, diagnostic, operational_health, debugging)
        lifetime.own("operations", 0, operations.close, operations.stop_accepting)
        debugging.start()
        await application.start()
        if local_vectors is not None:
            vector_state = documents.vector_status()

            def audit_vectors(u: UnitOfWork) -> None:
                # LocalVectors 尚未纳入 strict；维度按协议为 int，用局部 Any 桥接其未类型化对象。
                vectorizer: Any = local_vectors
                vector_dimension = vectorizer.dimension
                for agent in u.query("agents", ""):
                    u.audit(
                        str(agent["id"]),
                        "vector.boot_configuration",
                        boot_id,
                        {
                            "version": local_vectors.version,
                            "dimension": cast(int, vector_dimension),
                            "state": vector_state.get("state", vector_state.get("reason", "unavailable")),
                            "available": vector_state["available"],
                        },
                    )

            await database.write(audit_vectors)
        identity: Record = {
            **await database.read(lambda u: u.instance_identity()),
            **process_identity(),
            "boot_id": boot_id,
            "build_id": build_id,
            "api_version": 1,
            "schema_version": database.schema_version,
            "service_plan_sha256": deployment["service_plan_sha256"] if deployment else None,
        }
        identity_files = RuntimeIdentityFiles(paths.control, identity)
        lifetime.own("runtime_identity", 5, identity_files.close, identity_files.stop_accepting)
        await identity_files.start()
        await maintenance.start()
        deliveries.start()
        features.start()
        kernel_features.start()
        current_instance = Instance(
            application=application,
            paths=paths,
            _database=database,
            _lock=lock,
            _logging=logs,
            _observation=observation,
            boot_id=boot_id,
            build_id=build_id,
            identity=identity,
            _identity_files=identity_files,
            objects=objects,
            images=images,
            people=people,
            configurations=configurations,
            routing=routing,
            memory_semantics=memory_semantics,
            skills=skills,
            documents=documents,
            schedules=schedules,
            deliveries=deliveries,
            qq=qq,
            bilibili=bilibili,
            media=media,
            visual=visual,
            mcp=mcp,
            android=android,
            operations=operations,
            credentials=credentials,
            _credential_io=credential_io,
            _lifetime=lifetime,
            maintenance=maintenance,
            memory_organization=memory_organization,
            background_review=background_review,
            reflections=reflections,
            persona_growth=persona_growth,
            extensions=extensions,
            action_reviews=action_reviews,
            terminals=terminals,
            terminal_reviews=terminal_reviews,
            desktop=desktop,
            desktop_portal=desktop_portal,
            desktop_control=desktop_control,
            desktop_actions=desktop_actions,
            feature_services=MappingProxyType({**features.services, **kernel_features.services}),
            _process_diagnostics=host_diagnostics,
            model_port=model_transport.model,
        )
        return current_instance
    except BaseException:
        logging.getLogger("omni.v2.startup").exception(
            "Instance startup failed", extra={"event": "runtime.start_failed"}
        )
        result = await lifetime.close()
        if not result["complete"]:
            logging.getLogger("omni.v2.startup").error(
                "Startup rollback has residual components; instance lock retained",
                extra={"event": "runtime.rollback_incomplete", "error_code": "startup_cleanup_pending"},
            )
        raise
