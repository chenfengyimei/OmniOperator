"""Reusable releases fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni.adapters.persistence.releases import ReleaseData
from omni.adapters.platform.releases.release_files import ReleaseFiles
from omni.adapters.platform.runtime.paths import InstanceLock, InstancePaths
from omni.adapters.platform.runtime.permissions import assert_private
from omni.adapters.platform.services import deployment_files as d
from omni.adapters.primitives import SystemClock, UUIDs
from omni.application.releases import Releases
from omni.composition.cli import parser
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError
from omni.domain.releases import FENCE_FILES, make_release, validate_release
from omni.domain.service_deployment import make_plan
from omni_test_support.paths import PRODUCT_ROOT as PRODUCT_ROOT
from omni_test_support.v2.deployment_files import installation as installation
from omni_test_support.v2.service_operations import prepared


class Manager:
    def __init__(self):
        self.installed = False
        self.cleanup = True
        self.calls = 0
        self.wait = None

    async def inspect(self, plan):
        self.calls += 1
        if self.wait is not None:
            await self.wait.wait()
        return {
            "available": True,
            "installed": self.installed,
            "definition_present": self.installed,
            "state": "stopped",
            "pid": None,
        }

    async def close(self, timeout_s):
        return self.cleanup

    async def change(self, plan, action):
        pytest.fail("A release directory selection cannot mutate service registration")


@pytest.fixture
def release(installation, tmp_path, monkeypatch):
    deployments, package, _assets, binary, _config = installation
    actual = PRODUCT_ROOT / "src/omni"
    for name in FENCE_FILES:
        target = package / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((actual / name).read_bytes())
    source = asyncio.run(prepared(deployments, tmp_path))
    next_package = tmp_path / "next-program/omni"
    shutil.copytree(package, next_package)
    (next_package / "adapters/isolated.py").write_bytes(b"VALUE='next-version'\n")
    next_prefix = tmp_path / "next-environment"
    shutil.copytree(binary.parent.parent, next_prefix)
    current = {"package": next_package, "prefix": next_prefix}
    monkeypatch.setattr(d, "_installation", lambda: (current["package"], current["package"] / "web_assets", False))
    monkeypatch.setattr(
        d,
        "_python",
        lambda: {
            **d.python_files(current["prefix"] / "bin/python.exe", current["prefix"]),
            "python_version": sys.version.replace("\r\n", " ").replace("\n", " "),
        },
    )
    request = {**source["request"], "home": str(tmp_path / "next-home"), "bundle_dir": str(tmp_path / "next-bundle")}
    target = make_plan(request, deployments.capture(), UUIDs().new(), SystemClock().now())
    value = make_release(
        source,
        target,
        {"backup": str(tmp_path / "snapshot"), "journal": str(tmp_path / "journal")},
        UUIDs().new(),
        SystemClock().now(),
    )
    files = ReleaseFiles(deployments)
    manager = Manager()
    data = ReleaseData(files)
    service = Releases(deployments, files, data, manager, SystemClock(), UUIDs())
    return SimpleNamespace(
        plan=value,
        files=files,
        service=service,
        data=data,
        manager=manager,
        deployments=deployments,
        current=current,
        source_package=package,
        source_prefix=binary.parent.parent,
        root=tmp_path,
    )


__all__ = [
    "BusinessError",
    "FENCE_FILES",
    "InstanceLock",
    "InstancePaths",
    "Manager",
    "Path",
    "ReleaseData",
    "ReleaseFiles",
    "Releases",
    "SimpleNamespace",
    "SystemClock",
    "UUIDs",
    "assert_private",
    "asyncio",
    "copy",
    "d",
    "hashlib",
    "installation",
    "json",
    "make_plan",
    "make_release",
    "open_instance",
    "parser",
    "prepared",
    "pytest",
    "release",
    "shutil",
    "sys",
    "validate_release",
]
