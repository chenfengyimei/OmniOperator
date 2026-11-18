"""Real process exits around document publication, index replacement, erasure and DDL."""

import asyncio
import subprocess
import sys

import pytest
from omni_test_support.v2.documents import LocalVectors
from omni_test_support.v2.objects import Clock, opened

from omni.adapters.persistence.sqlite_access import sqlite_connection
from omni.application.documents import DocumentService
from omni.domain.contracts import BusinessError


@pytest.mark.parametrize("operation", ["save", "rebuild", "erase"])
@pytest.mark.parametrize("after_commit", [False, True])
def test_process_exit_preserves_document_truth_and_recoverable_derived_work(tmp_path, operation, after_commit):
    async def setup():
        async with opened(tmp_path, Clock()) as (paths, db, objects, agent):
            service = DocumentService(db, objects, LocalVectors())
            source = await service.save(agent, "Source", "original journey material")
            await service.rebuild(agent, source["id"], "vector")
            fact = await db.write(
                lambda u: u.remember(
                    agent, "derived fact", document_revision_id=source["revision_id"], quote="journey material"
                )
            )
            await service.close()
            return paths, agent, source, fact

    _paths, agent, source, fact = asyncio.run(setup())
    code = """
import asyncio,os,sys
from pathlib import Path
from omni.adapters.persistence.database import Database
from omni.adapters.persistence.document_repository import DocumentRepositories
from omni.adapters.persistence.memory_repository import MemoryRepositories
from omni.adapters.object_store import ObjectFiles
from omni.adapters.platform.runtime.paths import InstancePaths,InstanceLock
from omni.adapters.primitives import SystemClock,UUIDs
from omni.application.objects import ObjectService
from omni.application.documents import DocumentService
original=MemoryRepositories._forget_one if sys.argv[4]=='erase' else DocumentRepositories._document_chunks
def cut(self,*args,**kwargs):
    result=original(self,*args,**kwargs)
    if sys.argv[5]=='before': os._exit(94)
    return result
if sys.argv[4]=='erase': MemoryRepositories._forget_one=cut
else: DocumentRepositories._document_chunks=cut
async def main():
    paths=InstancePaths.resolve(Path(sys.argv[1])); lock=InstanceLock(paths); lock.acquire()
    clock=SystemClock(); db=Database(paths.database,clock,UUIDs(),objects_root=paths.objects)
    objects=ObjectService(db,ObjectFiles(paths.objects),clock); service=DocumentService(db,objects)
    if sys.argv[4]=='save': await service.save(sys.argv[2],'Changed','replacement source',sys.argv[3],1)
    elif sys.argv[4]=='rebuild': await service.rebuild(sys.argv[2],sys.argv[3],'full_text')
    else: await service.erase(sys.argv[2],sys.argv[3],1)
    os._exit(94)
asyncio.run(main())
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            str(tmp_path),
            agent,
            source["id"],
            operation,
            "after" if after_commit else "before",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 94, result.stderr

    async def recover():
        async with opened(tmp_path, Clock()) as (_, db, objects, _):
            await db.write(lambda u: u.recover())
            service = DocumentService(db, objects)
            if operation == "erase" and after_commit:
                with pytest.raises(BusinessError) as error:
                    await service.get(agent, source["id"])
                assert error.value.status == 410
                assert not (await db.read(lambda u: u.memory_history(agent, fact["id"])))[0]["content"]
            else:
                row = await service.get(agent, source["id"])
                assert row["content"] == (
                    "replacement source" if operation == "save" and after_commit else "original journey material"
                )
                if operation == "rebuild":
                    assert bool(row["active_vector_build_id"]) == (not after_commit)
                    assert (await service.search(agent, "journey"))["items"]
            if operation == "erase":
                job = await db.read(lambda u: u.document_erasure_status(agent, source["id"]))
                assert job["state"] == ("done" if after_commit else "pending")
                await service.erase(agent, source["id"], 1)
                assert (await objects.describe(agent, source["object_id"]))["references"] == 0
            assert (await db.integrity())["foreign_keys"] == 0
            assert await service.close()

    asyncio.run(recover())


def test_v6_source_anchor_rebuild_exit_keeps_v5_and_verified_backup(tmp_path):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from omni_test_support.v2.people import legacy_database
    from sqlalchemy import create_engine

    from omni.adapters.persistence.migrations.versions.v0003 import upgrade as v3
    from omni.adapters.persistence.migrations.versions.v0004 import upgrade as v4
    from omni.adapters.persistence.migrations.versions.v0005 import upgrade as v5

    paths, ids = legacy_database(tmp_path)
    engine = create_engine("sqlite+pysqlite:///" + str(paths.database))
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            v3()
            v4()
            v5()
        connection.exec_driver_sql("UPDATE alembic_version SET version_num='v2_0005'")
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
    if 'CREATE TABLE source_anchors' in str(statement): os._exit(95)
    return original(self,statement,*args,**kwargs)
DefaultImpl._exec=cut
paths=InstancePaths.resolve(Path(sys.argv[1])); lock=InstanceLock(paths); lock.acquire()
Database(paths.database,SystemClock(),UUIDs(),objects_root=paths.objects)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)], capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 95, result.stderr
    with sqlite_connection(paths.database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "v2_0005"
        assert "document_revision_id" not in {r[1] for r in connection.execute("PRAGMA table_info(source_anchors)")}
        assert connection.execute("SELECT quote FROM source_anchors").fetchone()[0] == "historical quote"

    async def recover():
        async with opened(tmp_path, Clock()) as (_, db, _, _):
            assert (await db.read(lambda u: u.memory_history(ids["agent"], ids["memory"])))[0]["sources"][0][
                "quote"
            ] == "historical quote"
            assert (await db.integrity())["foreign_keys"] == 0

    asyncio.run(recover())
    assert list(paths.database.parent.glob("schema-backups/*/manifest.json"))


def test_offline_fts_rebuild_repairs_posting_damage_without_restoring_erased_sources(tmp_path):
    from omni.adapters.persistence.maintenance import derived_indexes

    async def setup():
        async with opened(tmp_path, Clock()) as (paths, db, objects, agent):
            service = DocumentService(db, objects)
            row = await service.save(agent, "Erased", "do-not-restore-canary")
            await service.erase(agent, row["id"], 1)
            await db.write(lambda u: u.remember(agent, "surviving source"))
            with pytest.raises(BusinessError):
                derived_indexes(paths, rebuild=True)
            await service.close()
            return paths, agent

    paths, agent = asyncio.run(setup())
    with sqlite_connection(paths.database, read_only=False) as connection:
        row = connection.execute(
            "SELECT rowid,agent_id,id,content FROM memory_entries WHERE deleted_at IS NULL"
        ).fetchone()
        connection.execute(
            "INSERT INTO memory_fts(memory_fts,rowid,agent_id,id,content) VALUES('delete',?,?,?,?)", tuple(row)
        )
    with pytest.raises(BusinessError) as error:
        derived_indexes(paths)
    assert error.value.code == "derived_index_invalid"
    assert derived_indexes(paths, rebuild=True)["rebuilt"]
    assert derived_indexes(paths)["ok"]
    assert derived_indexes(paths, rebuild=True)["ok"]

    async def verify():
        async with opened(tmp_path, Clock()) as (_, db, objects, _):
            service = DocumentService(db, objects)
            assert not (await service.search(agent, "do-not-restore-canary"))["items"]
            assert (await db.read(lambda u: u.search_memory(agent, "surviving")))[0]["content"] == "surviving source"
            assert await service.close()

    asyncio.run(verify())


def test_cli_fulltext_check_and_rebuild_are_offline_and_exclusive(tmp_path):
    async def setup():
        async with opened(tmp_path, Clock()) as (_, db, objects, agent):
            service = DocumentService(db, objects)
            await service.save(agent, "Source", "source text")
            await service.close()

    asyncio.run(setup())
    for operation in ("check", "rebuild", "check"):
        result = subprocess.run(
            [sys.executable, "-m", "omni.composition.cli", "--home", str(tmp_path), "indexes", operation],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr + result.stdout
        assert '"ok": true' in result.stdout


def test_offline_global_index_rebuild_process_exit_does_not_commit_half_repair(tmp_path):
    from omni.adapters.persistence.maintenance import derived_indexes

    async def setup():
        async with opened(tmp_path, Clock()) as (paths, db, objects, agent):
            service = DocumentService(db, objects)
            await service.save(agent, "Source", "original source")
            await db.write(lambda u: u.remember(agent, "original memory"))
            await service.close()
            return paths

    paths = asyncio.run(setup())
    with sqlite_connection(paths.database, read_only=False) as connection:
        for row in connection.execute("SELECT rowid,content FROM document_chunks").fetchall():
            connection.execute("INSERT INTO document_fts(document_fts,rowid,content) VALUES('delete',?,?)", row)
        for row in connection.execute("SELECT rowid,agent_id,id,content FROM memory_entries").fetchall():
            connection.execute(
                "INSERT INTO memory_fts(memory_fts,rowid,agent_id,id,content) VALUES('delete',?,?,?,?)", row
            )
    code = """
import os,sys
from pathlib import Path
from omni.adapters.persistence import maintenance
from omni.adapters.platform.runtime.paths import InstancePaths
original=maintenance._rebuild_fulltext
def cut(connection,table):
    original(connection,table)
    os._exit(96)
maintenance._rebuild_fulltext=cut
maintenance.derived_indexes(InstancePaths.resolve(Path(sys.argv[1])),rebuild=True)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)], capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 96, result.stderr
    with sqlite_connection(paths.database) as connection:
        assert (
            connection.execute("SELECT count(*) FROM document_fts WHERE document_fts MATCH 'original'").fetchone()[0]
            == 0
        )
        assert (
            connection.execute("SELECT count(*) FROM memory_fts WHERE memory_fts MATCH 'original'").fetchone()[0] == 0
        )
    assert derived_indexes(paths, rebuild=True)["ok"]
    assert derived_indexes(paths)["ok"]
