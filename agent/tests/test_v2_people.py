"""People, single-source documents, evidence, erasure and exact context revisions."""

import asyncio
import json
import subprocess
import sys
import uuid

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, delete, insert, select, update
from test_v2_objects import Clock, opened
from v2_configuration_helpers import model_instance

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.database import Database
from omni.adapters.persistence.memory_repository import MemoryRepositories
from omni.adapters.persistence.migration_config import head as migration_head
from omni.adapters.persistence.migrations.versions.v0001 import SQL
from omni.adapters.persistence.migrations.versions.v0002 import upgrade as objects_upgrade
from omni.adapters.persistence.sqlite_access import sqlite_connection
from omni.adapters.platform.paths import InstancePaths
from omni.adapters.primitives import UUIDs
from omni.application.engine import Capability
from omni.application.memory_tools import MemoryCapabilities
from omni.application.people import PeopleService
from omni.composition.instance import open_instance
from omni.domain.contracts import (
    ActionResult,
    BusinessError,
    Effect,
    Invocation,
    InvocationContext,
    ModelReply,
    Outcome,
    ToolSpec,
)


def test_owner_unique_scoped_and_not_mutable_or_erasable(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, objects, agent):
            people = PeopleService(db, objects)
            owner = await people.owner(agent)
            other = (await db.write(lambda u: u.create_agent("Second")))["id"]
            owner_two = await people.owner(other)
            assert owner_two["owner_id"] == owner["owner_id"] and owner_two["id"] != owner["id"]
            with pytest.raises(BusinessError) as error:
                await people.get(other, owner["id"])
            assert error.value.status == 404
            with pytest.raises(BusinessError, match="constraint"):
                await db.write(
                    lambda u: u.connection.execute(
                        insert(s.people).values(
                            id=str(uuid.uuid4()),
                            agent_id=agent,
                            created_at=Clock().now(),
                            name="duplicate",
                            owner_id=owner["owner_id"],
                            revision=1,
                        )
                    )
                )
            for operation in [
                lambda u: u.connection.execute(
                    update(s.people).where(s.people.c.id == owner["id"]).values(owner_id=None)
                ),
                lambda u: u.connection.execute(delete(s.people).where(s.people.c.id == owner["id"])),
            ]:
                with pytest.raises(BusinessError, match="constraint"):
                    await db.write(operation)
            with pytest.raises(BusinessError) as error:
                await people.erase(agent, owner["id"], owner["revision"])
            assert error.value.code == "owner_person_protected"

    asyncio.run(scenario())


def test_manual_accounts_cannot_silently_move_between_people(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, objects, agent):
            people = PeopleService(db, objects)
            one, two = await people.create(agent, "One"), await people.create(agent, "Two")
            binding = await people.bind(agent, one["id"], " QQ ", " 12345 ")
            assert (
                binding["channel"] == "qq" and binding["external_id"] == "12345" and binding["verification"] == "manual"
            )
            assert (await people.bind(agent, one["id"], "qq", "12345"))["id"] == binding["id"]
            with pytest.raises(BusinessError) as error:
                await people.bind(agent, two["id"], "qq", "12345")
            assert error.value.code == "account_already_bound"
            with pytest.raises(BusinessError) as error:
                await people.unbind(agent, two["id"], binding["id"], 1)
            assert error.value.status == 404
            with pytest.raises(BusinessError, match="changed"):
                await people.erase(agent, one["id"], 1)
            await people.unbind(agent, one["id"], binding["id"], 1)
            assert (await people.accounts(agent, one["id"]))["items"] == []

    asyncio.run(scenario())


def test_document_revision_history_and_delete_recreate_aba(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, objects, agent):
            people = PeopleService(db, objects)
            owner = await people.owner(agent)
            doc = await people.save(agent, owner["id"], "profile", "first profile", None, 0)
            edited = await people.save(agent, owner["id"], "profile", "second profile", doc["entry_id"], 1)
            with pytest.raises(BusinessError, match="changed"):
                await people.save(agent, owner["id"], "profile", "lost update", doc["entry_id"], 1)
            history = (await people.history(agent, doc["entry_id"]))["items"]
            assert [row["content"] for row in history] == ["first profile", "second profile"]
            assert all(row["sources"][0]["manual_audit_id"] for row in history)
            assert await db.read(lambda u: u.search_memory(agent, "profile")) == []
            assert await db.read(lambda u: u.query("memory", agent)) == []
            empty = await people.save(agent, owner["id"], "profile", "", edited["entry_id"], 2)
            assert empty["entry_id"] is None and empty["revision"] == 0
            rebuilt = await people.save(agent, owner["id"], "profile", "new document", None, 0)
            assert rebuilt["entry_id"] != doc["entry_id"]
            with pytest.raises(BusinessError, match="changed"):
                await people.save(agent, owner["id"], "profile", "stale editor", doc["entry_id"], 1)
            erased = (await people.history(agent, doc["entry_id"]))["items"]
            assert all(row["content"] == "" and row["sources"] == [] and row["erased"] for row in erased)

    asyncio.run(scenario())


