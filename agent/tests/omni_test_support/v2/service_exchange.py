"""Reusable service_exchange fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import omni_test_support.v2.deployment_files as deployment_tests
from omni.adapters.persistence.releases import ReleaseData
from omni.adapters.platform.releases.release_files import ReleaseFiles
from omni.adapters.platform.runtime.paths import InstanceLock
from omni.adapters.platform.runtime.permissions import private_path
from omni.adapters.platform.services import deployment_files
from omni.adapters.platform.services.exchange_deployments import ExchangeSourceDeployment
from omni.adapters.platform.services.exchange_files import ExchangeFiles
from omni.adapters.platform.services.service_control_files import ServiceControlFiles
from omni.adapters.platform.services.service_operation_files import ServiceOperationFiles
from omni.adapters.primitives import SystemClock, UUIDs
from omni.application.exchange_steps import ExchangeSteps
from omni.application.releases import Releases
from omni.application.service_control import ServiceControl
from omni.application.service_exchange import ServiceExchange
from omni.application.service_operations import ServiceOperations
from omni.composition.cli import parser
from omni.domain.contracts import BusinessError
from omni.domain.service_control import make_control
from omni.domain.service_exchange import (
    STAGES,
    checked_proof,
    make_exchange,
    marker,
    progress,
    stage_identity,
    validate_exchange,
)
from omni.domain.service_operations import make_operation
from omni_test_support.v2.releases import release as release
from omni_test_support.v2.service_control import Boot, Native

base_installation = deployment_tests.installation


@pytest.fixture
def installation(base_installation, monkeypatch):
    if sys.platform == "darwin":
        # Exercise the implemented Linux protocol on POSIX without claiming macOS exchange support.
        # Replace only this adapter's sys reference; never change the interpreter's global platform.
        monkeypatch.setattr(deployment_files, "sys", SimpleNamespace(platform="linux"))
    return base_installation


def plan_for(r, suffix=""):
    ids, at = UUIDs(), SystemClock().now()
    return make_exchange(
        r.plan,
        make_operation(r.plan["source"], "uninstall", ids.new(), at),
        make_operation(r.plan["target"], "install", ids.new(), at),
        make_control(r.plan["target"], "start", ids.new(), at, ids.new()),
        str(r.root / ("exchange-journal" + suffix)),
        ids.new(),
        at,
    )


class Manager:
    def __init__(self, state):
        self.state = state

    async def inspect(self, deployment):
        state = self.state
        installed = state.installed is not None
        return {
            "available": True,
            "installed": installed,
            "definition_present": installed,
            "definition_matches": state.installed == deployment["sha256"],
            "enabled": installed,
            "state": "running" if state.native.lock else "stopped",
            "pid": state.native.identity["pid"] if state.native.lock else None,
        }

    async def change(self, deployment, action):
        state = self.state
        state.changes.append(action)
        if state.entered is not None:
            state.entered.set()
            await state.wait.wait()
        if getattr(state, "unknown_without_effect", False):
            return {"effect": "unknown"}
        # Registration runs under a real isolated instance lock.
        from omni.adapters.platform.runtime.paths import InstancePaths

        with pytest.raises(BusinessError):
            InstanceLock(InstancePaths(Path(deployment["request"]["home"]))).acquire()
        state.installed = deployment["sha256"] if action == "install" else None
        return {"effect": "unknown" if state.unknown == action else "applied"}

    async def start(self, deployment):
        self.state.changes.append("start")
        return await self.state.native.start(deployment)

    async def close(self, timeout_s):
        return self.state.cleanup


def build(r, plan, state=None):
    if state is None:
        state = SimpleNamespace(
            installed=r.plan["source"]["sha256"],
            native=Native(r.plan["target"]),
            changes=[],
            unknown="",
            cleanup=True,
            entered=None,
            wait=None,
        )
    reservation = marker(plan)
    release_files = ReleaseFiles(r.deployments, exchange=reservation)
    clock, ids = SystemClock(), UUIDs()
    selection = Releases(r.deployments, release_files, ReleaseData(release_files), Manager(state), clock, ids)
    source = ServiceOperations(
        ExchangeSourceDeployment(r.plan, release_files),
        ServiceOperationFiles(exchange=reservation),
        Manager(state),
        clock,
        ids,
    )
    target = ServiceOperations(r.deployments, ServiceOperationFiles(exchange=reservation), Manager(state), clock, ids)
    control = ServiceControl(
        r.deployments, ServiceControlFiles(exchange=reservation), Manager(state), Boot(state.native), clock, ids
    )
    steps = ExchangeSteps(source, target, selection, control)
    files = ExchangeFiles(r.deployments)
    service = ServiceExchange(files, steps, clock, ids)
    return SimpleNamespace(
        service=service, state=state, files=files, steps=steps, source=source, target=target, control=control
    )


@pytest.fixture
def exchange(release):
    r = release
    r.service.prepare(r.plan, r.plan["sha256"])
    return r, plan_for(r)


__all__ = [
    "Boot",
    "BusinessError",
    "ExchangeFiles",
    "ExchangeSourceDeployment",
    "ExchangeSteps",
    "InstanceLock",
    "Manager",
    "Native",
    "Path",
    "ReleaseData",
    "ReleaseFiles",
    "Releases",
    "STAGES",
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
    "checked_proof",
    "copy",
    "deployment_files",
    "deployment_tests",
    "exchange",
    "hashlib",
    "installation",
    "json",
    "make_control",
    "make_exchange",
    "make_operation",
    "marker",
    "parser",
    "plan_for",
    "private_path",
    "progress",
    "pytest",
    "release",
    "stage_identity",
    "sys",
    "validate_exchange",
]
