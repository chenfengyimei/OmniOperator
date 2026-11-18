"""Reusable objects fixtures, extracted without collecting test cases."""

import asyncio
import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy import insert

from omni.adapters import object_store
from omni.adapters.object_store import FileObjects, ObjectFiles
from omni.adapters.persistence import schema as s
from omni.adapters.persistence.database import Database
from omni.adapters.persistence.maintenance import backup, object_report, restore
from omni.adapters.persistence.migration_config import head as migration_head
from omni.adapters.persistence.migrations.versions.v0001 import SQL
from omni.adapters.persistence.object_gc import apply_collection, plan_collection
from omni.adapters.persistence.object_inventory import registry
from omni.adapters.platform.runtime.paths import InstanceLock, InstancePaths
from omni.adapters.primitives import SystemClock, UUIDs
from omni.application.objects import ObjectService
from omni.domain.contracts import ActionResult, BusinessError, Outcome, ToolSpec
from omni.domain.objects import Blob


class Clock(SystemClock):
    def __init__(self):
        self.current = dt.datetime.now(dt.timezone.utc)

    def now(self):
        return self.current.isoformat(timespec="microseconds")


@asynccontextmanager
async def opened(home, clock):
    paths = InstancePaths.resolve(home)
    paths.initialize()
    lock = InstanceLock(paths)
    lock.acquire()
    db = Database(paths.database, clock, UUIDs(), objects_root=paths.objects)
    await db.write(lambda u: u.initialize())
    service = ObjectService(db, ObjectFiles(paths.objects), clock)
    try:
        agent = (await db.read(lambda u: u.query("agents", "")))[0]["id"]
        yield paths, db, service, agent
    finally:
        assert await service.close()
        await db.close()
        lock.close()


__all__ = [
    "ActionResult",
    "Blob",
    "BusinessError",
    "Clock",
    "Database",
    "FileObjects",
    "InstanceLock",
    "InstancePaths",
    "ObjectFiles",
    "ObjectService",
    "Outcome",
    "Path",
    "SQL",
    "SystemClock",
    "ToolSpec",
    "UUIDs",
    "apply_collection",
    "asynccontextmanager",
    "asyncio",
    "backup",
    "dt",
    "insert",
    "json",
    "migration_head",
    "object_report",
    "object_store",
    "opened",
    "os",
    "plan_collection",
    "pytest",
    "registry",
    "restore",
    "s",
    "sqlite3",
    "subprocess",
    "sys",
    "uuid",
]
