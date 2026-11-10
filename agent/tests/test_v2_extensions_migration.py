"""V19 frozen DDL, empty captures, referenced source archives and restore revocation; never executes plugin code."""

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
from test_v2_audit_plan_migration import owned
from test_v2_event_plan_migration import normalized_check
from test_v2_extensions_contract import chinese_manifest, encoded, manifest
from test_v2_objects import Clock, opened
from v2_legacy_seed import LegacySeedRepositories, preserved_rows

from omni.adapters.extensions.packages import capture_directory
from omni.adapters.persistence import schema as s
from omni.adapters.persistence.database import Database
from omni.adapters.persistence.maintenance import backup, inspect_database, restore, verify_snapshot
from omni.adapters.persistence.migration_config import configuration
from omni.adapters.persistence.migration_config import head as migration_head
from omni.adapters.persistence.object_inventory import registry
from omni.adapters.persistence.sqlite_access import sqlite_connection
from omni.adapters.platform.paths import InstancePaths
from omni.adapters.primitives import SystemClock, UUIDs
from omni.domain.contracts import BusinessError
from omni.domain.extensions import tool_spec

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
    assert len(s.metadata.tables) == 136 and set(s.metadata.tables) <= set(reflection.get_table_names())
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


@pytest.mark.parametrize(
    "marker",
    [
        "CREATE TABLE extension_tools",
        "CREATE INDEX ix_grants_review_audit",
        "CREATE TRIGGER extension_install_requests_keep",
    ],
    ids=["table", "index", "trigger"],
)
def test_v19_process_exit_rolls_back_frozen_ddl_then_retries_with_verified_v18_snapshots(tmp_path, marker):
    paths = InstancePaths.resolve(tmp_path / "instance")
    original, old_schema = baseline(paths)
    script = tmp_path / "interrupt_v19.py"
    script.write_text(
        """import os,sys
from pathlib import Path
from sqlalchemy import Engine,event
from omni.adapters.persistence.database import Database
from omni.adapters.platform.paths import InstanceLock,InstancePaths
from omni.adapters.primitives import SystemClock,UUIDs
paths=InstancePaths.resolve(Path(sys.argv[1]))
lock=InstanceLock(paths)
lock.acquire()
@event.listens_for(Engine,'after_cursor_execute')
def cut(connection,cursor,statement,parameters,context,executemany):
    if statement.strip().startswith(sys.argv[2]): os._exit(99)
Database(paths.database,SystemClock(),UUIDs(),objects_root=paths.objects)
raise AssertionError('Requested v19 interruption did not occur')
""",
        encoding="utf-8",
    )
    child = subprocess.run(
        [sys.executable, str(script), str(paths.home), marker],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert child.returncode == 99, child.stderr
    with owned(paths):
        with sqlite_connection(paths.database) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "v2_0018"
            assert (
                connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
                == old_schema
            )
            assert preserved_rows(connection, SOURCES + ("extensions", "grants")) == original
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert not connection.execute("PRAGMA foreign_key_check").fetchall()

        async def retry():
            database = Database(paths.database, SystemClock(), UUIDs(), objects_root=paths.objects)
            try:
                assert database.schema_version == migration_head()
                await database.read(assert_schema)
                assert (await database.integrity())["foreign_keys"] == 0
            finally:
                await database.close()

        asyncio.run(retry())
        with sqlite_connection(paths.database) as connection:
            assert preserved_rows(connection, SOURCES) == {
                key: value for key, value in original.items() if key in SOURCES
            }
            epoch = connection.execute("SELECT epoch FROM history_identity WHERE slot=1").fetchone()[0]
            assert connection.execute("SELECT DISTINCT extension_capture_epoch FROM runs").fetchall() == [(epoch,)]
    snapshots = list(paths.database.parent.glob("schema-backups/*"))
    assert len(snapshots) == 2
    for snapshot in snapshots:
        report = verify_snapshot(snapshot)
        assert report["complete"] and report["database"]["schema"] == "v2_0018"
        assert report["database"]["integrity"] == "ok" and report["database"]["foreign_key_errors"] == 0


@pytest.mark.parametrize("placeholder", ["extensions", "grants"])
def test_v19_refuses_nonempty_placeholders_without_mutation_and_keeps_restorable_backup(tmp_path, placeholder):
    paths = InstancePaths.resolve(tmp_path / "instance")
    original, old_schema = baseline(paths, placeholder=placeholder)
    with owned(paths):
        with pytest.raises(BusinessError) as failure:
            Database(paths.database, SystemClock(), UUIDs(), objects_root=paths.objects)
        assert failure.value.code == "extension_placeholder_not_empty"
        with sqlite_connection(paths.database) as connection:
            assert preserved_rows(connection, SOURCES + ("extensions", "grants")) == original
            assert (
                connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
                == old_schema
            )
    snapshots = list(paths.database.parent.glob("schema-backups/*"))
    assert len(snapshots) == 1 and verify_snapshot(snapshots[0])["database"]["schema"] == "v2_0018"
    destination = tmp_path / "restored-v18"
    report = restore(snapshots[0], destination)
    assert report["database"]["schema"] == "v2_0018" and report["extensions_quarantined"] is True
    with sqlite_connection(destination / "data/omni.db") as connection:
        assert preserved_rows(connection, ("extensions", "grants")) == {
            key: original[key] for key in ("extensions", "grants")
        }
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()


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


def test_v19_deferred_cycle_full_schema_source_object_and_audit_references_are_real(tmp_path):
    async def scenario():
        async with opened(tmp_path / "instance", Clock()) as (paths, database, objects, agent):
            assert database.schema_version == migration_head()
            await database.read(assert_schema)
            slot, source, package = await installed(database, objects, agent, tmp_path / "source", chinese_manifest())
            assert slot["enabled"] is False and slot["file_count"] == 2 and slot["package_sha256"] == source["sha256"]
            assert slot["manifest"] == package.manifest and slot["files"] == list(package.files)
            assert len(json.dumps(slot["manifest"]).encode()) > 65_536
            reference = await database.read(lambda u: u.object_get(agent, source["id"]))
            assert reference["references"] == 1
            assert source["id"] in registry(paths.database)[3]
            slot = await enable(database, agent, slot)
            grant = await database.read(
                lambda u: dict(
                    u.connection.execute(select(s.grants).where(s.grants.c.extension_id == slot["id"]))
                    .mappings()
                    .first()
                )
            )
            with pytest.raises(BusinessError) as failure:
                await database.write(
                    lambda u: u.connection.execute(delete(s.audit).where(s.audit.c.id == grant["review_audit_id"]))
                )
            assert failure.value.code == "data_constraint"
            with pytest.raises(BusinessError) as failure:
                await database.write(
                    lambda u: u.connection.execute(delete(s.objects).where(s.objects.c.id == source["id"]))
                )
            assert failure.value.code == "data_constraint"
            report = inspect_database(paths.database)
            assert report["enabled_extensions"] == 1 and report["extension_attention"] == 0
            assert (await database.integrity())["foreign_keys"] == 0

    asyncio.run(scenario())


def test_empty_run_capture_cannot_gain_later_grants_or_have_marker_cleared(tmp_path):
    async def scenario():
        async with opened(tmp_path / "instance", Clock()) as (_, database, objects, agent):
            old = await database.write(lambda u: u.accept_turn(agent, None, "empty", "No enabled extensions yet", {}))
            marker = (await database.read(lambda u: u.get_run(agent, old["run_id"])))["extension_capture_epoch"]
            assert marker and await database.read(lambda u: u.extension_run_tools(agent, old["run_id"])) == []
            slot, _, _ = await installed(database, objects, agent, tmp_path / "source")
            await enable(database, agent, slot)
            await database.write(lambda u: u.extension_capture_run(agent, old["run_id"]))
            assert await database.read(lambda u: u.extension_run_tools(agent, old["run_id"])) == []
            with pytest.raises(BusinessError) as failure:
                await database.write(
                    lambda u: u.connection.execute(
                        update(s.runs).where(s.runs.c.id == old["run_id"]).values(extension_capture_epoch=None)
                    )
                )
            assert failure.value.code == "data_constraint"
            new = await database.write(lambda u: u.accept_turn(agent, None, "new", "New capture sees enabled tool", {}))
            rows = await database.read(lambda u: u.extension_run_tools(agent, new["run_id"]))
            assert len(rows) == 1
            raw = await database.read(lambda u: u._one(s.run_extension_tools, agent, rows[0]["binding_id"]))
            with pytest.raises(BusinessError) as failure:
                await database.write(
                    lambda u: u._record(
                        s.run_extension_tools,
                        {
                            key: value
                            for key, value in {**raw, "run_id": old["run_id"]}.items()
                            if key not in {"id", "created_at", "agent_id"}
                        },
                        agent,
                    )
                )
            assert failure.value.code == "data_constraint"

    asyncio.run(scenario())


def test_v19_captured_rows_and_grant_identity_are_immutable_and_never_deleted(tmp_path):
    async def scenario():
        async with opened(tmp_path / "instance", Clock()) as (_, database, objects, agent):
            slot, _, _ = await installed(database, objects, agent, tmp_path / "source")
            slot = await enable(database, agent, slot)
            await database.write(lambda u: action_fixture(u, agent, "pending"))
            for name in TABLES:
                table = s.metadata.tables[name]
                row = await database.read(
                    lambda u, table=table: dict(u.connection.execute(select(table)).mappings().first())
                )
                with pytest.raises(BusinessError) as failure:
                    await database.write(
                        lambda u, table=table, row=row: u.connection.execute(
                            delete(table).where(table.c.id == row["id"])
                        )
                    )
                assert failure.value.code == "data_constraint"
                values = {"created_at": "2000-01-01T00:00:00.000000+00:00"}
                if name == "extensions":
                    values.update(revision=row["revision"] + 1, authority_epoch=row["authority_epoch"] + 1)
                with pytest.raises(BusinessError) as failure:
                    await database.write(
                        lambda u, table=table, row=row, values=values: u.connection.execute(
                            update(table).where(table.c.id == row["id"]).values(**values)
                        )
                    )
                assert failure.value.code == "data_constraint"
            grant = await database.read(lambda u: dict(u.connection.execute(select(s.grants)).mappings().one()))
            await database.write(
                lambda u: u.connection.execute(
                    update(s.grants).where(s.grants.c.id == grant["id"]).values(revoked_at=u.clock.now())
                )
            )
            with pytest.raises(BusinessError) as failure:
                await database.write(
                    lambda u: u.connection.execute(
                        update(s.grants).where(s.grants.c.id == grant["id"]).values(revoked_at=None)
                    )
                )
            assert failure.value.code == "data_constraint"

    asyncio.run(scenario())


def test_restore_revokes_all_plugin_authority_in_one_transaction_and_preserves_captured_evidence(tmp_path):
    async def seed():
        async with opened(tmp_path / "instance", Clock()) as (paths, database, objects, agent):
            slot, object_row, package = await installed(database, objects, agent, tmp_path / "source")
            slot = await enable(database, agent, slot)
            actions = [
                await database.write(lambda u, state=state: action_fixture(u, agent, state))
                for state in ("pending", "approved", "executing")
            ]
            with sqlite_connection(paths.database) as connection:
                frozen = preserved_rows(connection, FROZEN + ("objects",))
                epoch = connection.execute("SELECT epoch FROM history_identity WHERE slot=1").fetchone()[0]
            return paths, slot, object_row, package, actions, frozen, epoch

    paths, slot, source, package, actions, frozen, old_epoch = asyncio.run(seed())
    destination, snapshot = tmp_path / "restored", tmp_path / "snapshot"
    backup(paths, snapshot)
    report = restore(snapshot, destination)
    assert report["extensions_quarantined"] is True and report["execution_quarantined"] is True
    counts = report["quarantine"]
    assert counts["disabled_extensions"] == 1 and counts["revoked_extension_grants"] == 1
    assert counts["cancelled_extension_actions"] == 2 and counts["cancelled_extension_approvals"] == 3
    restored_paths = InstancePaths.resolve(destination)
    assert counts["history_epoch"] != old_epoch
    with sqlite_connection(restored_paths.database) as connection:
        assert preserved_rows(connection, FROZEN + ("objects",)) == frozen
        assert connection.execute("SELECT enabled,state,revision,authority_epoch FROM extensions").fetchone() == (
            0,
            "disabled",
            slot["revision"] + 1,
            slot["authority_epoch"] + 1,
        )
        assert connection.execute("SELECT COUNT(*) FROM grants WHERE revoked_at IS NULL").fetchone()[0] == 0
        assert connection.execute("SELECT DISTINCT history_epoch FROM grants").fetchall() == [(old_epoch,)]
        for index, action in enumerate(actions):
            assert connection.execute(
                "SELECT state,effect FROM actions WHERE id=?", (action["action_id"],)
            ).fetchone() == (("unknown", "unknown") if index == 2 else ("cancelled", "none"))
            assert (
                connection.execute("SELECT state FROM approvals WHERE id=?", (action["approval_id"],)).fetchone()[0]
                == "cancelled"
            )
        assert (
            connection.execute("SELECT COUNT(*) FROM audit WHERE kind='extension.restored_disabled'").fetchone()[0] == 1
        )
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
        unchanged = preserved_rows(
            connection, ("extensions", "grants", "actions", "approvals", "audit", "run_extension_tools")
        )
    assert (
        restored_paths.objects / f"{source['agent_id']}/{source['sha256'][:2]}/{source['sha256']}.blob"
    ).read_bytes() == package.content
    assert restore(snapshot, destination)["already_restored"] is True
    with sqlite_connection(restored_paths.database) as connection:
        assert (
            preserved_rows(connection, ("extensions", "grants", "actions", "approvals", "audit", "run_extension_tools"))
            == unchanged
        )

    async def verify_revoked():
        async with opened(destination, Clock()) as (_, database, _, agent):
            for action in actions:
                assert (
                    await database.read(lambda u, action=action: u.extension_run_tools(agent, action["run_id"])) == []
                )

    asyncio.run(verify_revoked())


def test_restore_audit_failure_rolls_back_epoch_grants_actions_and_run_state_together(tmp_path):
    async def seed():
        async with opened(tmp_path / "instance", Clock()) as (paths, database, objects, agent):
            slot, _, _ = await installed(database, objects, agent, tmp_path / "source")
            await enable(database, agent, slot)
            for state in ("pending", "approved", "executing"):
                await database.write(lambda u, state=state: action_fixture(u, agent, state))
            await database.write(
                lambda u: u.connection.exec_driver_sql(
                    "CREATE TRIGGER reject_extension_restore_audit BEFORE INSERT ON audit "
                    "WHEN new.kind='extension.restored_disabled' "
                    "BEGIN SELECT RAISE(ABORT,'isolated restore audit failure'); END"
                )
            )
            with sqlite_connection(paths.database) as connection:
                before = preserved_rows(
                    connection, ("extensions", "grants", "actions", "approvals", "runs", "audit", "history_identity")
                )
            return paths, before

    paths, before = asyncio.run(seed())
    snapshot, destination = tmp_path / "snapshot", tmp_path / "restored"
    backup(paths, snapshot)
    with pytest.raises(sqlite3.IntegrityError, match="isolated restore audit failure"):
        restore(snapshot, destination)
    assert (destination / "restore-in-progress.json").is_file()
    assert not (destination / "restore-report.json").exists()
    with sqlite_connection(destination / "data/omni.db") as connection:
        assert preserved_rows(connection, tuple(before)) == before
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
