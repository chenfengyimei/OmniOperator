"""Reusable service_operations fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import copy
import json
import uuid
from pathlib import Path

import pytest

from omni.adapters.platform.runtime.paths import InstanceLock, InstancePaths
from omni.adapters.platform.runtime.permissions import assert_private
from omni.adapters.platform.services.service_operation_files import ServiceOperationFiles
from omni.adapters.primitives import SystemClock, UUIDs
from omni.application.service_operations import ServiceOperations
from omni.composition.cli import parser
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError
from omni.domain.service_deployment import make_plan
from omni.domain.service_operations import (
    make_operation,
    registration_goal,
    registration_precondition,
    validate_operation,
)
from omni_test_support.v2.deployment_files import installation as installation
from omni_test_support.v2.deployment_files import plan_for
from omni_test_support.v2.service_deployment import plan


def registration(installed=False):
    return {
        "available": True,
        "installed": installed,
        "definition_present": installed,
        "definition_matches": True if installed else None,
        "enabled": installed,
        "state": "stopped",
        "error_code": "",
    }


class Native:
    def __init__(self, report=None):
        self.report = registration() if report is None else report
        self.calls = []
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.release.set()
        self.failure = None
        self.cleanup = True

    async def inspect(self, deployment):
        self.calls.append("inspect")
        return dict(self.report)

    async def change(self, deployment, action):
        self.calls.append(action)
        self.entered.set()
        await self.release.wait()
        self.report = registration(action == "install")
        if self.failure:
            raise self.failure
        return {"effect": "applied", "error_code": ""}

    async def close(self, timeout_s=30):
        return self.cleanup


async def prepared(files, tmp_path):
    instance = await open_instance(tmp_path / "instance", capabilities=())
    identity = instance.identity["instance_id"]
    assert (await instance.close())["complete"]
    deployment = plan_for(files, tmp_path)
    request = {**deployment["request"], "instance_id": identity}
    deployment = make_plan(request, deployment["runtime"], deployment["id"], deployment["created_at"])
    files.materialize(deployment, tmp_path / "bundle")
    return deployment


def operation(deployment, action="install"):
    return make_operation(deployment, action, UUIDs().new(), SystemClock().now())


__all__ = [
    "BusinessError",
    "InstanceLock",
    "InstancePaths",
    "Native",
    "Path",
    "ServiceOperationFiles",
    "ServiceOperations",
    "SystemClock",
    "UUIDs",
    "assert_private",
    "asyncio",
    "copy",
    "installation",
    "json",
    "make_operation",
    "make_plan",
    "open_instance",
    "operation",
    "parser",
    "plan",
    "plan_for",
    "prepared",
    "pytest",
    "registration",
    "registration_goal",
    "registration_precondition",
    "uuid",
    "validate_operation",
]
