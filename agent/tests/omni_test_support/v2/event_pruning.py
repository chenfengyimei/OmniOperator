"""Reusable event_pruning fixtures, extracted without collecting test cases."""

from __future__ import annotations

import datetime as dt
import json

import pytest
from sqlalchemy import delete, select, update

from omni.adapters.persistence import audit_pruning, event_pruning, offline_plans, reference_release
from omni.adapters.persistence import schema as s
from omni.adapters.persistence.event_retention_repository import EventRetentionRepositories
from omni.adapters.persistence.maintenance import inspect_database, restore
from omni.adapters.persistence.migration_config import head as migration_head
from omni.adapters.persistence.repository_base import RepositoryBase
from omni.adapters.platform.runtime.paths import InstancePaths
from omni.domain.contracts import BusinessError
from omni_test_support.v2.audit_pruning import read_case, write_case
from omni_test_support.v2.audit_retention import _clock
from omni_test_support.v2.audit_retention import audit_template as audit_template
from omni_test_support.v2.audit_retention import prepared as prepared


@pytest.fixture
def event_case(prepared, tmp_path, monkeypatch):
    clock = _clock()
    monkeypatch.setattr(offline_plans, "SystemClock", lambda: clock)
    paths = InstancePaths.resolve(prepared["home"])
    output = tmp_path / "events-plan.json"
    event_pruning.plan_event_retention(paths, output, agent_id=prepared["agent"], limit=50)
    plan = event_pruning.read_event_plan(output)
    assert len(plan["targets"]) >= 5 and plan["schema_version"] == migration_head()
    return {
        **prepared,
        "paths": paths,
        "clock": clock,
        "plan": plan,
        "output": output,
        "backup": tmp_path / "event-backup",
    }


def event_ledger(case):
    return read_case(
        case,
        lambda u: {
            "batch": u.event_retention_batch(case["plan"]["id"]),
            "targets": [
                dict(row)
                for row in u.connection.execute(
                    select(s.event_retention_targets).where(s.event_retention_targets.c.batch_id == case["plan"]["id"])
                ).mappings()
            ],
            "scope": dict(
                u.connection.execute(
                    select(s.history_scopes).where(
                        s.history_scopes.c.kind == "events", s.history_scopes.c.scope_id == case["agent"]
                    )
                )
                .mappings()
                .one()
            ),
        },
    )


def apply_events(case):
    return event_pruning.apply_event_retention(case["paths"], case["plan"], case["backup"])


def business_snapshot(case):
    return read_case(
        case,
        lambda u: {
            table.name: [dict(row) for row in u.connection.execute(select(table)).mappings()]
            for table in (
                s.runs,
                s.messages,
                s.inbox,
                s.work_items,
                s.actions,
                s.approvals,
                s.model_calls,
                s.budget_accounts,
                s.context_snapshots,
                s.source_anchors,
                s.objects,
                s.action_objects,
                s.retention_batches,
                s.retention_targets,
            )
        },
    )


__all__ = [
    "BusinessError",
    "EventRetentionRepositories",
    "InstancePaths",
    "RepositoryBase",
    "_clock",
    "apply_events",
    "audit_pruning",
    "audit_template",
    "business_snapshot",
    "delete",
    "dt",
    "event_case",
    "event_ledger",
    "event_pruning",
    "inspect_database",
    "json",
    "migration_head",
    "offline_plans",
    "prepared",
    "pytest",
    "read_case",
    "reference_release",
    "restore",
    "s",
    "select",
    "update",
    "write_case",
]
