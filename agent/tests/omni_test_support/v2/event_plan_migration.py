"""Reusable event_plan_migration fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from itertools import chain

import pytest
from alembic import command
from sqlalchemy import CheckConstraint, UniqueConstraint, create_engine, delete, insert, inspect, select, update

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.database import Database
from omni.adapters.persistence.maintenance import verify_snapshot
from omni.adapters.persistence.migration_config import configuration
from omni.adapters.persistence.migration_config import head as migration_head
from omni.adapters.persistence.sqlite_access import sqlite_connection
from omni.adapters.platform.runtime.paths import InstancePaths
from omni.adapters.primitives import SystemClock, UUIDs
from omni.domain.contracts import BusinessError
from omni.domain.event_retention import validate_event_plan
from omni_test_support.v2.audit_plan_migration import owned
from omni_test_support.v2.event_retention_contract import make_plan
from omni_test_support.v2.v2_configuration_helpers import add_model_headers_column
from omni_test_support.v2.v2_legacy_seed import LegacySeedRepositories, preserved_rows

NEW_TABLES = {"event_retention_batches", "event_retention_targets"}

NEW_INDEXES = {
    "ix_event_retention_batches_agent_id",
    "ix_event_retention_prepared_agent",
    "ix_event_retention_targets_agent_id",
    "ix_event_retention_pending_sequence",
}

NEW_TRIGGERS = {
    "event_retention_batch_identity",
    "event_retention_target_identity",
    "event_retention_batch_keep",
    "event_retention_target_keep",
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
    "retention_batches",
    "audit_retention_batches",
    "audit_retention_targets",
)


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
                command.upgrade(cfg, "v2_0015")
                add_model_headers_column(connection)
                unit = LegacySeedRepositories(connection, SystemClock(), UUIDs())
                unit.initialize()
                agent = unit.query("agents", "")[0]["id"]
                unit.accept_turn(agent, None, "v15-request", "Preserved event migration message", {})
            with sqlite_connection(paths.database) as connection:
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "v2_0015"
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


def normalized_check(text):
    value = "".join(str(text).lower().split())
    while value.startswith("(") and value.endswith(")"):
        depth = 0
        for index, char in enumerate(value):
            depth += (char == "(") - (char == ")")
            if depth == 0:
                break
        if index != len(value) - 1:
            break
        value = value[1:-1]
    return value


def assert_schema(unit):
    reflection = inspect(unit.connection)
    assert len(s.metadata.tables) == 147
    assert set(s.metadata.tables) <= set(reflection.get_table_names())
    for name in NEW_TABLES:
        table = s.metadata.tables[name]
        actual_columns = reflection.get_columns(name)
        assert {column["name"] for column in actual_columns} == set(table.c.keys())
        assert {column["name"]: column["nullable"] for column in actual_columns} == {
            column.name: column.nullable for column in table.c
        }
        assert tuple(reflection.get_pk_constraint(name)["constrained_columns"]) == tuple(
            table.primary_key.columns.keys()
        )
        assert {tuple(item["column_names"]) for item in reflection.get_unique_constraints(name)} == {
            tuple(constraint.columns.keys())
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        assert {normalized_check(item["sqltext"]) for item in reflection.get_check_constraints(name)} == {
            normalized_check(constraint.sqltext)
            for constraint in chain(table.constraints, *(column.constraints for column in table.c))
            if isinstance(constraint, CheckConstraint)
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
    assert not {item["referred_table"] for item in reflection.get_foreign_keys("event_retention_targets")} & {
        "run_events",
        "audit",
        "actions",
    }
    indexes = {item["name"]: item for table in NEW_TABLES for item in reflection.get_indexes(table)}
    assert NEW_INDEXES <= set(indexes)
    for name, column, state in (
        ("ix_event_retention_prepared_agent", "agent_id", "prepared"),
        ("ix_event_retention_pending_sequence", "sequence", "pending"),
    ):
        actual = indexes[name]
        assert actual["unique"] == 1 and actual["column_names"] == [column]
        assert str(actual["dialect_options"]["sqlite_where"]).replace(" ", "") == f"state='{state}'"
    assert NEW_TRIGGERS <= {
        row[0] for row in unit.connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='trigger'")
    }


def evidence_rows(unit):
    """Create DDL-valid historical evidence directly; this fixture does not authorize pruning."""
    instance = unit.initialize()
    agent = unit.query("agents", "")[0]["id"]
    run = unit.accept_turn(agent, None, "event-schema-evidence", "Preserved event evidence source", {})
    event = dict(
        unit.connection.execute(
            select(s.run_events).where(s.run_events.c.run_id == run["run_id"], s.run_events.c.kind == "run.queued")
        )
        .mappings()
        .one()
    )
    plan = make_plan(1)
    plan.update(
        instance_id=instance["id"],
        agent_id=agent,
        epoch=unit.connection.execute(select(s.history_identity.c.epoch)).scalar_one(),
        high_water=event["sequence"],
    )
    target = plan["targets"][0]
    target.update(
        agent_id=agent, run_id=run["run_id"], sequence=event["sequence"], seq=event["seq"], kind=event["kind"]
    )
    unit.connection.execute(
        insert(s.retention_batches).values(
            id=target["prune_plan_id"],
            created_at=plan["created_at"],
            instance_id=instance["id"],
            plan_sha256="c" * 64,
            policy="execution-body-v1",
            cutoff=plan["cutoff"],
            schema_version="v2_0016",
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
        plan_sha256=validate_event_plan(plan, plan["created_at"]),
        backup_path="fixture/event-snapshot",
        backup_sha256="e" * 64,
        state="prepared",
        completed_at=None,
    )
    target_row = {"batch_id": plan["id"], **target, "state": "pending", "ended_at": None}
    unit.connection.execute(insert(s.event_retention_batches).values(**batch))
    unit.connection.execute(insert(s.event_retention_targets).values(**target_row))
    return batch, target_row, run


__all__ = [
    "BusinessError",
    "CheckConstraint",
    "Database",
    "InstancePaths",
    "LegacySeedRepositories",
    "NEW_INDEXES",
    "NEW_TABLES",
    "NEW_TRIGGERS",
    "SOURCE_TABLES",
    "SystemClock",
    "UUIDs",
    "UniqueConstraint",
    "add_model_headers_column",
    "assert_schema",
    "asyncio",
    "baseline",
    "chain",
    "command",
    "configuration",
    "create_engine",
    "delete",
    "evidence_rows",
    "insert",
    "inspect",
    "make_plan",
    "migration_head",
    "normalized_check",
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
    "validate_event_plan",
    "verify_snapshot",
]
