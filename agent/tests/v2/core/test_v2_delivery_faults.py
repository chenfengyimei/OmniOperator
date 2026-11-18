"""Outbound fault boundaries use isolated SQLite and trusted fake transports only."""

import asyncio
import subprocess
import sys
import uuid

import pytest
from omni_test_support.v2.delivery import drain
from omni_test_support.v2.v2_delivery_helpers import Clock, FakePort, output, setup
from sqlalchemy import delete, func, select

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.database import Database
from omni.adapters.primitives import UUIDs
from omni.domain.contracts import BusinessError
from omni.domain.delivery import DeliveryResult


async def approve(service, agent, row):
    return await service.decide(agent, row["id"], True, row["revision"], row["fingerprint"])


@pytest.mark.parametrize("change", ["expired", "configuration", "credential", "erasure", "missing_snapshot"])
def test_authority_is_checked_again_before_external_write(tmp_path, change):
    async def scenario():
        db, agent, channel, clock, port, service = await setup(tmp_path)
        try:
            turn, message = await output(db, agent)
            row = await service.create(agent, message["id"], channel["id"], "target", "key")
            await approve(service, agent, row)
            if change == "expired":
                clock.advance(301)
            elif change == "configuration":
                await db.write(
                    lambda u: u.configuration_put(
                        agent, channel["id"], "Changed", False, channel["value"], channel["revision"]
                    )
                )
            elif change == "credential":
                await service.credential_changed("fixture/key")
            elif change == "erasure":
                run = await db.read(lambda u: u.get_run(agent, turn["run_id"]))
                await db.write(lambda u: u.erase_message(agent, run["input_message_id"]))
            else:
                await db.write(lambda u: u.connection.execute(delete(s.context_messages)))
                await db.write(lambda u: u.connection.execute(delete(s.context_snapshots)))
            await service.tick()
            await drain(service)
            assert port.calls == []
            final = await service.get(agent, row["id"])
            assert final["state"] in ("blocked", "cancelled")
            if change in ("erasure", "missing_snapshot"):
                with pytest.raises(BusinessError, match="invalidated"):
                    await service.create(agent, message["id"], channel["id"], "other", "after-erase")
        finally:
            await service.close()
            await db.close()

    asyncio.run(scenario())


def test_same_real_account_is_serial_across_agents_and_expired_leases(tmp_path):
    async def scenario():
        db, agent, channel, clock, _, service = await setup(tmp_path)
        try:
            other = await db.write(lambda u: u.create_agent("Other"))
            other_id = other["id"]
            channel_two = await db.write(
                lambda u: u.configuration_create(other_id, "channel", "Same account", True, channel["value"])
            )
            _, first = await output(db, agent)
            _, second = await output(db, other_id)
            a = await service.create(agent, first["id"], channel["id"], "a", "a")
            b = await service.create(other_id, second["id"], channel_two["id"], "b", "b")
            await approve(service, other_id, b)
            assert (
                await db.write(lambda u: u.delivery_claim(other_id, b["id"], FakePort.identity, "account-one")) is None
            )
            await approve(service, agent, a)
            claim = await db.write(lambda u: u.delivery_claim(agent, a["id"], FakePort.identity, "account-one"))
            await db.write(
                lambda u: u.delivery_start(agent, claim["id"], claim["token"], FakePort.identity, "account-one")
            )
            clock.advance(61)
            await service.cancel(agent, a["id"])
            assert (
                await db.write(lambda u: u.delivery_claim(other_id, b["id"], FakePort.identity, "account-one")) is None
            )
            unknown = await service.get(agent, a["id"])
            with pytest.raises(BusinessError, match="still running"):
                await service.review(
                    agent, a["id"], unknown["revision"], unknown["review_fingerprint"], "allow_future", "fixture"
                )
            with pytest.raises(BusinessError):
                await service.get(other_id, a["id"])
            await db.write(
                lambda u: u.delivery_settle(
                    agent,
                    claim["id"],
                    claim["token"],
                    {
                        "status": "unknown",
                        "fingerprint": a["fingerprint"],
                        "receipt_id": "",
                        "error_code": "fixture",
                        "retryable": False,
                    },
                )
            )
            unknown = await service.get(agent, a["id"])
            await service.review(
                agent,
                a["id"],
                unknown["revision"],
                unknown["review_fingerprint"],
                "allow_future",
                "The local fixture has stopped",
            )
            assert (
                await db.write(lambda u: u.delivery_claim(other_id, b["id"], FakePort.identity, "account-one"))
                is not None
            )
            assert (await db.integrity())["foreign_keys"] == 0
        finally:
            await service.close()
            await db.close()

    asyncio.run(scenario())


