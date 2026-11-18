"""Reusable service_deployment fixtures, extracted without collecting test cases."""

from __future__ import annotations

import copy
import json
import plistlib
import subprocess
from xml.etree import ElementTree as ET

import pytest

from omni.adapters.platform.services.service_templates import TASK_NAMESPACE, render
from omni.domain.contracts import BusinessError
from omni.domain.service_deployment import (
    TARGETS,
    make_plan,
    manager_identity,
    parse_request,
    parse_startup,
    plan_fingerprint,
    service_argv,
    startup,
    validate_plan,
    validate_runtime,
)

INSTANCE = "8ccf2cf6-661f-4100-9141-2198864b558e"

PLAN = "521c022b-82a0-4680-81c9-54dac4b724f8"


def request(target="linux-user"):
    windows = target == "windows-user"
    principal = {
        "name": "example",
        "id": "S-1-5-21-100-200-300-1000" if windows else "1000",
        "home": "C:\\Users\\example" if windows else "/home/example",
    }
    return {
        "format": "omni-service-request-1",
        "target": target,
        "instance_id": INSTANCE,
        "home": "D:\\private\\omni" if windows else "/home/example/omni",
        "working_directory": "D:\\Owner Workspace" if windows else "/home/example/Owner Workspace",
        "bundle_dir": "D:\\Service Bundles\\v2" if windows else "/opt/Service Bundles/v2",
        "principal": principal,
        "host": "127.0.0.1",
        "port": 8766,
        "external_url": None,
        "master_key_file": None,
        "stdout_logs": True,
    }


def runtime(target="linux-user"):
    windows = target == "windows-user"
    prefix = "C:\\versions\\v2\\venv" if windows else "/opt/versions/v2/venv"
    python = prefix + ("\\Scripts\\python.exe" if windows else "/bin/python")
    return {
        "format": "omni-service-runtime-2",
        "platform": "win32" if windows else "darwin" if target.startswith("macos-") else "linux",
        "python": python,
        "python_realpath": python if windows else "/usr/bin/python3.12",
        "python_prefix": prefix,
        "python_links": [] if windows else [{"path": python, "target": "/usr/bin/python3.12"}],
        "venv_config": {
            "path": prefix + ("\\pyvenv.cfg" if windows else "/pyvenv.cfg"),
            "sha256": "a" * 64,
            "bytes": 27,
        },
        "python_sha256": "b" * 64,
        "process_python": "C:\\Python312\\python.exe" if windows else "/usr/bin/python3.12",
        "process_python_sha256": "f" * 64,
        "python_version": "3.12.12 (example) [Compiler]",
        "architecture": "AMD64" if windows else "arm64",
        "package_root": prefix + ("\\Lib\\site-packages\\omni" if windows else "/lib/python3.12/site-packages/omni"),
        "build_id": "c" * 64,
        "assets_sha256": "d" * 64,
        "api_version": 1,
        "schema_head": "v2_0026",
        "editable": False,
        "principal": request(target)["principal"],
        "files": [{"path": "__init__.py", "sha256": "e" * 64, "bytes": 23}],
        "distributions": [{"name": "omnioperator", "version": "2.0.0a1"}],
    }


def plan(target="linux-user"):
    return make_plan(request(target), runtime(target), PLAN, "2026-11-05T00:00:00+00:00")


__all__ = [
    "BusinessError",
    "ET",
    "INSTANCE",
    "PLAN",
    "TARGETS",
    "TASK_NAMESPACE",
    "copy",
    "json",
    "make_plan",
    "manager_identity",
    "parse_request",
    "parse_startup",
    "plan",
    "plan_fingerprint",
    "plistlib",
    "pytest",
    "render",
    "request",
    "runtime",
    "service_argv",
    "startup",
    "subprocess",
    "validate_plan",
    "validate_runtime",
]
