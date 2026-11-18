"""Reusable service_runtime fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from omni.adapters.platform.runtime.paths import InstanceLock
from omni.adapters.platform.runtime.runtime_identity import RuntimeIdentityFiles, canonical_proof, read_private
from omni.adapters.platform.services import service_readiness as readiness_module
from omni.adapters.platform.services.service_readiness import ServiceReadiness
from omni.adapters.primitives import SystemClock, UUIDs
from omni.application.service_deployment import ServiceDeployment
from omni.composition import service_runner
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError
from omni.domain.service_deployment import service_argv
from omni_test_support.v2.deployment_files import installation as installation
from omni_test_support.v2.deployment_files import plan_for


def boot_identity(plan):
    return {
        "format": "omni-runtime-identity-1",
        "instance_id": plan["request"]["instance_id"],
        "boot_id": UUIDs().new(),
        "pid": 1234,
        "process_created": 123.0,
        "build_id": plan["runtime"]["build_id"],
        "api_version": 1,
        "schema_version": plan["runtime"]["schema_head"],
        "service_plan_sha256": plan["sha256"],
        "state": "running",
    }


__all__ = [
    "BusinessError",
    "InstanceLock",
    "Path",
    "RuntimeIdentityFiles",
    "ServiceDeployment",
    "ServiceReadiness",
    "SimpleNamespace",
    "SystemClock",
    "UUIDs",
    "asyncio",
    "boot_identity",
    "canonical_proof",
    "httpx",
    "installation",
    "json",
    "open_instance",
    "os",
    "plan_for",
    "pytest",
    "read_private",
    "readiness_module",
    "service_argv",
    "service_runner",
    "threading",
]
