"""Reusable wheel_install fixtures, extracted without collecting test cases."""

import asyncio
import hashlib
import json
import sqlite3
import sys
import zipfile

import pytest
from alembic import command

from omni.adapters.persistence.maintenance import durable_json
from omni.adapters.persistence.migration_config import configuration, head
from omni.adapters.persistence.sqlalchemy_access import database_engine
from omni.adapters.platform.releases.wheel_backup import backup_candidate
from omni.adapters.platform.releases.wheel_install import (
    install_candidate,
    migrate_candidate,
    rollback_candidate,
    runtime_current,
    switch_candidate,
)
from omni.adapters.platform.releases.wheel_prepare import prepare_wheel
from omni.adapters.platform.releases.wheel_rollback import rollback_check
from omni.adapters.platform.releases.wheel_smoke import smoke_candidate
from omni.adapters.platform.releases.wheel_stage import stage_wheel
from omni.adapters.platform.runtime.paths import InstanceLock, InstancePaths
from omni.adapters.primitives import SystemClock
from omni.composition.cli import main, seal_installation
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError
from omni.domain.installation import make_installation

REQUIRED = (
    "omni/__init__.py",
    "omni/web_assets/index.html",
    "omni/web_assets/ui/v2/index.html",
    "omnioperator-0.1.0.dist-info/METADATA",
)


async def _open_and_close(home):
    instance = await open_instance(home)
    await instance.close()


def candidate(tmp_path, drilled=True):
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
    if drilled:
        backup_candidate(paths, sha256)
        rollback_check(paths, sha256)
    return paths, sha256, path


def _second_candidate(tmp_path, paths, tag):
    path = tmp_path / f"{tag}.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for name in REQUIRED:
            archive.writestr(name, tag if name.endswith(".html") else "")
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    stage_wheel(paths.home, path, sha256)
    prepare_wheel(paths.home, sha256)
    smoke_candidate(paths, sha256)
    backup_candidate(paths, sha256)
    rollback_check(paths, sha256)
    switch_candidate(paths, sha256)
    return sha256


__all__ = [
    "BusinessError",
    "InstanceLock",
    "InstancePaths",
    "REQUIRED",
    "SystemClock",
    "_open_and_close",
    "_second_candidate",
    "asyncio",
    "backup_candidate",
    "candidate",
    "command",
    "configuration",
    "database_engine",
    "durable_json",
    "hashlib",
    "head",
    "install_candidate",
    "json",
    "main",
    "make_installation",
    "migrate_candidate",
    "open_instance",
    "prepare_wheel",
    "pytest",
    "rollback_candidate",
    "rollback_check",
    "runtime_current",
    "seal_installation",
    "smoke_candidate",
    "sqlite3",
    "stage_wheel",
    "switch_candidate",
    "sys",
    "zipfile",
]
