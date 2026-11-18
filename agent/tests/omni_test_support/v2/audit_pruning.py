"""Reusable audit_pruning fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import copy
import datetime as dt
import json

import pytest
from sqlalchemy import delete, select, update

from omni.adapters.persistence import audit_pruning, offline_plans
from omni.adapters.persistence import schema as s
from omni.adapters.persistence.audit_pruning_repository import AuditPruningRepositories
from omni.adapters.persistence.maintenance import inspect_database, restore
from omni.adapters.persistence.migration_config import head as migration_head
from omni.adapters.persistence.operations_read import offline_read
from omni.adapters.persistence.repository_base import RepositoryBase
from omni.adapters.platform.runtime.paths import InstanceLock, InstancePaths
from omni.adapters.primitives import UUIDs
from omni.domain.contracts import BusinessError
from omni_test_support.v2.audit_retention import _clock
from omni_test_support.v2.audit_retention import audit_template as audit_template
from omni_test_support.v2.audit_retention import prepared as prepared
from omni_test_support.v2.objects import opened


@pytest.fixture
def audit_case(prepared, tmp_path, monkeypatch):
    clock = _clock()
    monkeypatch.setattr(offline_plans, "SystemClock", lambda: clock)
    paths = InstancePaths.resolve(prepared["home"])
    output = tmp_path / "audit-plan.json"
    audit_pruning.plan_audit_retention(paths, output, agent_id=prepared["agent"], limit=50)
    plan = audit_pruning.read_audit_plan(output)
    assert len(plan["targets"]) >= 2 and plan["schema_version"] == migration_head()
    return {
        **prepared,
        "paths": paths,
        "plan": plan,
        "output": output,
        "backup": tmp_path / "audit-backup",
        "clock": clock,
    }


def read_case(case, operation):
    paths = case["paths"]
    lock = InstanceLock(paths)
    lock.acquire()
    try:
        report = offline_read(paths.database, case["clock"], UUIDs(), operation)
        report.pop("verification")
        return report
    finally:
        lock.close()


def write_case(case, operation):
    async def execute():
        async with opened(case["paths"].home, case["clock"]) as (_, db, _, _):
            return await db.write(operation)

    return asyncio.run(execute())


def ledger(case):
    return read_case(
        case,
        lambda u: {
            "batch": u.audit_retention_batch(case["plan"]["id"]),
            "targets": [
                dict(row)
                for row in u.connection.execute(
                    select(s.audit_retention_targets).where(s.audit_retention_targets.c.batch_id == case["plan"]["id"])
                ).mappings()
            ],
            "scope": dict(
                u.connection.execute(
                    select(s.history_scopes).where(
                        s.history_scopes.c.kind == "audit", s.history_scopes.c.scope_id == case["agent"]
                    )
                )
                .mappings()
                .one()
            ),
        },
    )


def apply(case):
    return audit_pruning.apply_audit_retention(case["paths"], case["plan"], case["backup"])


__all__ = [
    "AuditPruningRepositories",
    "BusinessError",
    "InstanceLock",
    "InstancePaths",
    "RepositoryBase",
    "UUIDs",
    "_clock",
    "apply",
    "asyncio",
    "audit_case",
    "audit_pruning",
    "audit_template",
    "copy",
    "delete",
    "dt",
    "inspect_database",
    "json",
    "ledger",
    "migration_head",
    "offline_plans",
    "offline_read",
    "opened",
    "prepared",
    "pytest",
    "read_case",
    "restore",
    "s",
    "select",
    "update",
    "write_case",
]
