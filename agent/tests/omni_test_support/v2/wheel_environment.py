"""Reusable wheel_environment fixtures, extracted without collecting test cases."""

import asyncio
import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

from omni.adapters.platform.releases.wheel_backup import backup_candidate
from omni.adapters.platform.releases.wheel_environment import create_environment
from omni.adapters.platform.releases.wheel_install import switch_candidate
from omni.adapters.platform.releases.wheel_prepare import prepare_wheel
from omni.adapters.platform.releases.wheel_rollback import rollback_check
from omni.adapters.platform.releases.wheel_smoke import smoke_candidate
from omni.adapters.platform.releases.wheel_stage import stage_wheel
from omni.adapters.platform.runtime.paths import InstancePaths
from omni.composition.cli import main, seal_installation
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError

DIST = "omnioperator-0.1.0.dist-info"


async def _open_and_close(home):
    instance = await open_instance(home)
    await instance.close()


def installable_wheel(tmp_path):
    path = tmp_path / "omnioperator-0.1.0-py3-none-any.whl"
    members = {
        "omni/__init__.py": "",
        "omni/web_assets/index.html": "x",
        "omni/web_assets/ui/v2/index.html": "x",
        f"{DIST}/METADATA": "Metadata-Version: 2.1\nName: omnioperator\nVersion: 0.1.0\n",
        f"{DIST}/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    members[f"{DIST}/RECORD"] = "".join(f"{name},,\n" for name in members) + f"{DIST}/RECORD,,\n"
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def staged(tmp_path):
    home = tmp_path / "home"
    asyncio.run(_open_and_close(home))
    paths = InstancePaths.resolve(home)
    seal_installation(paths)
    path, sha256 = installable_wheel(tmp_path)
    stage_wheel(home, path, sha256)
    return paths, sha256


__all__ = [
    "BusinessError",
    "DIST",
    "InstancePaths",
    "Path",
    "_open_and_close",
    "asyncio",
    "backup_candidate",
    "create_environment",
    "hashlib",
    "installable_wheel",
    "json",
    "main",
    "open_instance",
    "prepare_wheel",
    "pytest",
    "rollback_check",
    "seal_installation",
    "smoke_candidate",
    "stage_wheel",
    "staged",
    "switch_candidate",
    "sys",
    "zipfile",
]
