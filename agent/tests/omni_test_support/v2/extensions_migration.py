"""Reusable extensions_migration fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import sys
from itertools import chain

import pytest
from alembic import command
from sqlalchemy import CheckConstraint, UniqueConstraint, create_engine, delete, inspect, select, update

from omni.adapters.extensions.packages import capture_directory
from omni.adapters.persistence import schema as s
from omni.adapters.persistence.database import Database
from omni.adapters.persistence.maintenance import backup, inspect_database, restore, verify_snapshot
from omni.adapters.persistence.migration_config import configuration
from omni.adapters.persistence.migration_config import head as migration_head
from omni.adapters.persistence.object_inventory import registry
from omni.adapters.persistence.sqlite_access import sqlite_connection
from omni.adapters.platform.runtime.paths import InstancePaths
from omni.adapters.primitives import SystemClock, UUIDs
from omni.domain.contracts import BusinessError
from omni.domain.extensions import tool_spec
from omni_test_support.v2.audit_plan_migration import owned
from omni_test_support.v2.event_plan_migration import normalized_check
from omni_test_support.v2.extensions_contract import chinese_manifest, encoded, manifest
from omni_test_support.v2.objects import Clock, opened
from omni_test_support.v2.v2_configuration_helpers import add_model_headers_column
from omni_test_support.v2.v2_legacy_seed import LegacySeedRepositories, preserved_rows

TABLES = {
    "extensions",
    "extension_revisions",
    "extension_tools",
    "grants",
    "run_extension_tools",
    "action_extension_bindings",
    "extension_install_requests",
}

SOURCES = (
    "principals",
    "instances",
    "agents",
    "people",
    "sessions",
    "messages",
    "runs",
    "audit",
    "memory_entries",
    "memory_revisions",
    "source_anchors",
    "history_identity",
    "history_sequences",
    "history_scopes",
)

FROZEN = (
    "extension_revisions",
    "extension_tools",
    "run_extension_tools",
    "action_extension_bindings",
    "extension_install_requests",
)


def raw_run(unit, agent):
    """Use v18 fields directly; current turn admission deliberately requires the v19 capture schema."""
    session = unit._record(s.sessions, {"title": "Preserved old run", "revision": 1, "next_seq": 1}, agent)
    message = unit._record(
        s.messages, {"session_id": session["id"], "seq": 1, "role": "user", "content": "Original input"}, agent
    )
    run = unit._record(
        s.runs,
        {
            "session_id": session["id"],
            "input_message_id": message["id"],
            "state": "queued",
            "config_revision": 1,
            "last_seq": 0,
            "error_code": "",
            **unit.capture_configuration(agent),
        },
        agent,
    )
    unit.connection.execute(update(s.messages).where(s.messages.c.id == message["id"]).values(run_id=run["id"]))
    return run


def baseline(paths, *, placeholder=None):
    paths.initialize()
    with owned(paths):
        engine = create_engine("sqlite+pysqlite:///" + str(paths.database))
        try:
            with engine.begin() as connection:
                cfg = configuration()
                cfg.attributes["connection"] = connection
                command.upgrade(cfg, "v2_0018")
                add_model_headers_column(connection)
                unit = LegacySeedRepositories(connection, SystemClock(), UUIDs())
                unit.initialize()
                agent = unit.query("agents", "")[0]["id"]
                raw_run(unit, agent)
                unit.remember(agent, "Preserved original fact")
                if placeholder:
                    extension_id = UUIDs().new()
                    connection.exec_driver_sql(
                        "INSERT INTO extensions(id,agent_id,created_at,kind,name,revision,manifest,fingerprint,enabled) "
                        "VALUES(?,?,?,'plugin','old-placeholder',1,'{}',?,0)",
                        (extension_id, agent, unit.clock.now(), "a" * 64),
                    )
                    if placeholder == "grants":
                        connection.exec_driver_sql(
                            "INSERT INTO grants(id,agent_id,created_at,extension_id,fingerprint,tool) VALUES(?,?,?,?,?,'old-tool')",
                            (UUIDs().new(), agent, unit.clock.now(), extension_id, "a" * 64),
                        )
            with sqlite_connection(paths.database) as connection:
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "v2_0018"
                assert "extension_revisions" not in {
                    row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                rows = preserved_rows(connection, SOURCES + ("extensions", "grants"))
                schema = connection.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
                ).fetchall()
                assert not connection.execute("PRAGMA foreign_key_check").fetchall()
                return rows, schema
        finally:
            engine.dispose()


def assert_schema(unit):
    reflection = inspect(unit.connection)
    assert len(s.metadata.tables) == 167 and set(s.metadata.tables) <= set(reflection.get_table_names())
    for name in TABLES:
        table = s.metadata.tables[name]
        columns = reflection.get_columns(name)
        assert {column["name"]: column["nullable"] for column in columns} == {
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
        assert {
            item["name"]: (tuple(item["column_names"]), bool(item["unique"])) for item in reflection.get_indexes(name)
        } == {
            index.name: (tuple(column.name for column in index.columns), bool(index.unique)) for index in table.indexes
        }
    pointer = next(
        item for item in reflection.get_foreign_keys("extensions") if item["referred_table"] == "extension_revisions"
    )
    assert pointer["options"]["deferrable"] is True and pointer["options"]["initially"] == "DEFERRED"
    assert (
        "AUTOINCREMENT"
        in unit.connection.exec_driver_sql("SELECT sql FROM sqlite_master WHERE name='extensions'").scalar_one()
    )
    assert "extension_capture_epoch" in {column["name"] for column in reflection.get_columns("runs")}
    assert "ix_memory_organization_review_audit" in {
        item["name"] for item in reflection.get_indexes("memory_organization_candidates")
    }
    object_owners = {(table.name, column.name) for table, column in s.OBJECT_REFERENCES}
    audit_owners = {(table.name, column.name) for table, column in s.AUDIT_REFERENCES}
    assert ("extension_revisions", "object_id") in object_owners
    assert ("grants", "review_audit_id") in audit_owners
    assert ("memory_organization_candidates", "review_audit_id") in audit_owners
    metadata_refs = {
        (table.name, fk.parent.name)
        for table in s.metadata.tables.values()
        for fk in table.foreign_keys
        if fk.target_fullname == "objects.id" and table.info.get("object_metadata")
    }
    assert metadata_refs == {("image_objects", "object_id")}
    assert not object_owners & metadata_refs
    image_fk = next(
        item for item in reflection.get_foreign_keys("image_objects") if item["referred_table"] == "objects"
    )
    assert image_fk["options"]["ondelete"] == "CASCADE"
    assert object_owners | metadata_refs == {
        (table.name, fk.parent.name)
        for table in s.metadata.tables.values()
        for fk in table.foreign_keys
        if fk.target_fullname == "objects.id"
    }
    assert audit_owners == {
        (table.name, fk.parent.name)
        for table in s.metadata.tables.values()
        for fk in table.foreign_keys
        if fk.target_fullname == "audit.id"
    }
    triggers = {
        row[0] for row in unit.connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='trigger'")
    }
    assert {name + "_immutable" for name in FROZEN} <= triggers
    assert {name + "_keep" for name in TABLES} <= triggers
    assert {
        "extension_slot_identity",
        "extension_grant_identity",
        "extension_tool_name",
        "extension_run_capture_identity",
        "extension_run_tool_authority",
        "extension_action_binding_identity",
    } <= triggers


async def installed(database, objects, agent, source, value=None):
    source.mkdir()
    (source / "extension.json").write_bytes(encoded(value or manifest()))
    (source / "plugin.py").write_text(
        "raise AssertionError('Metadata capture must never import this plugin')\n", encoding="utf-8"
    )
    package = capture_directory(source)
    object_row = await objects.publish(agent, package.content, "application/zip")
    captured = {
        "manifest": package.manifest,
        "files": package.files,
        "package_sha256": package.package_sha256,
        "catalog_sha256": package.catalog_sha256,
    }
    result = await database.write(
        lambda u: u.extension_install(
            agent,
            {"key": "import-" + source.name, "source": str(source), "extension_id": None, "revision": 0},
            captured,
            object_row["id"],
        )
    )
    return result, object_row, package


async def enable(database, agent, slot):
    return await database.write(
        lambda u: u.extension_authorize(
            agent,
            slot["id"],
            {
                "revision": slot["revision"],
                "package_sha256": slot["package_sha256"],
                "catalog_sha256": slot["catalog_sha256"],
                "history_epoch": slot["history_epoch"],
                "tools": [row["name"] for row in slot["tools"]],
                "trust": True,
            },
        )
    )


def action_fixture(unit, agent, state):
    receipt = unit.accept_turn(agent, None, "plugin-" + state, "Owner-approved plugin action", {})
    run_id = receipt["run_id"]
    claim = next(row for row in unit.claim_runs(1) if row["id"] == run_id)
    unit.start_claimed_run(agent, run_id, claim["claim_token"], claim["lease_fence"])
    unit.transition(agent, run_id, "running")
    row = unit.extension_run_tools(agent, run_id)[0]
    spec = tool_spec(row)
    policy = unit.settings(agent)["policy_revision"]
    action = unit.prepare_action(agent, run_id, spec, {}, policy)
    approval = next(row for row in unit.pending_approvals(agent) if row["action_id"] == action["id"])
    if state != "pending":
        unit.resolve_approval(agent, approval["id"], True, policy)
    if state == "executing":
        unit.authorize_action(agent, action["id"])
    return {"run_id": run_id, "action_id": action["id"], "approval_id": approval["id"], "binding_id": row["binding_id"]}


__all__ = [
    "BusinessError",
    "CheckConstraint",
    "Clock",
    "Database",
    "FROZEN",
    "InstancePaths",
    "LegacySeedRepositories",
    "SOURCES",
    "SystemClock",
    "TABLES",
    "UUIDs",
    "UniqueConstraint",
    "action_fixture",
    "add_model_headers_column",
    "assert_schema",
    "asyncio",
    "backup",
    "baseline",
    "capture_directory",
    "chain",
    "chinese_manifest",
    "command",
    "configuration",
    "create_engine",
    "delete",
    "enable",
    "encoded",
    "inspect",
    "inspect_database",
    "installed",
    "json",
    "manifest",
    "migration_head",
    "normalized_check",
    "opened",
    "owned",
    "preserved_rows",
    "pytest",
    "raw_run",
    "registry",
    "restore",
    "s",
    "select",
    "sqlite3",
    "sqlite_connection",
    "subprocess",
    "sys",
    "tool_spec",
    "update",
    "verify_snapshot",
]
