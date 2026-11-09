"""Real v14 migration crashes roll back DDL and seeds while preserving source history and restart identity."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from contextlib import contextmanager
from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect
from v2_legacy_seed import LegacySeedRepositories, preserved_rows

from omni.adapters.persistence import repository, repository_base
from omni.adapters.persistence import schema as s
from omni.adapters.persistence.database import Database
from omni.adapters.persistence.maintenance import verify_snapshot
from omni.adapters.persistence.migration_config import configuration
from omni.adapters.persistence.migration_config import head as migration_head
from omni.adapters.persistence.sqlite_access import sqlite_connection
from omni.adapters.platform.paths import InstanceLock, InstancePaths
from omni.adapters.primitives import SystemClock, UUIDs

NEW_TABLES = {"history_identity", "history_sequences", "history_scopes"}
NEW_TRIGGERS = {
    "history_new_agent",
    "history_new_instance",
    "history_sequence_monotonic",
    "history_sequence_keep",
    "history_scope_monotonic",
    "history_scope_keep",
    "history_identity_keep",
    "history_trim_audit",
    "history_trim_events",
    "history_trim_instance_audit",
}
SOURCE_TABLES = ("instances", "agents", "runs", "messages", "run_events", "audit", "instance_audit")


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


def legacy_baseline(paths, *, empty=False):
    paths.initialize()
    with owned(paths):
        engine = create_engine("sqlite+pysqlite:///" + str(paths.database))
        ids, clock = UUIDs(), SystemClock()
        try:
            with engine.begin() as connection, pytest.MonkeyPatch.context() as legacy_history:
                cfg = configuration()
                cfg.attributes["connection"] = connection
                command.upgrade(cfg, "v2_0013")
                # Use the pre-v14 SQLite allocation only while constructing the old database.
                legacy_history.setattr(repository_base, "allocate_history", lambda *args, **kwargs: None)
                legacy_history.setattr(repository, "allocate_history", lambda *args, **kwargs: None)
                if not empty:
                    unit = LegacySeedRepositories(connection, clock, ids)
                    instance = unit.initialize()
                    first = unit.query("agents", "")[0]["id"]
                    second = unit.create_agent("Second legacy Agent")["id"]
                    for agent in (first, second):
                        unit.accept_turn(agent, None, "legacy-request", "Unchanged legacy message", {})
                    connection.exec_driver_sql(
                        "UPDATE audit SET sequence=700 WHERE sequence=(SELECT MAX(sequence) FROM audit)"
                    )
                    connection.exec_driver_sql(
                        "UPDATE run_events SET sequence=900 WHERE sequence=(SELECT MAX(sequence) FROM run_events)"
                    )
                    for sequence in (500, 1200):
                        connection.exec_driver_sql(
                            "INSERT INTO instance_audit(sequence,id,created_at,owner_id,operation_id,kind,outcome,data) "
                            "VALUES(?,?,?,?,?,'credential.registered','registered','{}')",
                            (sequence, ids.new(), clock.now(), instance["owner_id"], ids.new()),
                        )
                    connection.exec_driver_sql("DELETE FROM instance_audit WHERE sequence=1200")
            with sqlite_connection(paths.database) as connection:
                assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                assert not connection.execute("PRAGMA foreign_key_check").fetchall()
                assert not NEW_TABLES & {
                    row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                return source_rows(connection), connection.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
                ).fetchall()
        finally:
            engine.dispose()


async def history_state(database):
    def read(unit):
        connection = unit.connection
        return {
            "epoch": connection.exec_driver_sql("SELECT epoch FROM history_identity WHERE slot=1").scalar_one(),
            "sequences": dict(connection.exec_driver_sql("SELECT kind,last_issued FROM history_sequences").all()),
            "scopes": [
                tuple(row)
                for row in connection.exec_driver_sql(
                    "SELECT kind,scope_id,agent_id,instance_id,trim_revision,deleted_count,last_trim_at "
                    "FROM history_scopes ORDER BY kind,scope_id"
                )
            ],
        }

    return await database.read(read)


@pytest.mark.parametrize(
    "marker",
    [
        "CREATE TABLE history_sequences",
        "INSERT INTO history_sequences SELECT 'audit'",
        "INSERT INTO history_identity(slot,epoch)",
    ],
    ids=["ddl", "counter-seeds", "identity"],
)
def test_v14_process_exit_rolls_back_all_ddl_and_counter_seeds_then_retries(tmp_path, marker):
    paths = InstancePaths.resolve(tmp_path / "instance")
    original, original_schema = legacy_baseline(paths)
    script = tmp_path / "interrupt_v14.py"
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
    if statement.strip().startswith(sys.argv[2]):
        os._exit(94)
Database(paths.database,SystemClock(),UUIDs(),objects_root=paths.objects)
raise AssertionError('Requested v14 interruption did not occur')
""",
        encoding="utf-8",
    )
    process = subprocess.run(
        [sys.executable, str(script), str(paths.home), marker],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert process.returncode == 94, process.stderr
    with owned(paths):
        with sqlite_connection(paths.database) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "v2_0013"
            assert (
                connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
                == original_schema
            )
            assert source_rows(connection) == original
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert not connection.execute("PRAGMA foreign_key_check").fetchall()

        async def upgrade():
            database = Database(paths.database, SystemClock(), UUIDs(), objects_root=paths.objects)
            try:
                assert database.schema_version == migration_head()
                state = await history_state(database)
                assert state["sequences"] == {"events": 900, "audit": 700}
                assert str(UUID(state["epoch"])) == state["epoch"]
                assert len(state["scopes"]) == 5
                assert all(row[4:] == (0, 0, None) for row in state["scopes"])
                assert (await database.integrity())["foreign_keys"] == 0
            finally:
                await database.close()

        asyncio.run(upgrade())
        with sqlite_connection(paths.database) as connection:
            assert source_rows(connection) == original

    snapshots = list(paths.database.parent.glob("schema-backups/*"))
    assert len(snapshots) == 2
    for snapshot in snapshots:
        manifest = verify_snapshot(snapshot)
        assert manifest["complete"] and manifest["database"]["schema"] == "v2_0013"
        assert manifest["database"]["integrity"] == "ok" and not manifest["database"]["foreign_key_errors"]


def test_v14_seeds_highest_global_positions_and_preserves_epoch_and_cursors_on_restart(tmp_path):
    paths = InstancePaths.resolve(tmp_path / "instance")
    original, _ = legacy_baseline(paths)
    with owned(paths):

        async def scenario():
            database = Database(paths.database, SystemClock(), UUIDs(), objects_root=paths.objects)
            try:
                initial = await history_state(database)
                assert initial["sequences"] == {"audit": 700, "events": 900}

                def inspect_schema(unit):
                    reflection = inspect(unit.connection)
                    for name in NEW_TABLES:
                        table = s.metadata.tables[name]
                        columns = reflection.get_columns(name)
                        assert {column["name"] for column in columns} == set(table.c.keys())
                        assert {column["name"]: column["nullable"] for column in columns} == {
                            column.name: column.nullable for column in table.c
                        }
                        assert {
                            (
                                tuple(item["constrained_columns"]),
                                item["referred_table"],
                                tuple(item["referred_columns"]),
                            )
                            for item in reflection.get_foreign_keys(name)
                        } == {
                            (
                                tuple(constraint.column_keys),
                                constraint.referred_table.name,
                                tuple(element.column.name for element in constraint.elements),
                            )
                            for constraint in table.foreign_key_constraints
                        }
                    assert NEW_TRIGGERS <= {
                        row[0]
                        for row in unit.connection.exec_driver_sql(
                            "SELECT name FROM sqlite_master WHERE type='trigger'"
                        )
                    }
                    agents = [row[0] for row in unit.connection.exec_driver_sql("SELECT id FROM agents ORDER BY id")]
                    instance = unit.connection.exec_driver_sql("SELECT id FROM instances").scalar_one()
                    assert {(row[0], row[1], row[2], row[3]) for row in initial["scopes"]} == {
                        ("instance_audit", instance, None, instance),
                        *((kind, agent, agent, None) for kind in ("events", "audit") for agent in agents),
                    }
                    run = unit.connection.exec_driver_sql("SELECT id,agent_id FROM runs ORDER BY id LIMIT 1").one()
                    return tuple(run)

                run_id, agent = await database.read(inspect_schema)
                page = await database.read(lambda u: u.history_page("events", agent))
                assert len(page["items"]) == 1 and page["stream"]["high_water"] == 900
                instance_page = await database.read(lambda u: u.history_page("instance_audit"))
                assert len(instance_page["items"]) == 1 and instance_page["stream"]["high_water"] == 1200
            finally:
                await database.close()

            with sqlite_connection(paths.database) as connection:
                assert source_rows(connection) == original
            database = Database(paths.database, SystemClock(), UUIDs(), objects_root=paths.objects)
            try:
                assert await history_state(database) == initial
                repeated = await database.read(lambda u: u.history_page("events", agent, cursor=page["next_cursor"]))
                assert not repeated["items"] and repeated["next_cursor"] == page["next_cursor"]

                def append(unit):
                    unit.event(agent, run_id, "history.after_restart", {"schema": 1})
                    audit_id = unit.audit(agent, "history.after_restart", run_id, {"schema": 1})
                    assert (
                        unit.connection.exec_driver_sql(
                            "SELECT sequence FROM audit WHERE id=?", (audit_id,)
                        ).scalar_one()
                        == 701
                    )
                    unit.create_agent("Created after v14 upgrade")

                await database.write(append)
                advanced = await history_state(database)
                assert advanced["epoch"] == initial["epoch"]
                assert advanced["sequences"]["events"] == 901
                assert advanced["sequences"]["audit"] > 700
                assert len(advanced["scopes"]) == 7
                continuation = await database.read(
                    lambda u: u.history_page("events", agent, cursor=page["next_cursor"])
                )
                assert [row["sequence"] for row in continuation["items"]] == [901]
                assert continuation["items"][0]["kind"] == "history.after_restart"
                report = await database.integrity()
                assert report["integrity"] == "ok" and report["foreign_keys"] == 0
            finally:
                await database.close()

            database = Database(paths.database, SystemClock(), UUIDs(), objects_root=paths.objects)
            try:
                assert await history_state(database) == advanced
                assert (await database.read(lambda u: u.history_page("events", agent)))["items"][-1]["sequence"] == 901
            finally:
                await database.close()

        asyncio.run(scenario())


def test_v14_empty_database_seeds_zero_and_registers_new_instance_and_agent_scopes(tmp_path):
    paths = InstancePaths.resolve(tmp_path / "instance")
    legacy_baseline(paths, empty=True)
    with owned(paths):

        async def scenario():
            database = Database(paths.database, SystemClock(), UUIDs(), objects_root=paths.objects)
            try:
                empty = await history_state(database)
                assert empty["sequences"] == {"events": 0, "audit": 0} and empty["scopes"] == []
                assert str(UUID(empty["epoch"])) == empty["epoch"]
                await database.write(lambda u: u.initialize())
                populated = await history_state(database)
                assert populated["epoch"] == empty["epoch"]
                assert populated["sequences"]["events"] == 0 and populated["sequences"]["audit"] > 0
                assert len(populated["scopes"]) == 3
                assert all(row[4:] == (0, 0, None) for row in populated["scopes"])
                report = await database.integrity()
                assert report["integrity"] == "ok" and report["foreign_keys"] == 0
            finally:
                await database.close()

        asyncio.run(scenario())
