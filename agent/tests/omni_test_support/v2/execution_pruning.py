"""Reusable execution_pruning fixtures, extracted without collecting test cases."""

import asyncio
import copy
import datetime as dt
import json
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, update

from omni.adapters.persistence import execution_pruning as pruning
from omni.adapters.persistence import offline_plans, repository_base
from omni.adapters.persistence import schema as s
from omni.adapters.persistence.maintenance import fingerprint, inspect_database, restore
from omni.adapters.persistence.migration_config import configuration
from omni.adapters.persistence.migration_config import head as migration_head
from omni.adapters.persistence.operations_read import offline_read
from omni.adapters.persistence.repository_base import RepositoryBase
from omni.adapters.persistence.sqlite_access import sqlite_connection
from omni.adapters.platform.runtime.paths import InstanceLock, InstancePaths
from omni.adapters.primitives import SystemClock, UUIDs
from omni.composition.http import http_app
from omni.domain.contracts import ActionResult, BusinessError, ModelReply, Outcome
from omni.domain.retention import body_fingerprint, validate_retention_plan
from omni_test_support.v2.documents import read_action
from omni_test_support.v2.objects import Clock, opened
from omni_test_support.v2.retention_preview import seed
from omni_test_support.v2.v2_configuration_helpers import add_model_headers_column
from omni_test_support.v2.v2_legacy_seed import LegacySeedRepositories


def prepare(tmp_path):
    home = tmp_path / "home"

    async def setup():
        clock = Clock()
        clock.current -= dt.timedelta(days=40)
        async with opened(home, clock) as (_, db, objects, agent):
            turn, action = await db.write(lambda u: seed(u, agent))
            call = await db.write(lambda u: u.reserve_call(agent, turn["run_id"], 100, 1000, "fixture"))
            await db.write(lambda u: u.settle_call(agent, call, ModelReply("", cost_micro=7)))
            blob = await objects.publish(agent, b"original referenced artifact", "text/plain")
            await db.write(lambda u: u.object_link(agent, blob["id"], "action", action["id"]))
            return agent, turn, action, blob

    agent, turn, action, blob = asyncio.run(setup())
    paths = InstancePaths.resolve(home)
    output = tmp_path / "prune.json"
    pruning.plan_pruning(paths, output)
    return paths, pruning.read_pruning_plan(output), agent, turn, action, blob


def read(paths, operation):
    # Test helper for an offline instance, matching the CLI's ownership contract.
    lock = InstanceLock(paths)
    lock.acquire()
    try:
        result = offline_read(paths.database, SystemClock(), UUIDs(), operation)
        result.pop("verification")
        return result
    finally:
        lock.close()


__all__ = [
    "ActionResult",
    "BusinessError",
    "Clock",
    "InstanceLock",
    "InstancePaths",
    "LegacySeedRepositories",
    "ModelReply",
    "Outcome",
    "RepositoryBase",
    "SystemClock",
    "TestClient",
    "UUIDs",
    "add_model_headers_column",
    "asyncio",
    "body_fingerprint",
    "configuration",
    "copy",
    "create_engine",
    "dt",
    "fingerprint",
    "http_app",
    "inspect_database",
    "json",
    "migration_head",
    "offline_plans",
    "offline_read",
    "opened",
    "prepare",
    "pruning",
    "pytest",
    "read",
    "read_action",
    "repository_base",
    "restore",
    "s",
    "seed",
    "select",
    "sqlite_connection",
    "subprocess",
    "sys",
    "update",
    "validate_retention_plan",
]
