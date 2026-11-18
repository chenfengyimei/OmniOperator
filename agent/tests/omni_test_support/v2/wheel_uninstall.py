"""Reusable wheel_uninstall fixtures, extracted without collecting test cases."""

import asyncio
import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

from omni.adapters.platform.releases.wheel_backup import backup_candidate
from omni.adapters.platform.releases.wheel_install import switch_candidate
from omni.adapters.platform.releases.wheel_prepare import prepare_wheel
from omni.adapters.platform.releases.wheel_rollback import rollback_check
from omni.adapters.platform.releases.wheel_smoke import smoke_candidate
from omni.adapters.platform.releases.wheel_stage import stage_wheel
from omni.adapters.platform.releases.wheel_uninstall import uninstall_candidate
from omni.adapters.platform.runtime.paths import InstanceLock, InstancePaths
from omni.composition.cli import main, seal_installation
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError

REQUIRED = (
    "omni/__init__.py",
    "omni/web_assets/index.html",
    "omni/web_assets/ui/v2/index.html",
    "omnioperator-0.1.0.dist-info/METADATA",
)


async def _open_and_close(home):
    instance = await open_instance(home)
    await instance.close()


def active_candidate(tmp_path):
    home = tmp_path / "home"
    asyncio.run(_open_and_close(home))
    paths = InstancePaths.resolve(home)
    seal_installation(paths)
    path = tmp_path / "candidate.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for name in REQUIRED:
            archive.writestr(name, "" if name.endswith("__init__.py") else "x")
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    stage_wheel(home, path, sha256)
    prepare_wheel(home, sha256)
    smoke_candidate(paths, sha256)
    backup_candidate(paths, sha256)
    rollback_check(paths, sha256)
    switch_candidate(paths, sha256)
    return paths, sha256


__all__ = [
    "BusinessError",
    "InstanceLock",
    "InstancePaths",
    "Path",
    "REQUIRED",
    "_open_and_close",
    "active_candidate",
    "asyncio",
    "backup_candidate",
    "hashlib",
    "json",
    "main",
    "open_instance",
    "prepare_wheel",
    "pytest",
    "rollback_check",
    "seal_installation",
    "smoke_candidate",
    "stage_wheel",
    "switch_candidate",
    "sys",
    "uninstall_candidate",
    "zipfile",
]
