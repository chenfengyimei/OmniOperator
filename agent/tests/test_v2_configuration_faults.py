"""Real process interruption of configuration publication and the v5 DDL transaction."""

import asyncio
import json
import subprocess
import sys

import pytest
from sqlalchemy import select
from test_v2_objects import Clock, opened

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.sqlite_access import sqlite_connection


@pytest.mark.parametrize("after_commit", [False, True])
def test_process_exit_keeps_configuration_head_and_revision_atomic(tmp_path, after_commit):
    async def setup():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            identity = (await db.read(lambda u: u.settings(agent)))["value"]["model_profile_id"]
            return agent, identity

    agent, identity = asyncio.run(setup())
    code = """
import asyncio,os,sys
from pathlib import Path
from omni.adapters.persistence.database import Database
from omni.adapters.persistence.configuration_repository import ConfigurationRepositories
from omni.adapters.platform.runtime.paths import InstancePaths,InstanceLock
from omni.adapters.primitives import SystemClock,UUIDs
original=ConfigurationRepositories._configuration_record
def cut(self,*args):
    result=original(self,*args)
    if sys.argv[4]=='before': os._exit(93)
    return result
ConfigurationRepositories._configuration_record=cut
async def main():
    paths=InstancePaths.resolve(Path(sys.argv[1])); lock=InstanceLock(paths); lock.acquire()
    db=Database(paths.database,SystemClock(),UUIDs(),objects_root=paths.objects)
    def edit(u):
        row=u.configuration_get(sys.argv[2],sys.argv[3])
        u.configuration_put(sys.argv[2],sys.argv[3],'Published',True,{**row['value'],'model':'committed'},1)
    await db.write(edit)
    os._exit(93)
asyncio.run(main())
"""
    process = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path), agent, identity, "after" if after_commit else "before"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert process.returncode == 93, process.stderr

    async def recover():
        async with opened(tmp_path, Clock()) as (_, db, _, _):
            row = await db.read(lambda u: u.configuration_get(agent, identity))
            revisions = await db.read(lambda u: u.configuration_history(agent, identity))
            assert row["revision"] == len(revisions) == (2 if after_commit else 1)
            assert row["value"]["model"] == ("committed" if after_commit else "")
            assert len(await db.read(lambda u: list(u.connection.execute(select(s.model_options)).all()))) == len(
                revisions
            )
            assert (await db.integrity())["foreign_keys"] == 0

    asyncio.run(recover())


@pytest.mark.parametrize("resolved", [False, True])
def test_v5_ddl_process_exit_leaves_v4_retryable_and_snapshot_references_exact(tmp_path, resolved):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine
    from test_v2_people import legacy_database

    from omni.adapters.persistence.migrations.versions.v0003 import upgrade as people_upgrade
    from omni.adapters.persistence.migrations.versions.v0004 import upgrade as context_upgrade
    from omni.adapters.persistence.migrations.versions.v0005 import DEFAULT_PREFERENCES

    paths, ids = legacy_database(tmp_path)
    engine = create_engine("sqlite+pysqlite:///" + str(paths.database))
    value = {**DEFAULT_PREFERENCES, "model": "historical-model", "persona": "historical-persona"}
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            people_upgrade()
            context_upgrade()
        connection.exec_driver_sql("UPDATE alembic_version SET version_num='v2_0004'")
        connection.exec_driver_sql(
            "INSERT INTO settings(agent_id,revision,updated_at,value) VALUES(?,1,?,?)",
            (ids["agent"], Clock().now(), json.dumps(value)),
        )
        connection.exec_driver_sql("UPDATE runs SET config_snapshot=?", (json.dumps(value if resolved else {}),))
        if not resolved:
            connection.exec_driver_sql("UPDATE runs SET state='queued'")
            connection.exec_driver_sql(
                "INSERT INTO work_items(id,agent_id,created_at,run_id,kind,state,payload,attempt) VALUES(?,?,?,?,'turn','pending','{}',0)",
                (ids["snapshot"], ids["agent"], Clock().now(), ids["run"]),
            )
    engine.dispose()
    code = """
import os,sys
from pathlib import Path
from alembic.ddl.impl import DefaultImpl
from omni.adapters.persistence.database import Database
from omni.adapters.platform.runtime.paths import InstancePaths,InstanceLock
from omni.adapters.primitives import SystemClock,UUIDs
original=DefaultImpl._exec
def cut(self,statement,*args,**kwargs):
    if 'CREATE TABLE model_options' in str(statement): os._exit(94)
    return original(self,statement,*args,**kwargs)
DefaultImpl._exec=cut
paths=InstancePaths.resolve(Path(sys.argv[1])); lock=InstanceLock(paths); lock.acquire()
Database(paths.database,SystemClock(),UUIDs(),objects_root=paths.objects)
"""
    process = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)], capture_output=True, text=True, timeout=30, check=False
    )
    assert process.returncode == 94, process.stderr
    with sqlite_connection(paths.database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "v2_0004"
        assert "config_snapshot" in {r[1] for r in connection.execute("PRAGMA table_info(runs)")}
        assert json.loads(connection.execute("SELECT value FROM settings").fetchone()[0]) == value

    async def recover():
        async with opened(tmp_path, Clock()) as (_, db, _, _):
            if resolved:
                settings = await db.read(lambda u: u.run_settings(ids["agent"], ids["run"]))
                assert settings["model"] == "historical-model" and settings["persona"] == "historical-persona"
            else:
                run = await db.read(lambda u: u.get_run(ids["agent"], ids["run"]))
                assert run["state"] == "interrupted" and run["error_code"] == "legacy_configuration_unresolved"
                assert (await db.read(lambda u: u.query("work", ids["agent"])))[0]["state"] == "unknown"
            assert (await db.integrity())["foreign_keys"] == 0

    asyncio.run(recover())
    assert list(paths.database.parent.glob("schema-backups/*/manifest.json"))
