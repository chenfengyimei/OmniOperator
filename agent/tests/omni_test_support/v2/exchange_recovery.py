"""Reusable exchange_recovery fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import copy
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni.adapters.persistence.database import Database
from omni.adapters.persistence.releases import ReleaseData
from omni.adapters.persistence.sqlite_access import sqlite_connection
from omni.adapters.platform.releases.release_files import ReleaseFiles
from omni.adapters.platform.runtime.paths import InstanceLock
from omni.adapters.platform.services.exchange_deployments import ExchangeSourceDeployment
from omni.adapters.platform.services.exchange_files import ExchangeFiles
from omni.adapters.platform.services.service_control_files import ServiceControlFiles
from omni.adapters.platform.services.service_operation_files import ServiceOperationFiles
from omni.adapters.primitives import SystemClock, UUIDs
from omni.application.exchange_recovery import ExchangeRecovery
from omni.application.exchange_recovery_steps import ExchangeRecoverySteps
from omni.application.exchange_steps import ExchangeSteps
from omni.application.releases import Releases
from omni.application.service_control import ServiceControl
from omni.application.service_exchange import ServiceExchange
from omni.application.service_operations import ServiceOperations
from omni.domain.contracts import BusinessError, ModelReply
from omni.domain.releases import validate_release
from omni.domain.service_deployment import manager_identity, validate_plan
from omni.domain.service_exchange import marker
from omni.domain.service_isolation import validate_isolation
from omni_test_support.v2.service_control import Boot, Clock, Native
from omni_test_support.v2.service_exchange import base_installation as base_installation
from omni_test_support.v2.service_exchange import exchange as exchange
from omni_test_support.v2.service_exchange import installation as installation
from omni_test_support.v2.service_exchange import release as release
from omni_test_support.v2.v2_configuration_helpers import model_uow


class Manager:
    def __init__(self, state):
        self.state = state

    async def inspect(self, plan):
        state, name = self.state, manager_identity(plan)["name"]
        present = name in state.registry
        return {
            "available": True,
            "installed": present,
            "definition_present": present,
            "definition_matches": state.registry.get(name) == plan["sha256"],
            "enabled": present,
            "state": "running"
            if state.native.lock and state.native.paths.home == Path(plan["request"]["home"])
            else "stopped",
            "pid": state.native.identity["pid"]
            if state.native.lock and state.native.paths.home == Path(plan["request"]["home"])
            else None,
        }

    async def change(self, plan, action):
        state, name = self.state, manager_identity(plan)["name"]
        state.changes.append((action, name))
        if action == state.unknown:
            return {"effect": "unknown"}
        if action == "install":
            assert name not in state.registry
            state.registry[name] = plan["sha256"]
        else:
            assert state.registry[name] == plan["sha256"]
            del state.registry[name]
        return {"effect": "applied"}

    async def start(self, plan):
        self.state.changes.append(("start", manager_identity(plan)["name"]))
        return await self.state.native.start(plan)

    async def close(self, timeout_s):
        return True


def build(r, plan, state=None):
    if state is None:
        state = SimpleNamespace(
            registry={manager_identity(plan["release"]["source"])["name"]: plan["release"]["source"]["sha256"]},
            native=Native(plan["release"]["target"]),
            changes=[],
            unknown="install",
        )
    clock, ids, reservation = Clock(), UUIDs(), marker(plan)
    files = ExchangeFiles(r.deployments)
    storage = ReleaseFiles(r.deployments, exchange=reservation)
    selection = Releases(r.deployments, storage, ReleaseData(storage), Manager(state), clock, ids)
    source = ServiceOperations(
        ExchangeSourceDeployment(plan["release"], storage),
        ServiceOperationFiles(exchange=reservation),
        Manager(state),
        clock,
        ids,
    )
    target = ServiceOperations(r.deployments, ServiceOperationFiles(exchange=reservation), Manager(state), clock, ids)
    boot = Boot(state.native)
    control = ServiceControl(r.deployments, ServiceControlFiles(exchange=reservation), Manager(state), boot, clock, ids)
    service = ServiceExchange(files, ExchangeSteps(source, target, selection, control), clock, ids)
    recovery = ExchangeRecovery(files, ExchangeRecoverySteps(files, selection), clock, ids)
    return SimpleNamespace(
        service=service,
        recovery=recovery,
        files=files,
        storage=storage,
        selection=selection,
        state=state,
        boot=boot,
        control=control,
    )


async def interrupted(r, plan, kind="install"):
    x = build(r, plan)
    if kind == "start":
        x.state.unknown = ""
        x.boot.wrong_boot = True
    result = await x.service.apply(plan, plan["sha256"])
    assert result["state"] == "blocked"
    assert result["active_stage"] == ("install_target" if kind == "install" else "start_target")
    assert await x.service.close(1)
    if kind == "start":
        await x.state.native.dispose()  # The test target exits normally; isolation never forces a real app to stop.
    return x


def isolation(x, r, plan):
    output = r.root / "isolation.json"
    result = x.recovery.plan(plan, "Preserve the uncertain outcome and restore in a new directory", str(output))
    value = validate_isolation(r.deployments.read(output))
    assert result["executed"] is False
    return value


def third_installation(r):
    package, prefix = r.root / "rescue-program/omni", r.root / "rescue-environment"
    shutil.copytree(r.current["package"], package)
    shutil.copytree(r.current["prefix"], prefix)
    (package / "adapters/rescue.py").write_text("BUILD='rescue'\n", encoding="utf-8")
    r.current.update(package=package, prefix=prefix)


__all__ = [
    "Boot",
    "BusinessError",
    "Clock",
    "Database",
    "ExchangeFiles",
    "ExchangeRecovery",
    "ExchangeRecoverySteps",
    "ExchangeSourceDeployment",
    "ExchangeSteps",
    "InstanceLock",
    "Manager",
    "ModelReply",
    "Native",
    "Path",
    "ReleaseData",
    "ReleaseFiles",
    "Releases",
    "ServiceControl",
    "ServiceControlFiles",
    "ServiceExchange",
    "ServiceOperationFiles",
    "ServiceOperations",
    "SimpleNamespace",
    "SystemClock",
    "UUIDs",
    "asyncio",
    "base_installation",
    "build",
    "copy",
    "exchange",
    "installation",
    "interrupted",
    "isolation",
    "json",
    "manager_identity",
    "marker",
    "model_uow",
    "pytest",
    "release",
    "shutil",
    "sqlite_connection",
    "third_installation",
    "validate_isolation",
    "validate_plan",
    "validate_release",
]
