"""V21 frozen terminal DDL, verified migration recovery and metadata-only restore quarantine."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from itertools import chain

import pytest
from alembic import command
from sqlalchemy import CheckConstraint, UniqueConstraint, create_engine, inspect
from test_v2_audit_plan_migration import owned
from test_v2_event_plan_migration import normalized_check
from test_v2_objects import Clock, opened
from test_v2_terminals_storage import begin_terminal
from v2_legacy_seed import LegacySeedRepositories

from omni.adapters.persistence import maintenance
from omni.adapters.persistence import schema as s
from omni.adapters.persistence.database import Database
from omni.adapters.persistence.maintenance import backup, inspect_database, restore, verify_snapshot
from omni.adapters.persistence.migration_config import configuration
from omni.adapters.persistence.migration_config import head as migration_head
from omni.adapters.persistence.sqlite_access import sqlite_connection
from omni.adapters.platform.paths import InstancePaths
from omni.adapters.primitives import SystemClock, UUIDs
from omni.domain.contracts import BusinessError

TABLES = {"terminal_sessions", "terminal_operations"}
SOURCE_TABLES = (
    "principals",
    "instances",
    "agents",
    "sessions",
    "messages",
    "runs",
    "run_events",
    "audit",
    "memory_entries",
    "memory_revisions",
    "history_identity",
    "history_sequences",
    "history_scopes",
)


def rows(connection, tables=SOURCE_TABLES):
    from v2_legacy_seed import preserved_rows

    return preserved_rows(connection, tables, excluded_run_columns=("execution_kind",))


def baseline(paths):
    paths.initialize()
    with owned(paths):
        engine = create_engine("sqlite+pysqlite:///" + str(paths.database))
        try:
            with engine.begin() as connection:
                cfg = configuration()
                cfg.attributes["connection"] = connection
                command.upgrade(cfg, "v2_0020")
                unit = LegacySeedRepositories(connection, SystemClock(), UUIDs())
                unit.initialize()
                agent = unit.query("agents", "")[0]["id"]
                unit.accept_turn(agent, None, "v20-terminal-migration", "Preserved original message", {})
                unit.remember(agent, "Preserved independent fact")
            with sqlite_connection(paths.database) as connection:
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "v2_0020"
                assert not TABLES & {
                    r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                assert not connection.execute("PRAGMA foreign_key_check").fetchall()
                return rows(connection), connection.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
                ).fetchall()
        finally:
            engine.dispose()


def assert_schema(unit):
    reflection = inspect(unit.connection)
    assert len(s.metadata.tables) == 136 and set(s.metadata.tables) <= set(reflection.get_table_names())
    for name in TABLES:
        table = s.metadata.tables[name]
        columns = reflection.get_columns(name)
        assert {c["name"]: c["nullable"] for c in columns} == {c.name: c.nullable for c in table.c}
        assert {c["name"]: str(c["type"]) for c in columns} == {c.name: str(c.type) for c in table.c}
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
    assert (
        "AUTOINCREMENT"
        in unit.connection.exec_driver_sql("SELECT sql FROM sqlite_master WHERE name='terminal_sessions'").scalar_one()
    )
    triggers = {r[0] for r in unit.connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='trigger'")}
    assert {
        "terminal_session_identity",
        "terminal_session_transitions",
        "terminal_receipt_identity",
        "terminal_session_keep",
        "terminal_receipt_keep",
    } <= triggers
    assert (s.terminal_operations, s.terminal_operations.c.intent_audit_id) in s.AUDIT_REFERENCES
    assert not any(
        fk.target_fullname.startswith(("runs.", "actions.", "approvals."))
        for table in (s.terminal_sessions, s.terminal_operations)
        for fk in table.foreign_keys
    )


def test_fresh_v21_declares_93_tables_with_exact_terminal_columns_constraints_and_audit_references(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, database, _, _):
            assert database.schema_version == migration_head()
            await database.read(assert_schema)
            assert (await database.integrity())["foreign_keys"] == 0

    asyncio.run(scenario())


def test_v21_process_exit_rolls_back_ddl_and_retries_from_verified_v20_backups(tmp_path):
    paths = InstancePaths.resolve(tmp_path / "instance")
    original, schema = baseline(paths)
    script = tmp_path / "interrupt_terminal_migration.py"
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
    if statement.strip().startswith('CREATE TRIGGER terminal_session_transitions'): os._exit(93)
Database(paths.database,SystemClock(),UUIDs(),objects_root=paths.objects)
raise AssertionError('Requested terminal migration interruption did not occur')
""",
        encoding="utf-8",
    )
    child = subprocess.run(
        [sys.executable, str(script), str(paths.home)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert child.returncode == 93, child.stderr
    with owned(paths):
        with sqlite_connection(paths.database) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "v2_0020"
            assert rows(connection) == original
            assert (
                connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
                == schema
            )
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

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
            assert rows(connection) == original
    snapshots = list(paths.database.parent.glob("schema-backups/*"))
    assert len(snapshots) == 2
    for snapshot in snapshots:
        verified = verify_snapshot(snapshot)
        assert verified["complete"] and verified["database"]["schema"] == "v2_0020"
        assert verified["database"]["integrity"] == "ok" and not verified["database"]["foreign_key_errors"]


def test_v21_refuses_to_migrate_when_verified_backup_cannot_be_created(tmp_path, monkeypatch):
    paths = InstancePaths.resolve(tmp_path / "instance")
    original, schema = baseline(paths)

    def failed(*_args, **_kwargs):
        raise BusinessError("isolated_backup_failed", "Isolated snapshot failure", status=503)

    monkeypatch.setattr(maintenance, "migration_snapshot", failed)
    with owned(paths):
        with pytest.raises(BusinessError) as failure:
            Database(paths.database, SystemClock(), UUIDs(), objects_root=paths.objects)
        assert failure.value.code == "isolated_backup_failed"
        with sqlite_connection(paths.database) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "v2_0020"
            assert rows(connection) == original
            assert (
                connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
                == schema
            )
            assert not connection.execute("PRAGMA foreign_key_check").fetchall()


def test_restore_reports_terminal_quarantine_preserves_evidence_and_never_claims_native_cleanup(tmp_path):
    async def seed():
        async with opened(tmp_path / "instance", Clock()) as (paths, database, _, agent):
            starting, request, _ = await begin_terminal(database, agent, tmp_path / "workspace", running=False)
            running, _, _ = await begin_terminal(database, agent, tmp_path / "workspace")
            closing, _, _ = await begin_terminal(database, agent, tmp_path / "workspace")
            close_id = UUIDs().new()
            await database.write(
                lambda u: u.terminal_operation_begin(
                    agent, closing["id"], close_id, "close", "a" * 64, 0, closing["revision"]
                )
            )
            finished, _, _ = await begin_terminal(database, agent, tmp_path / "workspace")
            exited = await database.write(lambda u: u.terminal_mark_closed(agent, finished["id"], True, 3))
            with sqlite_connection(paths.database) as connection:
                frozen = rows(connection, ("terminal_sessions", "terminal_operations"))
            return paths, agent, starting, running, closing, exited, request, close_id, frozen

    paths, agent, starting, running, closing, exited, request, close_id, frozen = asyncio.run(seed())
    before = inspect_database(paths.database)
    assert before["active_terminals"] == 3 and before["terminal_attention"] == 2
    assert before["unknown_terminal_operations"] == 2
    snapshot, destination = tmp_path / "snapshot", tmp_path / "restored"
    backup(paths, snapshot)
    report = restore(snapshot, destination)
    assert report["terminals_quarantined"] is True and report["quarantine"]["unknown_terminals"] == 3
    assert report["database"]["active_terminals"] == 0 and report["database"]["terminal_attention"] == 3
    assert report["database"]["unknown_terminal_operations"] == 2
    database_path = destination / "data/omni.db"
    with sqlite_connection(database_path) as connection:
        connection.row_factory = __import__("sqlite3").Row
        current = {r["id"]: dict(r) for r in connection.execute("SELECT * FROM terminal_sessions")}
        for previous in (starting, running, closing):
            now = current[previous["id"]]
            assert now["state"] == "unknown" and now["cleanup_pending"] == 1 and now["ended_at"] is None
            assert (now["pid"], now["process_created"], now["boot_id"]) == (
                previous["pid"],
                previous["process_created"],
                previous["boot_id"],
            )
        assert current[exited["id"]] == exited
        ops = {r["id"]: dict(r) for r in connection.execute("SELECT * FROM terminal_operations")}
        assert ops[request["operation_id"]]["state"] == ops[close_id]["state"] == "unknown"
        assert all(r["state"] != "prepared" for r in ops.values())
        connection.row_factory = None
        after = rows(connection, ("terminal_sessions", "terminal_operations", "audit"))
    assert restore(snapshot, destination)["already_restored"] is True
    with sqlite_connection(database_path) as connection:
        assert rows(connection, tuple(after)) == after
    assert frozen["terminal_sessions"] != after["terminal_sessions"]

    async def fenced():
        async with opened(destination, Clock()) as (_, database, _, _):
            with pytest.raises(BusinessError) as error:
                await database.read(lambda u: u.terminal_assert_control(agent, running["id"]))
            assert error.value.code == "terminal_unavailable"
            replay = await database.read(lambda u: u.terminal_open_replay(agent, request))
            assert replay["replayed"] and replay["operation"]["state"] == "unknown"

    asyncio.run(fenced())
