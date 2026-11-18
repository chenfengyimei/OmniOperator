"""Real process exits at schedule admission, ownership and schema publication boundaries."""

import asyncio
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import select
from test_v2_scheduling import Clock, interval, setup

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.database import Database
from omni.adapters.persistence.sqlite_access import sqlite_connection
from omni.adapters.primitives import UUIDs
from omni.adapters.timezones import PinnedTimezones
from omni.application.scheduling import SchedulingService


@pytest.mark.parametrize("phase", ["before_commit", "after_commit", "claimed", "started"])
def test_process_exit_recovers_due_and_claim_without_repeating_execution(tmp_path, phase):
    async def seed():
        db, agent, _, service = await setup(tmp_path)
        try:
            return agent, (await service.save(agent, "periodic", True, interval()))["id"]
        finally:
            await db.close()

    agent, plan_id = asyncio.run(seed())
    code = """
import asyncio,os,sys
from pathlib import Path
from omni.adapters.persistence.database import Database
from omni.adapters.persistence.repository import Repositories
from omni.adapters.platform.runtime.paths import InstancePaths,InstanceLock
from omni.adapters.primitives import UUIDs
from omni.adapters.timezones import PinnedTimezones
from omni.application.scheduling import SchedulingService
class Clock:
    def now(self): return '2026-11-03T00:01:01.000000+00:00'
    def monotonic(self): return 0.0
original=Repositories.accept_turn
def cut(self,*args):
    result=original(self,*args)
    if sys.argv[3]=='before_commit': os._exit(96)
    return result
Repositories.accept_turn=cut
async def main():
    paths=InstancePaths.resolve(Path(sys.argv[1])); paths.initialize()
    lock=InstanceLock(paths); lock.acquire()
    db=Database(Path(sys.argv[1])/'omni.db',Clock(),UUIDs())
    service=SchedulingService(db,Clock(),PinnedTimezones(),lambda:None,lambda a,r:None)
    await service.tick()
    if sys.argv[3] in ('claimed','started'):
        run=(await db.write(lambda u:u.claim_runs(4)))[0]
        if sys.argv[3]=='started':
            def start(u):
                u.transition(sys.argv[2],run['id'],'running')
                u.reserve_call(sys.argv[2],run['id'],50,100,'fake')
            await db.write(start)
    os._exit(96)
asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path), agent, phase],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 96, result.stderr

    async def recover():
        clock = Clock("2026-11-03T00:02:01.000000+00:00")
        db = Database(tmp_path / "omni.db", clock, UUIDs())
        service = SchedulingService(db, clock, PinnedTimezones(), lambda: None, lambda a, r: None)
        try:
            await db.write(lambda u: u.recover())
            await service.tick()
            occurrences = await service.occurrences(agent, plan_id)
            runs = await db.read(lambda u: u.query("runs", agent))
            assert len(occurrences) == len(runs) == 1
            assert occurrences[0]["run_id"] == runs[0]["id"]
            if phase == "started":
                assert runs[0]["state"] == "interrupted"
                assert (await service.get(agent, plan_id))["blocker"]["reason"] == "unknown"
                assert await db.write(lambda u: u.claim_runs(4)) == []
                assert (await db.read(lambda u: u.query("calls", agent)))[0]["state"] == "unknown"
                assert (
                    await db.read(lambda u: u.connection.execute(select(s.budget_accounts.c.held_micro)).scalar_one())
                    == 50
                )
            else:
                claims = await db.write(lambda u: u.claim_runs(4))
                assert len(claims) == 1 and claims[0]["id"] == runs[0]["id"]
            assert (await db.integrity())["foreign_keys"] == 0
        finally:
            await db.close()

    asyncio.run(recover())


def test_v7_rebuild_exit_rolls_back_and_retry_preserves_unverified_history(tmp_path):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine
    from test_v2_people import legacy_database

    from omni.adapters.persistence.migrations.versions.v0003 import upgrade as v3
    from omni.adapters.persistence.migrations.versions.v0004 import upgrade as v4
    from omni.adapters.persistence.migrations.versions.v0005 import upgrade as v5
    from omni.adapters.persistence.migrations.versions.v0006 import upgrade as v6

    paths, ids = legacy_database(tmp_path)
    plan_id, occurrence_id = str(uuid.uuid4()), str(uuid.uuid4())
    engine = create_engine("sqlite+pysqlite:///" + str(paths.database))
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            for upgrade in (v3, v4, v5, v6):
                upgrade()
        connection.exec_driver_sql("UPDATE alembic_version SET version_num='v2_0006'")
        connection.exec_driver_sql(
            "INSERT INTO schedules(id,agent_id,created_at,name,prompt,timezone,expression,revision,enabled,next_at) VALUES(?,?,?,'old','historical prompt','UTC','0 9 * * *',1,1,?)",
            (plan_id, ids["agent"], Clock().now(), Clock().now()),
        )
        connection.exec_driver_sql(
            "INSERT INTO occurrences(id,agent_id,created_at,schedule_id,scheduled_at,run_id) VALUES(?,?,?,?,?,?)",
            (occurrence_id, ids["agent"], Clock().now(), plan_id, Clock().now(), ids["run"]),
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
    if 'CREATE TABLE schedule_progress' in str(statement): os._exit(97)
    return original(self,statement,*args,**kwargs)
DefaultImpl._exec=cut
paths=InstancePaths.resolve(Path(sys.argv[1])); lock=InstanceLock(paths); lock.acquire()
Database(paths.database,SystemClock(),UUIDs(),objects_root=paths.objects)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)], capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 97, result.stderr
    with sqlite_connection(paths.database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "v2_0006"
        assert connection.execute("SELECT prompt FROM schedules").fetchone()[0] == "historical prompt"

    async def verify():
        db = Database(paths.database, Clock(), UUIDs(), objects_root=paths.objects)
        try:
            plan = await db.read(lambda u: u.schedule_get(ids["agent"], plan_id))
            due = await db.read(lambda u: u.schedule_occurrences(ids["agent"], plan_id))
            assert not plan["enabled"] and plan["progress"]["next_at"] is None
            assert plan["progress"]["error_code"] == "legacy_schedule_unverified"
            assert due[0]["id"] == occurrence_id and due[0]["run_id"] == ids["run"]
            assert due[0]["state"] == "legacy"
            assert (await db.integrity())["foreign_keys"] == 0
        finally:
            await db.close()

    asyncio.run(verify())
    assert list(paths.database.parent.glob("schema-backups/*/manifest.json"))


def test_restored_snapshot_quarantines_execution_and_invalidates_old_reviews(tmp_path):
    from test_v2_objects import opened

    from omni.adapters.persistence.maintenance import backup, restore
    from omni.adapters.platform.runtime.paths import InstancePaths
    from omni.domain.contracts import BusinessError

    clock = Clock()

    async def seed():
        async with opened(tmp_path / "original", clock) as (paths, db, _, agent):
            svc = SchedulingService(db, clock, PinnedTimezones(), lambda: None, lambda a, r: None)
            unknown = await svc.save(agent, "unknown", True, interval())
            clock.advance(60)
            await svc.tick()
            one = (await svc.occurrences(agent, unknown["id"]))[0]
            await db.write(lambda u: u.transition(agent, one["run_id"], "running"))
            await db.write(lambda u: u.reserve_call(agent, one["run_id"], 50, 100, "fake"))
            await db.write(lambda u: u.recover())
            blocker = (await svc.get(agent, unknown["id"]))["blocker"]
            await svc.review(agent, unknown["id"], blocker["id"], blocker["fingerprint"])
            await svc.save(agent, "unknown", False, unknown["value"], unknown["id"], 1)
            plan = await svc.save(agent, "queued", True, interval())
            clock.advance(60)
            await svc.tick()
            due = (await svc.occurrences(agent, plan["id"]))[0]
            manual = await db.write(lambda u: u.accept_turn(agent, None, "delivery", "manual", {}))
            await db.write(lambda u: u.transition(agent, manual["run_id"], "running"))
            from v2_delivery_helpers import descriptor

            route = await descriptor(db, agent)
            await db.write(lambda u: u.snapshot(agent, manual["run_id"], [], []))
            await db.write(lambda u: u.finish(agent, manual["run_id"], "output", route))
            return paths, agent, plan["id"], due["run_id"], unknown["id"], blocker

    paths, agent, plan_id, run_id, unknown_id, previous = asyncio.run(seed())
    snapshot = tmp_path / "snapshot"
    backup(paths, snapshot)

    async def complete_original():
        db = Database(paths.database, clock, UUIDs(), objects_root=paths.objects)
        try:
            await db.write(lambda u: u.transition(agent, run_id, "running"))
            await db.write(lambda u: u.finish(agent, run_id, "completed after backup"))
        finally:
            await db.close()

    asyncio.run(complete_original())
    target = tmp_path / "restored"
    report = restore(snapshot, target)
    assert report["execution_quarantined"] and report["quarantine"]["interrupted_runs"] == 1
    assert restore(snapshot, target)["already_restored"]

    async def verify():
        db = Database(InstancePaths.resolve(target).database, clock, UUIDs())
        svc = SchedulingService(db, clock, PinnedTimezones(), lambda: None, lambda a, r: None)
        try:
            await db.write(lambda u: u.recover())
            clock.advance(600)
            assert await svc.tick() == 0
            assert await db.write(lambda u: u.claim_runs(4)) == []
            run = await db.read(lambda u: u.get_run(agent, run_id))
            assert run["state"] == "interrupted" and run["error_code"] == "backup_restored_requires_review"
            plan = await svc.get(agent, plan_id)
            assert plan["progress"]["next_at"] is None and plan["progress"]["error_code"] == "restored_schedule_paused"
            blocker = (await svc.get(agent, unknown_id))["blocker"]
            assert blocker["reason"] == "unknown" and blocker["fingerprint"] != previous["fingerprint"]
            with pytest.raises(BusinessError):
                await svc.review(agent, unknown_id, blocker["id"], previous["fingerprint"])
            await svc.review(agent, unknown_id, blocker["id"], blocker["fingerprint"])
            assert await db.read(lambda u: u.connection.execute(select(s.schedule_reviews.c.id)).all())
            assert (
                await db.read(lambda u: u.connection.execute(select(s.budget_accounts.c.held_micro)).scalar_one()) == 50
            )
            assert (await db.read(lambda u: u.query("outbox", agent)))[0]["state"] == "unknown"
            assert (await db.integrity())["foreign_keys"] == 0
        finally:
            await db.close()

    asyncio.run(verify())


@pytest.mark.parametrize("phase", ["transaction", "before_report", "after_report"])
def test_restore_process_exit_never_exposes_automatic_execution(tmp_path, phase):
    from test_v2_objects import opened

    from omni.adapters.persistence.maintenance import backup, restore
    from omni.composition.instance import open_instance
    from omni.domain.contracts import BusinessError

    async def seed():
        async with opened(tmp_path / "original", Clock()) as (paths, db, _, agent):
            await db.write(lambda u: u.accept_turn(agent, None, "pending", "do not replay", {}))
            return paths

    paths = asyncio.run(seed())
    snapshot = tmp_path / "snapshot"
    backup(paths, snapshot)
    target = tmp_path / "partial"
    code = """
import os,sys
from pathlib import Path
import omni.adapters.persistence.maintenance as m
old=m._quarantine_restored_work
def cut(*args):
    result=old(*args)
    if sys.argv[3]=='transaction': os._exit(98)
    return result
m._quarantine_restored_work=cut
write=m.durable_json
def publish(path,value):
    if path.name=='restore-report.json' and sys.argv[3]=='before_report': os._exit(98)
    result=write(path,value)
    if path.name=='restore-report.json' and sys.argv[3]=='after_report': os._exit(98)
    return result
m.durable_json=publish
m.restore(Path(sys.argv[1]),Path(sys.argv[2]))
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(snapshot), str(target), phase],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 98, result.stderr

    async def cannot_start():
        with pytest.raises(BusinessError, match="Restore did not finish"):
            await open_instance(target, capabilities=())

    asyncio.run(cannot_start())
    if phase == "after_report":
        assert restore(snapshot, target)["already_restored"]
        assert not (target / "restore-in-progress.json").exists()
    else:
        with pytest.raises(BusinessError):
            restore(snapshot, target)
        assert restore(snapshot, tmp_path / "fresh")["execution_quarantined"]