def test_cancel_ignored_retains_slot_until_late_receipt_and_repeated_close(tmp_path):
    class Port(FakePort):
        def __init__(self):
            super().__init__()
            self.started, self.release = asyncio.Event(), asyncio.Event()

        async def send(self, message):
            self.calls.append(message)
            self.started.set()
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    pass
            return DeliveryResult("sent", message.fingerprint, receipt_id="late-provider-receipt")

    async def scenario():
        port = Port()
        db, agent, channel, clock, _, service = await setup(tmp_path, port)
        try:
            _, message = await output(db, agent)
            row = await service.create(agent, message["id"], channel["id"], "target", "key")
            await approve(service, agent, row)
            await service.tick()
            await asyncio.wait_for(port.started.wait(), 10)
            clock.advance(31)
            await service.tick()
            assert (await service.get(agent, row["id"]))["state"] == "unknown"
            assert not await service.close(0.01)
            assert len(service._active) == 1
            assert (
                await db.read(
                    lambda u: u.connection.execute(
                        select(func.count())
                        .select_from(s.dispatch_resources)
                        .where(s.dispatch_resources.c.state == "held")
                    ).scalar_one()
                )
                == 1
            )
            port.release.set()
            await drain(service)
            final = await service.get(agent, row["id"])
            assert final["state"] == "unknown" and len(port.calls) == 1
            result = (await service.attempts(agent, row["id"]))[0]["results"][0]
            assert result["late"] and result["status"] == "sent"
            assert await service.close(10)
        finally:
            port.release.set()
            await service.close()
            await db.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("component", ["database", "port"])
def test_shutdown_budget_keeps_one_owned_cleanup_task(tmp_path, component):
    class Port(FakePort):
        def __init__(self):
            super().__init__()
            self.close_calls = 0
            self.release = asyncio.Event()

        async def close(self, timeout_s):
            self.close_calls += 1
            if component == "port":
                await self.release.wait()
            return True

    async def scenario():
        port = Port()
        db, _, _, _, _, service = await setup(tmp_path, port)
        release = asyncio.Event()

        class SlowDatabase:
            calls = 0

            async def write(self, operation):
                self.calls += 1
                await release.wait()
                return await db.write(operation)

        slow = SlowDatabase()
        if component == "database":
            service._db = slow
        try:
            assert not await service.close(0.02)
            assert not await service.close(0.02)
            assert (slow.calls if component == "database" else port.close_calls) == 1
            release.set()
            port.release.set()
            assert await service.close(10)
            assert port.close_calls == 1
        finally:
            release.set()
            port.release.set()
            await service.close()
            await db.close()

    asyncio.run(scenario())


def test_atomic_output_preserved_when_channel_authority_changes(tmp_path):
    async def scenario():
        db, agent, channel, _, port, service = await setup(tmp_path)
        try:
            turn = await db.write(lambda u: u.accept_turn(agent, None, "turn", "question", {}))
            await db.write(lambda u: u.transition(agent, turn["run_id"], "running"))
            await db.write(lambda u: u.snapshot(agent, turn["run_id"], [], []))
            await db.write(
                lambda u: u.configuration_put(
                    agent, channel["id"], "Disabled", False, channel["value"], channel["revision"]
                )
            )
            descriptor = {
                "config_id": channel["id"],
                "config_revision_id": channel["revision_id"],
                "target": "target",
                "adapter_id": FakePort.identity,
                "account_key": "account-one",
            }
            await db.write(lambda u: u.finish(agent, turn["run_id"], "generated-but-not-sent", descriptor))
            assert (await db.read(lambda u: u.get_run(agent, turn["run_id"])))["state"] == "succeeded"
            row = (await service.page(agent))[0]
            assert row["state"] == "blocked" and row["text"] == "generated-but-not-sent"
            await service.tick()
            assert port.calls == []
            await service.cancel(agent, row["id"])
            assert (await service.get(agent, row["id"]))["state"] == "cancelled"
        finally:
            await service.close()
            await db.close()

    asyncio.run(scenario())


