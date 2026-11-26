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
from omni.adapters.platform.runtime.workspaces import WorkerWorkspaces, WorkspacePolicy
from omni.adapters.platform.services.service_launch import consume_launch

from ..adapters.build_identity import CODE_CACHE_DIRS, CODE_SUFFIXES, LAYERS
from ..adapters.credentials import Credentials
from ..adapters.diagnostics import JsonLogSink, LoggingSession, bind_context
from ..adapters.images import ImageCodec
from ..adapters.local_vectors import configured_vectors
from ..adapters.metrics import Metrics
from ..adapters.native_credentials import NativeCredentials
from ..adapters.object_store import ObjectFiles
from ..adapters.persistence.database import Database
from ..adapters.persistence.deployed_instance import verify_deployed_instance
from ..adapters.primitives import SystemClock, UUIDs
from ..adapters.process_diagnostics import ProcessDiagnostics
from ..adapters.token_counter import ConservativeTokenCounter
from ..application.action_review import ActionReviewService
from ..application.android import AndroidPort, AndroidService
from ..application.background_review import BackgroundReviewService
from ..application.bilibili import BilibiliPort, BilibiliService
from ..application.capabilities import Capability, CapabilityPlugin
from ..application.clarify import ClarifyService
from ..application.configuration import ConfigurationService, ConfigurationServices
from ..application.context import ContextService
from ..application.credentials import CredentialService
from ..application.delivery import DeliveryService
from ..application.desktop import DesktopService
from ..application.desktop_actions import DesktopActions
from ..application.desktop_control import DesktopControlService
from ..application.desktop_portal import DesktopPortalService
from ..application.desktop_services import DesktopServices
from ..application.documents import DocumentService
from ..application.engine import Application
from ..application.extensions import ExtensionService
from ..application.images import ImageService
from ..application.learning_services import LearningServices
from ..application.maintenance import MaintenanceService
from ..application.mcp import MCPService, MCPTransportPort
from ..application.media import MediaProcessingPort, MediaService
from ..application.memory_organization import MemoryOrganizationService
from ..application.memory_semantics import MemorySemanticService
from ..application.memory_services import MemoryServices
from ..application.model_calls import ModelCalls
from ..application.objects import ObjectService
from ..application.operations import OperationsService
from ..application.people import PeopleService, PeopleServices
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
from ..application.terminal_services import TerminalServices
from ..application.terminals import TerminalService
from ..application.todos import TodoService
from ..application.vision_services import VisionServices
from ..application.visual_subagent import VisualSubagentService
from ..domain.background_review import READ_TOOLS
from ..domain.contracts import JSON, ActionResult, BusinessError, InvocationContext, Outcome
from ..domain.omniweave import OmniWeaveMode, checked_mode
from .capabilities import builtin_plugins
from .channel_providers import register_channel_providers
from .features import FeatureContext, FeaturePlugin, KernelFeatureContext, WorkflowFeatureContext, install_features
from .features.catalog import BUILTIN_FEATURES, BUILTIN_KERNEL_FEATURES, BUILTIN_WORKFLOW_FEATURES
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
    omniweave_mode: OmniWeaveMode = "off",
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
    omniweave_mode = checked_mode(omniweave_mode)
    initial_plugins = (*BUILTIN_FEATURES, *feature_plugins)
    declared_plugins = (*initial_plugins, *BUILTIN_WORKFLOW_FEATURES, *BUILTIN_KERNEL_FEATURES)
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
        platform = platform_provider(sys.platform)
        image_codec = ImageCodec()
        lifetime.own("image_codec", 1, image_codec.close, image_codec.stop_accepting)
        images = ImageService(database, objects, image_codec)
        lifetime.own("images", 0, images.close, images.stop_accepting)
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
        local_vectors = vectorizer if vectorizer is not None else await configured_vectors(paths.home)
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
        application: Application | None = None

        def accepting() -> bool:
            return application is not None and bool(application.health()["ready"])

        registered_services: dict[str, object] = {}
        feature_context = FeatureContext(
            lifetime=lifetime,
            database=database,
            paths=paths,
            boot_id=boot_id,
            objects=objects,
            images=images,
            services=MappingProxyType(registered_services),
            vectors=local_vectors,
            clock=clock,
            ids=ids,
            observer=observation,
            token_counter=counter,
            secrets=credential_io,
            channels=channels,
            platform=platform_io,
            skill_platform=platform.skill_platform,
            redact_secret=sink.redactor.register,
            accepting=accepting,
            default_tools=capabilities is None,
            android_backend=android_backend,
            media_processor=media_processor,
            mcp_transport=mcp_transport,
        )
        features = await install_features(initial_plugins, feature_context, registered_services)
        media = features.require("media", MediaService)
        people = features.require("people", PeopleServices)
        documents = features.require("documents", DocumentService)
        memory = features.require("memory", MemoryServices)
        android = features.require("android", AndroidService)
        bilibili = features.require("bilibili", BilibiliService)
        desktop = features.require("desktop", DesktopServices)
        skills = features.require("skills", SkillService)
        extensions = features.require("extensions", ExtensionService)
        mcp = features.require("mcp", MCPService)
        deliveries = features.require("deliveries", DeliveryService)
        terminal = features.require("terminals", TerminalServices)
        configurations = features.require("configurations", ConfigurationServices)
        credentials = features.require("credentials", CredentialService)

        plugins = features.capabilities
        if capabilities is None:
            plugins = (*builtin_plugins(database, documents, memory.semantics), *plugins)
        agent_capabilities = (
            *(capabilities or ()),
            *(cap for plugin in (*plugins, *capability_plugins) for cap in plugin.tools),
        )
        review_read_caps = {cap.spec.name: cap for cap in agent_capabilities if cap.spec.name in READ_TOOLS}

        async def review_read(name: str, arguments: Mapping[str, JSON], context: InvocationContext) -> ActionResult:
            cap = review_read_caps.get(name)
            if cap is None:
                return ActionResult(Outcome.ERROR, "Unknown read tool", error_code="tool_not_found")
            return await cap.adapter.invoke(name, arguments, operation_id=context.run_id, context=context)

        workflow_features = await install_features(
            BUILTIN_WORKFLOW_FEATURES,
            WorkflowFeatureContext(
                base=feature_context,
                model_calls=calls,
                read_tools=tuple(cap.spec for cap in agent_capabilities if cap.spec.name in READ_TOOLS),
                read_dispatch=review_read,
            ),
            registered_services,
        )
        vision = workflow_features.require("vision", VisionServices)
        learning = workflow_features.require("learning", LearningServices)
        maintenance = workflow_features.require("maintenance", MaintenanceService)
        application = Application(
            database,
            calls,
            contexts,
            capabilities if capabilities is not None else (),
            clock,
            observation,
            workspace_validator=validate_workspace,
            delivery_cancellation=deliveries.cancel_active,
            images=images,
            memory_semantics=memory.semantics,
            skills=skills,
            todos=TodoService(database) if capabilities is None else None,
            reflections=learning.reflections if capabilities is None else None,
            delegation_enabled=capabilities is None if delegation_enabled is None else delegation_enabled,
            worker_workspace=WorkerWorkspaces(paths.home).prepare if omniweave_mode == "dispatch" else None,
            background_review=learning.review if capabilities is None else None,
            clarify=ClarifyService(database) if capabilities is None else None,
            verify_on_stop=verify_on_stop,
            model_recovery=model_recovery,
            plugins=(*plugins, *workflow_features.capabilities, *capability_plugins),
        )
        lifetime.own(
            "runs", 0, lambda budget: application.close(budget, close_database=False), application.stop_accepting
        )
        kernel_features = await install_features(
            BUILTIN_KERNEL_FEATURES,
            KernelFeatureContext(
                base=feature_context,
                notify_run=application.notify,
                cancel_run=application.cancel,
                review_blockers=application.action_review_blockers,
                runtime_health=lambda: current_instance.health(),
                metrics=measurements,
                sink=sink,
                omniweave_mode=omniweave_mode,
                worker_tool_specs=tuple(cap.spec for cap in agent_capabilities),
            ),
            registered_services,
        )
        schedules = kernel_features.require("schedules", SchedulingService)
        qq = kernel_features.require("qq", QQService)
        action_reviews = kernel_features.require("action-reviews", ActionReviewService)
        operations = kernel_features.require("system", OperationsService)
        for plugin in kernel_features.capabilities:
            application.attach_plugin(plugin)
        for provider in (*features.providers, *workflow_features.providers, *kernel_features.providers):
            application.attach_provider(provider.id, provider.adapter, review_quiescent=provider.review_quiescent)
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
        await workflow_features.start()
        await features.start()
        await kernel_features.start()
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
            images=vision.images,
            people=people.people,
            configurations=configurations.configurations,
            routing=configurations.routing,
            memory_semantics=memory.semantics,
            skills=skills,
            documents=documents,
            schedules=schedules,
            deliveries=deliveries,
            qq=qq,
            bilibili=bilibili,
            media=media,
            visual=vision.visual,
            mcp=mcp,
            android=android,
            operations=operations,
            credentials=credentials,
            _credential_io=credential_io,
            _lifetime=lifetime,
            maintenance=maintenance,
            memory_organization=memory.organization,
            background_review=learning.review,
            reflections=learning.reflections,
            persona_growth=people.persona,
            extensions=extensions,
            action_reviews=action_reviews,
            terminals=terminal.terminals,
            terminal_reviews=terminal.reviews,
            desktop=desktop.observations,
            desktop_portal=desktop.portal,
            desktop_control=desktop.control,
            desktop_actions=desktop.actions,
            feature_services=features.services,
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
