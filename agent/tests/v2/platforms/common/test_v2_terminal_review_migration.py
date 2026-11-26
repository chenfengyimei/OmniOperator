"""V26 upgrade preserves V25 receipts; backup restoration fences review proofs without native replay."""

import asyncio
import hashlib
import subprocess
import sys
import uuid

import pytest
from alembic import command
from omni_test_support.v2.objects import Clock, opened
from omni_test_support.v2.v2_configuration_helpers import add_model_headers_column
from omni_test_support.v2.v2_terminal_helpers import configure_workspace, open_request
from omni_test_support.v2.v2_terminal_review_helpers import request, unknown
from sqlalchemy import create_engine, inspect

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.database import Database
from omni.adapters.persistence.maintenance import backup, restore
from omni.adapters.persistence.migration_config import configuration
from omni.adapters.persistence.migration_config import head as migration_head
from omni.adapters.persistence.repository import Repositories
from omni.adapters.persistence.sqlite_access import sqlite_connection
from omni.adapters.platform.runtime.paths import InstanceLock, InstancePaths
from omni.adapters.primitives import UUIDs
from omni.domain.contracts import BusinessError


def test_v25_to_v26_preserves_terminal_rows_and_adds_scoped_immutable_review_schema(tmp_path):
    paths = InstancePaths.resolve(tmp_path / "home")
    paths.initialize()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    engine = create_engine("sqlite+pysqlite:///" + str(paths.database))
    with engine.begin() as connection:
        config = configuration()
        config.attributes["connection"] = connection
        command.upgrade(config, "v2_0025")
        add_model_headers_column(connection)
        u = Repositories(connection, Clock(), UUIDs(), str(uuid.uuid4()))
        u.initialize()
        agent = u.query("agents", "")[0]["id"]
        settings = configure_workspace(u, agent, workspace)
        opened_request = open_request()
        row = u.terminal_open_begin(
            agent,
            opened_request,
            {
                "executable": sys.executable,
                "cwd": str(workspace),
                "workspace_sha256": hashlib.sha256(str(workspace).encode()).hexdigest(),
            },
            settings["policy_revision"],
        )
        u.terminal_operation_settle(agent, row["id"], opened_request["operation_id"], "unknown", "fixture_interrupted")
        u.terminal_mark_closed(agent, row["id"], True, None)
    engine.dispose()
    with sqlite_connection(paths.database) as connection:
        before = {
            table: connection.execute("SELECT * FROM " + table + " ORDER BY rowid").fetchall()
            for table in ("terminal_sessions", "terminal_operations", "audit")
        }
    lock = InstanceLock(paths)
    lock.acquire()

    async def scenario():
        db = Database(paths.database, Clock(), UUIDs(), objects_root=paths.objects)
        try:
            assert db.schema_version == migration_head() and len(s.metadata.tables) == 157
            detail = await db.read(lambda u: u.terminal_review_detail(agent, opened_request["operation_id"]))
            assert detail["reviewable"] and detail["operation"]["state"] == "unknown"
            reflected = await db.read(lambda u: inspect(u.connection).get_foreign_keys("terminal_reviews"))
            assert any(fk["constrained_columns"] == ["operation_id", "session_id", "agent_id"] for fk in reflected)
            assert (await db.integrity())["foreign_keys"] == 0
        finally:
            await db.close()

    try:
        asyncio.run(scenario())
    finally:
        lock.close()
    with sqlite_connection(paths.database) as connection:
        assert {
            table: connection.execute("SELECT * FROM " + table + " ORDER BY rowid").fetchall() for table in before
        } == before
    assert len(list(paths.database.parent.glob("schema-backups/*"))) == 1


def test_verified_backup_restore_preserves_evidence_and_invalidates_old_review_authority(tmp_path):
    paths = None

    async def seed():
        nonlocal paths
        async with opened(tmp_path / "home", Clock()) as (paths, db, _objects, agent):
            operation = await unknown(db, agent, tmp_path / "workspace")
            detail = await db.read(lambda u: u.terminal_review_detail(agent, operation["id"]))
            review = await db.write(lambda u: u.terminal_review_commit(agent, operation["id"], request(detail)))
            stale = request(await db.read(lambda u: u.terminal_review_detail(agent, operation["id"])))
            return agent, operation, review, stale

    agent, operation, original, stale = asyncio.run(seed())
    assert backup(paths, tmp_path / "snapshot")["complete"]
    restored = restore(tmp_path / "snapshot", tmp_path / "restored")
    assert restored["execution_quarantined"] and restored["terminals_quarantined"]
    assert restored["database"]["integrity"] == "ok" and not restored["database"]["foreign_key_errors"]

    async def check():
        async with opened(tmp_path / "restored", Clock()) as (_, db, _objects, restored_agent):
            assert restored_agent == agent
            current = await db.read(lambda u: u.terminal_review_detail(agent, operation["id"]))
            assert current["history_epoch"] != stale["history_epoch"] and not current["latest_matches_source"]
            assert current["latest_review"]["evidence"] == original["evidence"]
            with pytest.raises(BusinessError) as changed:
                await db.write(lambda u: u.terminal_review_commit(agent, operation["id"], stale))
            assert changed.value.code == "terminal_review_changed"
            assert current["operation"]["state"] == "unknown" and not current["authorizes_replay"]

    asyncio.run(check())


def test_interrupted_v26_schema_transaction_rolls_back_before_retry(tmp_path):
    paths = InstancePaths.resolve(tmp_path / "home")
    paths.initialize()
    engine = create_engine("sqlite+pysqlite:///" + str(paths.database))
    with engine.begin() as connection:
        config = configuration()
        config.attributes["connection"] = connection
        command.upgrade(config, "v2_0025")
        add_model_headers_column(connection)
        Repositories(connection, Clock(), UUIDs()).initialize()
    engine.dispose()
    with sqlite_connection(paths.database) as connection:
        before = connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
    script = tmp_path / "interrupt_review_migration.py"
    script.write_text(
        """import os,sys
from pathlib import Path
from sqlalchemy import Engine,event
from omni.adapters.persistence.database import Database
from omni.adapters.platform.runtime.paths import InstanceLock,InstancePaths
from omni.adapters.primitives import SystemClock,UUIDs
paths=InstancePaths.resolve(Path(sys.argv[1]))
lock=InstanceLock(paths)
lock.acquire()
@event.listens_for(Engine,"after_cursor_execute")
def cut(connection,cursor,statement,parameters,context,executemany):
    if statement.strip().startswith("CREATE TRIGGER terminal_review_source"): os._exit(96)
Database(paths.database,SystemClock(),UUIDs(),objects_root=paths.objects)
raise AssertionError("Migration interruption point not reached")
""",
        encoding="utf-8",
    )
    process = subprocess.run(
        [sys.executable, str(script), str(paths.home)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert process.returncode == 96, process.stderr
    with sqlite_connection(paths.database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "v2_0025"
        assert (
            connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
            == before
        )
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    async def retry():
        db = Database(paths.database, Clock(), UUIDs(), objects_root=paths.objects)
        try:
            assert db.schema_version == migration_head()
            assert (await db.integrity())["foreign_keys"] == 0
        finally:
            await db.close()

    lock = InstanceLock(paths)
    lock.acquire()
    try:
        asyncio.run(retry())
    finally:
        lock.close()