def test_late_observation_invalidates_exact_reviews_but_archive_does_not(tmp_path):
    from omni.adapters.timezones import PinnedTimezones
    from omni.application.scheduling import SchedulingService

    async def scenario():
        db, agent, channel, clock, _, service = await setup(tmp_path)
        scheduler = SchedulingService(db, clock, PinnedTimezones(), lambda: None, lambda a, r: None)
        try:
            plan = await scheduler.save(
                agent,
                "Fixture",
                True,
                {"kind": "interval", "interval_seconds": 60, "expression": "", "prompt": "generate"},
            )
            clock.advance(60)
            await scheduler.tick()
            due = (await scheduler.occurrences(agent, plan["id"]))[0]
            await db.write(lambda u: u.transition(agent, due["run_id"], "running"))
            await db.write(lambda u: u.snapshot(agent, due["run_id"], [], []))
            route = {
                "config_id": channel["id"],
                "config_revision_id": channel["revision_id"],
                "adapter_id": FakePort.identity,
                "account_key": "account-one",
                "target": "target",
            }
            await db.write(lambda u: u.finish(agent, due["run_id"], "reply", route))
            row = (await service.page(agent))[0]
            await approve(service, agent, row)
            claim = await db.write(lambda u: u.delivery_claim(agent, row["id"], FakePort.identity, "account-one"))
            await db.write(
                lambda u: u.delivery_start(agent, claim["id"], claim["token"], FakePort.identity, "account-one")
            )
            await service.cancel(agent, row["id"])
            # The fake transport has been independently verified stopped; its
            # first durable return observation has not arrived yet.
            await db.write(lambda u: u.delivery_release_boot())
            unknown = await service.get(agent, row["id"])
            blocker = (await scheduler.get(agent, plan["id"]))["blocker"]
            await scheduler.review(agent, plan["id"], blocker["id"], blocker["fingerprint"])
            reviewed = await service.review(
                agent,
                row["id"],
                unknown["revision"],
                unknown["review_fingerprint"],
                "allow_future",
                "Verified fixture cleanup; external result uncertain",
            )
            await service.archive(agent, row["id"], reviewed["revision"])
            assert (await scheduler.get(agent, plan["id"]))["blocker"] is None
            assert (await service.get(agent, row["id"]))["review_valid"]
            result = {
                "status": "sent",
                "fingerprint": row["fingerprint"],
                "receipt_id": "late",
                "error_code": "",
                "retryable": False,
            }
            await db.write(lambda u: u.delivery_settle(agent, claim["id"], claim["token"], result))
            late = await service.get(agent, row["id"])
            assert late["state"] == "unknown" and not late["review_valid"]
            assert late["outcome_revision"] > reviewed["outcome_revision"]
            assert (await scheduler.get(agent, plan["id"]))["blocker"]["reason"] == "unknown"
            with pytest.raises(BusinessError):
                await service.review(
                    agent, row["id"], unknown["revision"], unknown["review_fingerprint"], "allow_future", "stale"
                )
            await db.write(lambda u: u.delivery_settle(agent, claim["id"], claim["token"], result))
            assert (await service.get(agent, row["id"]))["outcome_revision"] == late["outcome_revision"]
        finally:
            await service.close()
            await db.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["intent_before_commit", "approved", "claimed", "sending", "settled"])
