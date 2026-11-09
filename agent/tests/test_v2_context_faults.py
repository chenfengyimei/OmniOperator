"""Relational isolation and real process interruptions during summary publication and DDL."""

import asyncio
import subprocess
import sys

import pytest
from sqlalchemy import select, update
from test_v2_context import Model, prepare, seed
from test_v2_objects import Clock, opened

from omni.adapters.persistence import schema as s
from omni.domain.contracts import BusinessError, ModelReply


def test_context_and_summary_relations_reject_other_sessions_and_agents(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            current, _ = await db.write(lambda u: seed(u, agent, 24))
            prepared = await prepare(db, agent, current, Model())
            second = await db.write(lambda u: u.accept_turn(agent, None, "other-session", "different", {}))
            snapshot = await db.write(lambda u: u.prepare_context(agent, second["run_id"]))
            other_message = (await db.read(lambda u: u.messages(agent, second["session_id"])))[0]
            for table, values in [
                (
                    s.context_messages,
                    {"snapshot_id": prepared["snapshot_id"], "message_id": other_message["id"], "position": 99},
                ),
                (
                    s.context_summaries,
                    {"snapshot_id": snapshot["snapshot_id"], "summary_id": prepared["summary"]["id"]},
                ),
                (
                    s.summary_messages,
                    {
                        "summary_id": prepared["summary"]["id"],
                        "message_id": other_message["id"],
                        "session_id": current["session_id"],
                    },
                ),
            ]:
                with pytest.raises(BusinessError, match="constraint"):
                    await db.write(lambda u: u._record(table, values, agent))
            other = (await db.write(lambda u: u.create_agent("isolated")))["id"]
            with pytest.raises(BusinessError) as error:
                await db.read(lambda u: u.summary_get(other, prepared["summary"]["id"]))
            assert error.value.status == 404

    asyncio.run(scenario())


def test_source_fingerprint_change_invalidates_publication_and_budget_denial_is_atomic(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            current, messages = await db.write(lambda u: seed(u, agent, 24))
            await db.write(lambda u: u.prepare_context(agent, current["run_id"]))

            def begin(u):
                row = u.prepare_summary(agent, current["run_id"])
                call = u.reserve_call(agent, current["run_id"], 100, 100, "fake", purpose="summary")
                u.summary_call(agent, row["id"], call)
                return row, call

            row, call = await db.write(begin)
            await db.write(
                lambda u: u.connection.execute(
                    update(s.messages).where(s.messages.c.id == messages[0]["id"]).values(content="changed source")
                )
            )
            await db.write(lambda u: u.settle_call(agent, call, ModelReply("", cost_micro=7)))
            settled = await db.write(lambda u: u.settle_summary(agent, row["id"], "old summary"))
            assert settled["state"] == "invalidated" and not settled["content"]
            cfg = await db.read(lambda u: u.settings(agent))
            await db.write(lambda u: u.settings_put(agent, {**cfg["value"], "daily_budget_micro": 7}, cfg["revision"]))
            model = Model()
            await db.write(lambda u: u.transition(agent, current["run_id"], "failed", "test_completed"))
            fresh = await db.write(lambda u: u.accept_turn(agent, current["session_id"], "after-budget", "next", {}))
            result = await prepare(db, agent, fresh, model)
            assert result["reason"] == "budget_exceeded" and not model.requests
            assert len(await db.read(lambda u: u.query("summaries", agent))) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("after_commit", [False, True])
def test_process_exit_around_summary_publication_preserves_ledger_and_truth(tmp_path, after_commit):
    async def setup():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            current, _ = await db.write(lambda u: seed(u, agent, 24))
            await db.write(lambda u: u.prepare_context(agent, current["run_id"]))

            def begin(u):
                summary = u.prepare_summary(agent, current["run_id"])
                call = u.reserve_call(agent, current["run_id"], 100, 1000, "fake", purpose="summary")
                u.summary_call(agent, summary["id"], call)
                return summary["id"], call

            identity, call = await db.write(begin)
            return agent, identity, call

    agent, identity, call = asyncio.run(setup())
    code = """
import asyncio,os,sys
from pathlib import Path
from omni.adapters.persistence.database import Database
from omni.adapters.platform.paths import InstancePaths,InstanceLock
from omni.adapters.primitives import SystemClock,UUIDs
from omni.domain.contracts import ModelReply
async def main():
    paths=InstancePaths.resolve(Path(sys.argv[1])); lock=InstanceLock(paths); lock.acquire()
    db=Database(paths.database,SystemClock(),UUIDs(),objects_root=paths.objects)
    await db.write(lambda u:u.settle_call(sys.argv[2],sys.argv[4],ModelReply("",cost_micro=7)))
    def publish(u):
        u.settle_summary(sys.argv[2],sys.argv[3],"committed summary")
        if sys.argv[5]=='before': os._exit(91)
    await db.write(publish)
    os._exit(91)
asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path), agent, identity, call, "after" if after_commit else "before"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 91, result.stderr

    async def recover():
        async with opened(tmp_path, Clock()) as (_, db, _, _):
            await db.write(lambda u: u.recover())
            row = await db.read(lambda u: u.summary_get(agent, identity))
            assert row["state"] == ("ready" if after_commit else "unknown")
            assert row["content"] == ("committed summary" if after_commit else "")
            ledger = await db.read(lambda u: dict(u.connection.execute(select(s.budget_accounts)).mappings().one()))
            assert ledger["spent_micro"] == 7 and ledger["held_micro"] == 0
            with pytest.raises(BusinessError, match="already settled"):
                await db.write(lambda u: u.settle_call(agent, call, ModelReply("", cost_micro=7)))
            assert (await db.integrity())["foreign_keys"] == 0

    asyncio.run(recover())


def test_schema_rebuild_process_exit_restores_v3_then_preserves_legacy_context(tmp_path):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine
    from test_v2_people import legacy_database

    from omni.adapters.persistence.migrations.versions.v0003 import upgrade
    from omni.adapters.persistence.sqlite_access import sqlite_connection

    paths, ids = legacy_database(tmp_path)
    engine = create_engine("sqlite+pysqlite:///" + str(paths.database))
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            upgrade()
        connection.exec_driver_sql("UPDATE alembic_version SET version_num='v2_0003'")
    engine.dispose()
    code = """
import os,sys
from pathlib import Path
from alembic.ddl.impl import DefaultImpl
from omni.adapters.persistence.database import Database
from omni.adapters.platform.paths import InstancePaths,InstanceLock
from omni.adapters.primitives import SystemClock,UUIDs
original=DefaultImpl._exec
def cut(self,statement,*args,**kwargs):
    if 'CREATE TABLE context_summaries' in str(statement): os._exit(88)
    return original(self,statement,*args,**kwargs)
DefaultImpl._exec=cut
paths=InstancePaths.resolve(Path(sys.argv[1])); lock=InstanceLock(paths); lock.acquire()
Database(paths.database,SystemClock(),UUIDs(),objects_root=paths.objects)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)], capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 88, result.stderr
    with sqlite_connection(paths.database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "v2_0003"
        assert "message_ids" in {row[1] for row in connection.execute("PRAGMA table_info(context_snapshots)")}

    async def recover():
        async with opened(tmp_path, Clock()) as (_, db, _, _):
            report = await db.read(lambda u: u.run_context_report(ids["agent"], ids["run"]))
            assert report["snapshots"][0]["mode"] == "legacy"
            assert report["snapshots"][0]["message_ids"] == [ids["message"]]
            assert (await db.integrity())["foreign_keys"] == 0

    asyncio.run(recover())
    assert list(paths.database.parent.glob("schema-backups/*/manifest.json"))
