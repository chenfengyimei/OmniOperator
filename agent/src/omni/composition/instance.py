"""Build one isolated instance; never imports or opens the V1 runtime."""

from __future__ import annotations

import hashlib
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from opentelemetry.sdk.trace import TracerProvider

from ..adapters.diagnostics import JsonLogSink, LoggingSession, bind_context
from ..adapters.models import LiteLLMModel
from ..adapters.persistence.database import Database
from ..adapters.platform.paths import InstanceLock, InstancePaths
from ..adapters.platform.secrets import FileVault, SystemSecrets
from ..adapters.platform.workspaces import WorkspacePolicy
from ..adapters.primitives import SystemClock, UUIDs
from ..application.engine import Application, Capability
from ..application.ports import ModelPort
from ..domain.contracts import BusinessError
from .capabilities import builtin_capabilities


class Observation:
    def __init__(self) -> None:
        self.provider = TracerProvider()
        self.tracer = self.provider.get_tracer("omni.v2")

    @contextmanager
    def context(self, **fields: str):
        with bind_context(**fields):
            yield

    def span(self, name: str):
        return self.tracer.start_as_current_span(name)


@dataclass
class Instance:
    application: Application
    paths: InstancePaths
    _database: Database
    _lock: InstanceLock
    _logging: LoggingSession
    _observation: Observation
    boot_id: str
    build_id: str
    _closed: bool = False

    def health(self) -> dict:
        health = {
            **self.application.health(),
            "boot_id": self.boot_id,
            "build_id": self.build_id,
            "api_version": 1,
            "schema_version": "v2_0001",
            "database": self._database.metrics,
            "logs": self._logging.sink.snapshot(),
            "platform_acceptance": "pending",
        }
        health["ready"] = bool(
            health["ready"] and not self._database.metrics["closed"] and not self._database.metrics["error_code"]
        )
        return health

    async def close(self) -> dict:
        if self._closed:
            return {"complete": True, "residual": []}
        result = await self.application.close()
        if result["complete"]:
            self._observation.provider.shutdown()
            if not self._logging.close():
                return {"complete": False, "residual": ["log_writer"]}
            self._lock.close()
            self._closed = True
        return result


async def open_instance(
    home: Path | None = None,
    *,
    model: ModelPort | None = None,
    capabilities: tuple[Capability, ...] | None = None,
    master_key_file: Path | None = None,
    stderr_only: bool = False,
) -> Instance:
    paths = InstancePaths.resolve(home)
    if master_key_file and master_key_file.resolve().is_relative_to(paths.home):
        raise BusinessError("unsafe_master_key", "Master key must be outside the instance home")
    paths.initialize()
    lock = InstanceLock(paths)
    lock.acquire()
    ids, clock = UUIDs(), SystemClock()
    boot_id = ids.new()
    source = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for path in sorted(
        p for part in ("domain", "application", "adapters", "composition") for p in (source / part).rglob("*.py")
    ):
        digest.update(path.relative_to(source).as_posix().encode())
        digest.update(path.read_bytes())
    build_id = digest.hexdigest()
    sink = JsonLogSink(paths.logs, boot_id=boot_id, build_id=build_id, stderr_only=stderr_only)
    logs = LoggingSession(sink)
    observation = Observation()
    database = None
    try:
        database = Database(paths.database, clock, ids)
        namespace = hashlib.sha256(str(paths.home).encode()).hexdigest()[:20]
        secrets = (
            FileVault(paths.home / "private/vault", master_key_file) if master_key_file else SystemSecrets(namespace)
        )
        application = Application(
            database,
            model or LiteLLMModel(secrets, sink.redactor),
            capabilities if capabilities is not None else builtin_capabilities(database),
            clock,
            observation,
            workspace_validator=WorkspacePolicy(paths.home, Path(__file__).resolve().parents[1]).validate,
        )
        await application.start()
        return Instance(application, paths, database, lock, logs, observation, boot_id, build_id)
    except BaseException:
        logging.getLogger("omni.v2.startup").exception(
            "Instance startup failed", extra={"event": "runtime.start_failed"}
        )
        if database is not None:
            await database.close()
        observation.provider.shutdown()
        logs.close()
        lock.close()
        raise