def test_process_exit_recovery_never_replays_external_write(tmp_path, phase):
    async def seed():
        db, agent, channel, _, _, service = await setup(tmp_path)
        try:
            _, message = await output(db, agent)
            row = await service.create(agent, message["id"], channel["id"], "target", "key")
            return agent, row["id"]
        finally:
            # Intentionally model a process exit, not graceful revocation.
            await db.close()

    agent, identity = asyncio.run(seed())
    code = """
import asyncio,os,sys
from pathlib import Path
from omni.adapters.persistence.database import Database
from omni.adapters.primitives import UUIDs
class Clock:
 def now(self):return '2026-11-04T00:00:00.000000+00:00'
 def monotonic(self):return 0.0
async def main():
 db=Database(Path(sys.argv[1])/'omni.db',Clock(),UUIDs())
 agent,key,phase=sys.argv[2:]
 row=await db.read(lambda u:u.delivery_get(agent,key))
 def approve(u):
  u.delivery_decide(agent,key,True,row['revision'],row['fingerprint'])
  if phase=='intent_before_commit':os._exit(95)
 await db.write(approve)
 if phase in ('claimed','sending','settled'):
  claim=await db.write(lambda u:u.delivery_claim(agent,key,'fixture-qq/v1','account-one'))
  if phase in ('sending','settled'):
   await db.write(lambda u:u.delivery_start(agent,claim['id'],claim['token'],'fixture-qq/v1','account-one'))
  if phase=='settled':
   await db.write(lambda u:u.delivery_settle(agent,claim['id'],claim['token'],{'status':'sent','fingerprint':row['fingerprint'],'receipt_id':'fixture','error_code':'','retryable':False}))
 os._exit(95)
asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path), agent, identity, phase],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 95, result.stderr

    async def recover():
        db = Database(tmp_path / "omni.db", Clock(), UUIDs())
        try:
            await db.write(lambda u: u.recover())
            row = await db.read(lambda u: u.delivery_get(agent, identity))
            expected = {
                "intent_before_commit": "awaiting_approval",
                "approved": "cancelled",
                "claimed": "cancelled",
                "sending": "unknown",
                "settled": "sent",
            }
            assert row["state"] == expected[phase]
            assert await db.read(lambda u: u.delivery_candidates()) == []
            assert (await db.integrity())["foreign_keys"] == 0
        finally:
            await db.close()

    asyncio.run(recover())


def test_v8_migration_exit_restores_old_schema_then_quarantines_unverified_delivery(tmp_path):
    from importlib import import_module

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from omni_test_support.v2.people import legacy_database
    from sqlalchemy import create_engine

    from omni.adapters.persistence.sqlite_access import sqlite_connection

    paths, ids = legacy_database(tmp_path)
    delivery_id, message_id = str(uuid.uuid4()), str(uuid.uuid4())
    engine = create_engine("sqlite+pysqlite:///" + str(paths.database))
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            for version in range(3, 8):
                import_module(f"omni.adapters.persistence.migrations.versions.v{version:04d}").upgrade()
        connection.exec_driver_sql("UPDATE alembic_version SET version_num='v2_0007'")
        connection.exec_driver_sql(
            "INSERT INTO messages(id,agent_id,created_at,session_id,seq,role,content,run_id) VALUES(?,?,?,?,2,'assistant','old-unverified-output',?)",
            (message_id, ids["agent"], Clock().now(), ids["session"], ids["run"]),
        )
        connection.exec_driver_sql(
            "INSERT INTO outbox(id,agent_id,created_at,run_id,message_id,channel,target,state) VALUES(?,?,?,?,?,'fixture','target','pending')",
            (delivery_id, ids["agent"], Clock().now(), ids["run"], message_id),
        )
    engine.dispose()
    code = """
import os,sys
from pathlib import Path
from alembic.ddl.impl import DefaultImpl
from omni.adapters.persistence.database import Database
from omni.adapters.primitives import SystemClock,UUIDs
original=DefaultImpl._exec
def cut(self,statement,*args,**kwargs):
 if 'CREATE TABLE delivery_attempts' in str(statement):os._exit(94)
 return original(self,statement,*args,**kwargs)
DefaultImpl._exec=cut
Database(Path(sys.argv[1])/'data/omni.db',SystemClock(),UUIDs(),objects_root=Path(sys.argv[1])/'objects')
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)], capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 94, result.stderr
    with sqlite_connection(paths.database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "v2_0007"
        assert connection.execute("SELECT state FROM outbox").fetchone()[0] == "pending"

    async def verify():
        db = Database(paths.database, Clock(), UUIDs(), objects_root=paths.objects)
        try:
            row = await db.read(lambda u: u.delivery_get(ids["agent"], delivery_id))
            assert row["state"] == "unknown" and row["legacy"] and row["legacy_state"] == "pending"
            assert row["error_code"] == "legacy_delivery_unverified"
            assert await db.read(lambda u: u.delivery_candidates()) == []
            assert (await db.integrity())["foreign_keys"] == 0
        finally:
            await db.close()

    asyncio.run(verify())
    assert list(paths.database.parent.glob("schema-backups/*/manifest.json"))
