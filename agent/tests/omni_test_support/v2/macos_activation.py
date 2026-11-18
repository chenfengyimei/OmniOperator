"""Reusable macos_activation fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

import pytest

from omni.adapters.platform.releases.release_files import ReleaseFiles
from omni.adapters.platform.runtime.paths import InstanceLock, InstancePaths
from omni.adapters.platform.services import service_control_files, service_operation_files
from omni.adapters.platform.services import service_launch as launch
from omni.adapters.platform.services import service_registration as native
from omni.adapters.platform.services.deployment_files import CurrentDeploymentFiles
from omni.adapters.platform.services.exchange_files import ExchangeFiles
from omni.adapters.primitives import SystemClock, UUIDs
from omni.application.exchange_steps import ExchangeSteps
from omni.application.service_control import ServiceControl
from omni.application.service_exchange import ServiceExchange
from omni.application.service_operations import ServiceOperations
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError
from omni.domain.releases import FENCE_FILES, make_release
from omni.domain.service_activation import activation_binding, validate_reservation
from omni.domain.service_control import make_control, start_postcondition
from omni.domain.service_deployment import make_plan, startup
from omni.domain.service_exchange import make_exchange, marker, stage_identity, validate_exchange
from omni.domain.service_operations import make_operation
from omni_test_support.v2.macos_services import acceptance_script
from omni_test_support.v2.service_control import Clock
from omni_test_support.v2.service_deployment import request, runtime


def exchange_plan(target="macos-user"):
    ids, at = UUIDs(), SystemClock().now()
    captured = runtime(target)
    captured["files"] += [{"path": name, "bytes": 100, "sha256": "e" * 64} for name in FENCE_FILES]
    old = make_plan(request(target), captured, ids.new(), at)
    next_runtime = json.loads(json.dumps(captured).replace("/opt/versions/v2/venv", "/opt/versions/next/venv"))
    supplied = request(target)
    for key in ("home", "bundle_dir"):
        supplied[key] += "-next"
    new = make_plan(supplied, next_runtime, ids.new(), at)
    release = make_release(old, new, {"journal": "/tmp/release-journal", "backup": "/tmp/snapshot"}, ids.new(), at)
    return make_exchange(
        release,
        make_operation(old, "uninstall", ids.new(), at),
        make_operation(new, "install", ids.new(), at),
        make_control(new, "start", ids.new(), at, ids.new()),
        "/tmp/exchange-journal",
        ids.new(),
        at,
    )


@pytest.fixture(params=["macos-user", "macos-system"])
def claimed(tmp_path, monkeypatch, request):
    plan = exchange_plan(request.param)
    source, target = (InstancePaths(tmp_path / name) for name in ("source", "target"))
    for home in (source, target):
        home.initialize()
    files = ExchangeFiles(CurrentDeploymentFiles())
    monkeypatch.setattr(files, "homes", lambda _: [source, target])
    monkeypatch.setattr(files, "_root", lambda _: tmp_path / "journal")
    # Native macOS home strings remain POSIX. Map only this port's comparison
    # to isolated Windows/POSIX test paths; this is not a Mac execution claim.
    logical_home = plan["release"]["target"]["request"]["home"]
    monkeypatch.setattr(launch, "Path", lambda value: target.home if value == logical_home else Path(value))
    with files.exclusive(plan):
        files.claim(plan)
    return plan, files, source, target


__all__ = [
    "BusinessError",
    "Clock",
    "CurrentDeploymentFiles",
    "ExchangeFiles",
    "ExchangeSteps",
    "FENCE_FILES",
    "InstanceLock",
    "InstancePaths",
    "Path",
    "ReleaseFiles",
    "ServiceControl",
    "ServiceExchange",
    "ServiceOperations",
    "SystemClock",
    "UUIDs",
    "acceptance_script",
    "activation_binding",
    "asyncio",
    "claimed",
    "copy",
    "exchange_plan",
    "json",
    "launch",
    "make_control",
    "make_exchange",
    "make_operation",
    "make_plan",
    "make_release",
    "marker",
    "native",
    "open_instance",
    "pytest",
    "request",
    "runtime",
    "service_control_files",
    "service_operation_files",
    "stage_identity",
    "start_postcondition",
    "startup",
    "validate_exchange",
    "validate_reservation",
]