def test_import_evidence_binds_revision_and_forget_releases_object(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, objects, agent):
            people = PeopleService(db, objects)
            owner = await people.owner(agent)
            obj = await objects.publish(agent, "真实 UTF-8 档案".encode(), "text/markdown")
            doc = await people.import_object(agent, owner["id"], "resident", obj["id"], None, 0)
            assert (await objects.describe(agent, obj["id"]))["references"] == 1
            first = (await people.history(agent, doc["entry_id"]))["items"][0]
            assert first["sources"][0]["object_id"] == obj["id"] and first["sources"][0]["quote"] == "真实 UTF-8 档案"
            await people.save(agent, owner["id"], "resident", "manual revision", doc["entry_id"], 1)
            assert (await objects.describe(agent, obj["id"]))["references"] == 1
            await people.save(agent, owner["id"], "resident", "", doc["entry_id"], 2)
            assert (await objects.describe(agent, obj["id"]))["references"] == 0
            bad = await objects.publish(agent, b"\xff\xfe", "application/octet-stream")
            with pytest.raises(BusinessError) as error:
                await people.import_object(agent, owner["id"], "profile", bad["id"], None, 0)
            assert error.value.code == "invalid_document_encoding"
            assert (await people.document(agent, owner["id"], "profile"))["entry_id"] is None

    asyncio.run(scenario())


def test_model_evidence_defaults_to_input_and_tombstones_block_reextraction(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _objects, agent):
            turn = await db.write(lambda u: u.accept_turn(agent, None, "source", "I prefer blue", {}))
            context = InvocationContext(agent, turn["run_id"], turn["session_id"], 1)
            adapter = MemoryCapabilities(db)

            async def remember(content, quote):
                return await adapter.invoke(
                    "memory_remember",
                    {"content": content, "quote": quote},
                    operation_id=str(uuid.uuid4()),
                    context=context,
                )

            stored = await remember("I prefer blue", "prefer blue")
            assert stored.effect == Effect.APPLIED
            row = (await db.read(lambda u: u.query("memory", agent)))[0]
            await db.write(lambda u: u.forget(agent, row["id"], 1))
            for content, quote in [
                (" I PREFER BLUE ", "blue"),
                ("Paraphrased preference", "prefer blue"),
                ("Different paraphrase", "blue"),
            ]:
                denied = await remember(content, quote)
                assert (
                    denied.status == Outcome.ERROR
                    and denied.effect == Effect.NONE
                    and denied.error_code == "memory_forgotten"
                )
            invalid = await remember("different", "invented quote")
            assert invalid.error_code == "invalid_evidence" and invalid.effect == Effect.NONE
            assert await db.read(lambda u: u.search_memory(agent, "blue")) == []
            # Explicit owner authorship may intentionally create fresh content.
            owner_authored = await db.write(lambda u: u.remember(agent, "I prefer blue"))
            assert owner_authored["id"] != row["id"]

    asyncio.run(scenario())


