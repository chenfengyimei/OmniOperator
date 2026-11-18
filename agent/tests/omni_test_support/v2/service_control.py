"""Reusable service_control fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni.adapters.platform.runtime.paths import InstanceLock, InstancePaths, PrivateFileLock
from omni.adapters.platform.runtime.runtime_identity import RuntimeIdentityFiles
from omni.adapters.platform.services.service_control_files import ServiceControlFiles
from omni.adapters.platform.services.service_launch import consume_launch
from omni.adapters.platform.services.service_operation_files import ServiceOperationFiles
from omni.adapters.primitives import SystemClock, UUIDs
from omni.application.service_control import ServiceControl
from omni.composition.cli import parser
from omni.domain.contracts import BusinessError
from omni.domain.service_control import make_control, validate_control
from omni.domain.service_deployment import startup
from omni_test_support.v2.deployment_files import installation as installation
from omni_test_support.v2.service_operations import operation as registration_operation
from omni_test_support.v2.service_operations import prepared
from omni_test_support.v2.service_runtime import boot_identity


class Clock(SystemClock):
    def __init__(self):
        self.tick = 0

    def monotonic(self):
        self.tick += 31  # Deterministic clock advances beyond the real 30s observation budget.
        return self.tick


class Native:
    def __init__(self, deployment):
        self.deployment = deployment
        self.paths = InstancePaths(Path(deployment["request"]["home"]))
        self.publisher = None
        self.lock = None
        self.identity = None
        self.starts = 0
        self.cleanup = True
        self.entered = self.release = None

    async def inspect(self, plan):
        return {
            "available": True,
            "installed": True,
            "definition_present": True,
            "definition_matches": True,
            "enabled": True,
            "state": "running" if self.lock else "stopped",
            "pid": self.identity["pid"] if self.lock else None,
        }

    async def start(self, plan):
        self.starts += 1
        if self.entered:
            self.entered.set()
            await self.release.wait()
        self.lock = InstanceLock(self.paths)
        self.lock.acquire()
        self.identity = {**boot_identity(plan), "boot_id": consume_launch(self.paths.control, startup(plan))}
        self.publisher = RuntimeIdentityFiles(
            self.paths.control, {k: v for k, v in self.identity.items() if k not in {"format", "state"}}
        )
        await self.publisher.start()
        return {"effect": "applied", "error_code": ""}

    async def close(self, timeout_s):
        return self.cleanup  # Manager/helper transport only; closing it never stops the app.

    async def dispose(self):
        if self.publisher:
            assert await self.publisher.close(2)
        if self.lock:
            self.lock.close()
            self.lock = None


class Boot:
    def __init__(self, native):
        self.native = native
        self.wrong_boot = False
        self.drains = 0
        self.cleanup = True

    async def probe(self, plan):
        if self.native.lock is None:
            return {"ready": False, "error_code": "offline"}
        return {
            **self.native.identity,
            "boot_id": UUIDs().new() if self.wrong_boot else self.native.identity["boot_id"],
            "ready": True,
            "error_code": None,
        }

    async def drain(self, operation):
        self.drains += 1
        assert self.native.identity["boot_id"] == operation["boot_id"]
        await self.native.dispose()
        return {"effect": "unknown", "accepted": True, "error_code": ""}

    async def close(self, timeout_s):
        return self.cleanup


@pytest.fixture
def controls(installation, tmp_path):
    files = installation[0]
    deployment = asyncio.run(prepared(files, tmp_path))
    native = Native(deployment)
    boot = Boot(native)
    store = ServiceControlFiles()

    def service():
        return ServiceControl(files, store, native, boot, Clock(), UUIDs())

    yield SimpleNamespace(
        deployment=deployment, files=files, native=native, boot=boot, store=store, service=service, root=tmp_path
    )
    asyncio.run(native.dispose())


__all__ = [
    "Boot",
    "BusinessError",
    "Clock",
    "InstanceLock",
    "InstancePaths",
    "Native",
    "Path",
    "PrivateFileLock",
    "RuntimeIdentityFiles",
    "ServiceControl",
    "ServiceControlFiles",
    "ServiceOperationFiles",
    "SimpleNamespace",
    "SystemClock",
    "UUIDs",
    "asyncio",
    "boot_identity",
    "consume_launch",
    "controls",
    "installation",
    "json",
    "make_control",
    "parser",
    "prepared",
    "pytest",
    "registration_operation",
    "startup",
    "validate_control",
]
