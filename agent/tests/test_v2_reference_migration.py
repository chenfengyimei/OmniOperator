"""Real process exits during v13 DDL preserve the v12 instance and its verified upgrade snapshots."""

import asyncio
import subprocess
import sys
from contextlib import contextmanager

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect
from v2_legacy_seed import LegacySeedRepositories

from omni.adapters.persistence import repository_base
from omni.adapters.persistence import schema as s
from omni.adapters.persistence.database import Database
from omni.adapters.persistence.maintenance import verify_snapshot
from omni.adapters.persistence.migration_config import configuration
from omni.adapters.persistence.migration_config import head as migration_head
from omni.adapters.persistence.sqlite_access import sqlite_connection
from omni.adapters.platform.paths import InstanceLock, InstancePaths
from omni.adapters.primitives import SystemClock, UUIDs

NEW_TABLES = {"reference_release_batches", "reference_release_targets"}
NEW_INDEXES = {"ix_reference_release_targets_agent_id", "ix_reference_release_pending"}
NEW_TRIGGERS = {
    "reference_release_batch_identity",
    "reference_release_target_identity",
    "reference_release_batch_keep",
    "reference_release_target_keep",
    "pruned_action_no_new_object",
    "pruned_action_no_changed_object",
}
NEW_OBJECTS = NEW_TABLES | NEW_INDEXES | NEW_TRIGGERS


@contextmanager
def owned(paths):
    lock = InstanceLock(paths)
    lock.acquire()
    try:
        yield
    finally:
        lock.close()


def baseline(paths):
    paths.initialize()
    with owned(paths):
        engine = create_engine("sqlite+pysqlite:///" + str(paths.database))
        try:
            cfg = configuration()
            with engine.begin() as connection, pytest.MonkeyPatch.context() as legacy_history:
                # Pre-v14 audit rows used SQLite's INTEGER PRIMARY KEY allocation.
                legacy_history.setattr(repository_base, "allocate_history", lambda *args, **kwargs: None)
                cfg.attributes["connection"] = connection
                command.upgrade(cfg, "v2_0012")
                unit = LegacySeedRepositories(connection, SystemClock(), UUIDs())
                unit.initialize()
                agents = [
                    tuple(row)
                    for row in connection.exec_driver_sql("SELECT id,name,enabled,revision FROM agents ORDER BY id")
                ]
            with sqlite_connection(paths.database) as connection:
                assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                assert not connection.execute("PRAGMA foreign_key_check").fetchall()
            return agents
        finally:
            engine.dispose()


@pytest.mark.parametrize(
    "marker",
    [
        "CREATE TABLE reference_release_targets",
        "CREATE UNIQUE INDEX ix_reference_release_pending",
        "CREATE TRIGGER pruned_action_no_changed_object",
    ],
    ids=["table", "index", "trigger"],
)
def test_v13_process_exit_rolls_back_all_ddl_then_retries_from_verified_v12(tmp_path, marker):
    paths = InstancePaths.resolve(tmp_path / "instance")
    agents = baseline(paths)
    code = """
import os,sys
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
        os._exit(93)
Database(paths.database,SystemClock(),UUIDs(),objects_root=paths.objects)
raise AssertionError('Requested migration interruption did not occur')
"""
    process = subprocess.run(
        [sys.executable, "-c", code, str(paths.home), marker],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert process.returncode == 93, process.stderr
    placeholders = ",".join("?" for _ in NEW_OBJECTS)
    with owned(paths):
        with sqlite_connection(paths.database) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "v2_0012"
            assert not connection.execute(
                f"SELECT name FROM sqlite_master WHERE name IN ({placeholders})", sorted(NEW_OBJECTS)
            ).fetchall()
            assert connection.execute("SELECT id,name,enabled,revision FROM agents ORDER BY id").fetchall() == agents
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert not connection.execute("PRAGMA foreign_key_check").fetchall()

        async def upgrade():
            database = Database(paths.database, SystemClock(), UUIDs(), objects_root=paths.objects)
            try:
                assert database.schema_version == migration_head()
                report = await database.integrity()
                assert report["integrity"] == "ok" and report["foreign_keys"] == 0

                def compare(unit):
                    reflection = inspect(unit.connection)
                    for name in NEW_TABLES:
                        table = s.metadata.tables[name]
                        actual_columns = reflection.get_columns(name)
                        assert {column["name"] for column in actual_columns} == set(table.c.keys())
                        assert {column["name"]: column["nullable"] for column in actual_columns} == {
                            column.name: column.nullable for column in table.c
                        }
                        declared = {
                            (
                                tuple(constraint.column_keys),
                                constraint.referred_table.name,
                                tuple(element.column.name for element in constraint.elements),
                            )
                            for constraint in table.foreign_key_constraints
                        }
                        actual = {
                            (
                                tuple(constraint["constrained_columns"]),
                                constraint["referred_table"],
                                tuple(constraint["referred_columns"]),
                            )
                            for constraint in reflection.get_foreign_keys(name)
                        }
                        assert actual == declared
                    assert {"object_sha256", "object_bytes"} <= {
                        column["name"] for column in reflection.get_columns("reference_release_targets")
                    }
                    assert not {
                        item["referred_table"] for item in reflection.get_foreign_keys("reference_release_targets")
                    } & {"objects", "action_objects"}
                    index = next(
                        item
                        for item in reflection.get_indexes("reference_release_targets")
                        if item["name"] == "ix_reference_release_pending"
                    )
                    assert index["unique"] == 1 and index["column_names"] == ["reference_id"]
                    assert str(index["dialect_options"]["sqlite_where"]).replace(" ", "") == "state='pending'"
                    names = {
                        row[0]
                        for row in unit.connection.exec_driver_sql(
                            f"SELECT name FROM sqlite_master WHERE name IN ({placeholders})", tuple(sorted(NEW_OBJECTS))
                        )
                    }
                    assert names == NEW_OBJECTS
                    assert [
                        tuple(row)
                        for row in unit.connection.exec_driver_sql(
                            "SELECT id,name,enabled,revision FROM agents ORDER BY id"
                        )
                    ] == agents

                await database.read(compare)
            finally:
                await database.close()

        asyncio.run(upgrade())

    snapshots = list(paths.database.parent.glob("schema-backups/*"))
    assert len(snapshots) == 2
    for source in snapshots:
        manifest = verify_snapshot(source)
        assert manifest["complete"] and manifest["database"]["schema"] == "v2_0012"
        assert manifest["database"]["integrity"] == "ok" and not manifest["database"]["foreign_key_errors"]