def test_person_erasure_rolls_back_as_one_unit_then_scrubs_all_dependents(tmp_path, monkeypatch):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, objects, agent):
            people = PeopleService(db, objects)
            person = await people.create(agent, "private person")
            await people.save(agent, person["id"], "profile", "private profile canary", None, 0)
            await people.save(agent, person["id"], "resident", "private resident canary", None, 0)
            await people.bind(agent, person["id"], "qq", "private-external-canary")
            current = await people.get(agent, person["id"])
            original = MemoryRepositories.forget
            calls = 0

            def fail_second(self, *args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError("injected erasure failure")
                return original(self, *args, **kwargs)

            with monkeypatch.context() as patch:
                patch.setattr(MemoryRepositories, "forget", fail_second)
                with pytest.raises(RuntimeError, match="injected"):
                    await people.erase(agent, person["id"], current["revision"])
            assert (await people.document(agent, person["id"], "profile"))["content"] == "private profile canary"
            assert len((await people.accounts(agent, person["id"]))["items"]) == 1
            await people.erase(agent, person["id"], current["revision"])
            with pytest.raises(BusinessError) as error:
                await people.get(agent, person["id"])
            assert error.value.status == 410
            rows = await db.read(
                lambda u: [dict(r) for r in u.connection.execute(select(s.memory_revisions)).mappings()]
            )
            assert all(row["content"] == "" for row in rows)
            assert await db.read(lambda u: u.connection.execute(select(s.accounts)).first()) is None
            with pytest.raises(BusinessError, match="constraint"):
                await db.write(
                    lambda u: u.connection.execute(
                        insert(s.accounts).values(
                            id=str(uuid.uuid4()),
                            agent_id=agent,
                            created_at=Clock().now(),
                            person_id=person["id"],
                            channel="qq",
                            external_id="new",
                        )
                    )
                )

    asyncio.run(scenario())


def test_erasure_invalidates_active_context_before_another_model_call(tmp_path):
    class Model:
        def __init__(self):
            self.prompts = []

        async def complete(self, messages, tools, settings):
            self.prompts.append(messages[0]["content"])
            if len(self.prompts) == 1:
                return ModelReply("", (Invocation(str(uuid.uuid4()), "observe", {}),), cost_micro=0)
            return ModelReply("done", cost_micro=0)

    class Reader:
        def __init__(self):
            self.started, self.release = asyncio.Event(), asyncio.Event()

        async def invoke(self, name, arguments, *, operation_id, context):
            self.started.set()
            await self.release.wait()
            return ActionResult(Outcome.OK, "observed")

        async def close(self, timeout_s):
            return True

    async def scenario():
        model, reader = Model(), Reader()
        instance = await open_instance(
            tmp_path, model=model, capabilities=(Capability(ToolSpec("observe", "test", {}), reader),)
        )
        try:
            app, people = instance.application, instance.people
            agent = (await app.query("agents", ""))[0]["id"]
            await model_instance(instance, agent)
            owner = await people.owner(agent)
            profile = await people.save(agent, owner["id"], "profile", "profile-before", None, 0)
            resident = await people.save(agent, owner["id"], "resident", "resident-secret-canary", None, 0)
            turn = await app.submit_turn(agent, None, "one", "first")
            async with asyncio.timeout(10):
                await reader.started.wait()
            await people.save(agent, owner["id"], "profile", "profile-after", profile["entry_id"], 1)
            await people.save(agent, owner["id"], "resident", "", resident["entry_id"], 1)
            reader.release.set()
            async with asyncio.timeout(10):
                while (await app.get_run(agent, turn["run_id"]))["state"] not in {"succeeded", "failed"}:
                    await asyncio.sleep(0.01)
            checked = await app.get_run(agent, turn["run_id"])
            assert checked["state"] == "failed" and checked["error_code"] == "context_invalidated"
            assert len(model.prompts) == 1
            assert "profile-before" in model.prompts[0] and "profile-after" not in model.prompts[0]
            footprint = await app.context(agent, turn["run_id"])
            assert {row["memory_id"] for row in footprint} == {profile["entry_id"], resident["entry_id"]}
            assert next(row for row in footprint if row["kind"] == "resident")["erased"]
            assert next(row for row in footprint if row["kind"] == "profile")["content"] == "profile-before"
        finally:
            reader.release.set()
            await instance.close()

    asyncio.run(scenario())


def legacy_database(home):
    paths = InstancePaths.resolve(home)
    paths.initialize()
    identities = {
        name: str(uuid.uuid4())
        for name in (
            "owner",
            "instance",
            "agent",
            "session",
            "message",
            "run",
            "memory",
            "version",
            "anchor",
            "snapshot",
        )
    }
    now = Clock().now()
    engine = create_engine("sqlite+pysqlite:///" + str(paths.database))
    with engine.begin() as connection:
        for statement in SQL:
            connection.exec_driver_sql(statement)
        with Operations.context(MigrationContext.configure(connection)):
            objects_upgrade()
        connection.exec_driver_sql("CREATE TABLE alembic_version(version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO alembic_version VALUES('v2_0002')")
        connection.exec_driver_sql(
            "INSERT INTO principals(id,created_at,role,name) VALUES(?,?,'owner','Owner')", (identities["owner"], now)
        )
        connection.exec_driver_sql(
            "INSERT INTO instances(id,created_at,slot,owner_id) VALUES(?,?,1,?)",
            (identities["instance"], now, identities["owner"]),
        )
        connection.exec_driver_sql(
            "INSERT INTO agents(id,created_at,name,enabled,revision) VALUES(?,?,'Agent',1,1)",
            (identities["agent"], now),
        )
        connection.exec_driver_sql(
            "INSERT INTO sessions(id,agent_id,created_at,title,revision,next_seq) VALUES(?,?,?,'old',1,1)",
            (identities["session"], identities["agent"], now),
        )
        connection.exec_driver_sql(
            "INSERT INTO messages(id,agent_id,created_at,session_id,seq,role,content) VALUES(?,?,?,?,1,'user','historical quote')",
            (identities["message"], identities["agent"], now, identities["session"]),
        )
        connection.exec_driver_sql(
            "INSERT INTO runs(id,agent_id,created_at,session_id,input_message_id,state,config_revision,config_snapshot,last_seq,error_code) VALUES(?,?,?,?,?,'succeeded',1,'{}',0,'')",
            (identities["run"], identities["agent"], now, identities["session"], identities["message"]),
        )
        connection.exec_driver_sql(
            "INSERT INTO memory_entries(id,agent_id,created_at,kind,content,revision) VALUES(?,?,?,'fact','historical memory',1)",
            (identities["memory"], identities["agent"], now),
        )
        connection.exec_driver_sql(
            "INSERT INTO memory_revisions(id,agent_id,created_at,memory_id,revision,content) VALUES(?,?,?,?,1,'historical memory')",
            (identities["version"], identities["agent"], now, identities["memory"]),
        )
        connection.exec_driver_sql(
            "INSERT INTO source_anchors(id,agent_id,created_at,memory_id,message_id,quote) VALUES(?,?,?,?,?,'historical quote')",
            (identities["anchor"], identities["agent"], now, identities["memory"], identities["message"]),
        )
        connection.exec_driver_sql(
            "INSERT INTO context_snapshots(id,agent_id,created_at,run_id,message_ids,memory_ids) VALUES(?,?,?,?,?,?)",
            (
                identities["snapshot"],
                identities["agent"],
                now,
                identities["run"],
                json.dumps([identities["message"]]),
                json.dumps([identities["memory"]]),
            ),
        )
    engine.dispose()
    return paths, identities


def test_upgrade_preserves_history_but_marks_missing_revision_as_unresolved(tmp_path):
    paths, ids = legacy_database(tmp_path)

    async def scenario():
        database = Database(paths.database, Clock(), UUIDs(), objects_root=paths.objects)
        try:
            owner = await database.read(lambda u: u.owner_person(ids["agent"]))
            assert owner["owner_id"] == ids["owner"]
            footprint = await database.read(lambda u: u.context_memories(ids["agent"], ids["snapshot"]))
            assert footprint[0]["state"] == "legacy_unresolved" and footprint[0]["content"] is None
            history = await database.read(lambda u: u.memory_history(ids["agent"], ids["memory"]))
            assert history[0]["content"] == "historical memory"
            assert history[0]["sources"][0]["memory_revision_id"] == ids["version"]
            assert (await database.integrity())["foreign_keys"] == 0
        finally:
            await database.close()

    asyncio.run(scenario())
    assert list(paths.database.parent.glob("schema-backups/*/manifest.json"))


def test_process_exit_mid_schema_rebuild_keeps_old_database_recoverable(tmp_path):
    paths, ids = legacy_database(tmp_path)
    code = """
import os,sys
from pathlib import Path
from alembic.ddl.impl import DefaultImpl
from omni.adapters.persistence.database import Database
from omni.adapters.primitives import SystemClock,UUIDs
from omni.adapters.platform.paths import InstancePaths,InstanceLock
original=DefaultImpl._exec
def cut(self,statement,*args,**kwargs):
    if 'uq_people_owner' in str(statement): os._exit(77)
    return original(self,statement,*args,**kwargs)
DefaultImpl._exec=cut
paths=InstancePaths.resolve(Path(sys.argv[1])); lock=InstanceLock(paths); lock.acquire()
Database(paths.database,SystemClock(),UUIDs(),objects_root=paths.objects)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)], capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 77, result.stderr
    with sqlite_connection(paths.database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "v2_0002"
        assert (
            connection.execute("SELECT content FROM memory_entries WHERE id=?", (ids["memory"],)).fetchone()[0]
            == "historical memory"
        )
        assert "memory_ids" in {row[1] for row in connection.execute("PRAGMA table_info(context_snapshots)")}

    async def recover():
        database = Database(paths.database, Clock(), UUIDs(), objects_root=paths.objects)
        try:
            assert database.schema_version == migration_head()
            assert (await database.integrity())["integrity"] == "ok"
        finally:
            await database.close()

    asyncio.run(recover())
