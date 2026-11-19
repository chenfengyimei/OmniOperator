"""Reusable audit_plan_migration fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from contextlib import contextmanager

import pytest
from alembic import command
from sqlalchemy import create_engine, delete, insert, inspect, select, update

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.database import Database
from omni.adapters.persistence.maintenance import verify_snapshot
from omni.adapters.persistence.migration_config import configuration
from omni.adapters.persistence.migration_config import head as migration_head
from omni.adapters.persistence.sqlite_access import sqlite_connection
from omni.adapters.platform.runtime.paths import InstanceLock, InstancePaths
from omni.adapters.primitives import SystemClock, UUIDs
from omni.domain.audit_retention import validate_audit_plan
from omni.domain.contracts import BusinessError
from omni_test_support.v2.audit_plan_contract import make_plan
from omni_test_support.v2.v2_configuration_helpers import add_model_headers_column
from omni_test_support.v2.v2_legacy_seed import LegacySeedRepositories, preserved_rows

NEW_TABLES = {"audit_retention_batches", "audit_retention_targets"}

NEW_INDEXES = {
    "ix_audit_retention_batches_agent_id",
    "ix_audit_retention_prepared_agent",
    "ix_audit_retention_targets_agent_id",
    "ix_audit_retention_pending_audit",
}

NEW_TRIGGERS = {
    "audit_retention_batch_identity",
    "audit_retention_target_identity",
    "audit_retention_batch_keep",
    "audit_retention_target_keep",
}

SOURCE_TABLES = (
    "instances",
    "agents",
    "runs",
    "messages",
    "run_events",
    "audit",
    "history_identity",
    "history_sequences",
    "history_scopes",
)


@contextmanager
def owned(paths):
    lock = InstanceLock(paths)
    lock.acquire()
    try:
        yield
    finally:
        lock.close()


def source_rows(connection):
    return preserved_rows(connection, SOURCE_TABLES)


def baseline(paths):
    paths.initialize()
    with owned(paths):
        engine = create_engine("sqlite+pysqlite:///" + str(paths.database))
        try:
            with engine.begin() as connection:
                cfg = configuration()
                cfg.attributes["connection"] = connection
                command.upgrade(cfg, "v2_0014")
                add_model_headers_column(connection)
                unit = LegacySeedRepositories(connection, SystemClock(), UUIDs())
                unit.initialize()
                agent = unit.query("agents", "")[0]["id"]
                unit.accept_turn(agent, None, "v14-request", "Preserved migration message", {})
            with sqlite_connection(paths.database) as connection:
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "v2_0014"
                assert not NEW_TABLES & {
                    row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                assert not connection.execute("PRAGMA foreign_key_check").fetchall()
                return source_rows(connection), connection.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
                ).fetchall()
        finally:
            engine.dispose()


def assert_schema(unit):
    reflection = inspect(unit.connection)
    assert len(s.metadata.tables) == 140
    assert set(s.metadata.tables) <= set(reflection.get_table_names())
    for name in NEW_TABLES:
        table = s.metadata.tables[name]
        columns = reflection.get_columns(name)
        assert {column["name"] for column in columns} == set(table.c.keys())
        assert {column["name"]: column["nullable"] for column in columns} == {
            column.name: column.nullable for column in table.c
        }
        assert {
            (tuple(item["constrained_columns"]), item["referred_table"], tuple(item["referred_columns"]))
            for item in reflection.get_foreign_keys(name)
        } == {
            (
                tuple(constraint.column_keys),
                constraint.referred_table.name,
                tuple(element.column.name for element in constraint.elements),
            )
            for constraint in table.foreign_key_constraints
        }
    assert not {item["referred_table"] for item in reflection.get_foreign_keys("audit_retention_targets")} & {
        "audit",
        "actions",
    }
    actual_indexes = {item["name"]: item for table in NEW_TABLES for item in reflection.get_indexes(table)}
    assert NEW_INDEXES <= set(actual_indexes)
    for name, column, state in (
        ("ix_audit_retention_prepared_agent", "agent_id", "prepared"),
        ("ix_audit_retention_pending_audit", "audit_id", "pending"),
    ):
        actual = actual_indexes[name]
        assert actual["unique"] == 1 and actual["column_names"] == [column]
        assert str(actual["dialect_options"]["sqlite_where"]).replace(" ", "") == f"state='{state}'"
    assert NEW_TRIGGERS <= {
        row[0] for row in unit.connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='trigger'")
    }


def evidence_rows(unit):
    """Create DDL-valid evidence directly; this fixture grants no deletion authorization."""
    instance = unit.initialize()
    agent = unit.query("agents", "")[0]["id"]
    run = unit.accept_turn(agent, None, "schema-evidence", "Preserved evidence source", {})
    audit_id = unit.audit(agent, "run.completed", run["run_id"], {})
    sequence = unit.connection.execute(select(s.audit.c.sequence).where(s.audit.c.id == audit_id)).scalar_one()
    plan = make_plan()
    plan.update(
        instance_id=instance["id"],
        agent_id=agent,
        epoch=unit.connection.execute(select(s.history_identity.c.epoch)).scalar_one(),
        high_water=sequence,
    )
    target = plan["targets"][0]
    target.update(
        audit_id=audit_id, agent_id=agent, operation_id=run["run_id"], run_id=run["run_id"], sequence=sequence
    )
    unit.connection.execute(
        insert(s.retention_batches).values(
            id=target["prune_plan_id"],
            created_at=plan["created_at"],
            instance_id=instance["id"],
            plan_sha256="c" * 64,
            policy="execution-body-v1",
            cutoff=plan["cutoff"],
            schema_version="v2_0015",
            backup_path="fixture/body-snapshot",
            backup_sha256="d" * 64,
            state="prepared",
        )
    )
    batch = {
        key: plan[key]
        for key in (
            "id",
            "agent_id",
            "created_at",
            "instance_id",
            "policy",
            "schema_version",
            "as_of",
            "cutoff",
            "epoch",
            "trim_revision",
            "high_water",
        )
    }
    batch.update(
        plan_sha256=validate_audit_plan(plan, plan["created_at"]),
        backup_path="fixture/audit-snapshot",
        backup_sha256="e" * 64,
        state="prepared",
        completed_at=None,
    )
    target_row = {"batch_id": plan["id"], **target, "state": "pending", "ended_at": None}
    unit.connection.execute(insert(s.audit_retention_batches).values(**batch))
    unit.connection.execute(insert(s.audit_retention_targets).values(**target_row))
    return batch, target_row, run


__all__ = [
    "BusinessError",
    "Database",
    "InstanceLock",
    "InstancePaths",
    "LegacySeedRepositories",
    "NEW_INDEXES",
    "NEW_TABLES",
    "NEW_TRIGGERS",
    "SOURCE_TABLES",
    "SystemClock",
    "UUIDs",
    "add_model_headers_column",
    "assert_schema",
    "asyncio",
    "baseline",
    "command",
    "configuration",
    "contextmanager",
    "create_engine",
    "delete",
    "evidence_rows",
    "insert",
    "inspect",
    "make_plan",
    "migration_head",
    "owned",
    "preserved_rows",
    "pytest",
    "s",
    "select",
    "source_rows",
    "sqlite_connection",
    "subprocess",
    "sys",
    "update",
    "validate_audit_plan",
    "verify_snapshot",
]
