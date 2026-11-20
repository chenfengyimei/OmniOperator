"""Automatic daily memory decay via maintenance tick: hour gate, once-per-day, bounded, disabled-in-tests; real isolated SQLite."""

from __future__ import annotations

import datetime as dt

from omni_test_support.v2.objects import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.objects import (
    Clock as Clock,
)
from omni_test_support.v2.objects import (
    asyncio as asyncio,
)
from omni_test_support.v2.objects import (
    opened as opened,
)
from omni_test_support.v2.objects import (
    pytest as pytest,
)
from omni_test_support.v2.objects import (
    s as s,
)
from omni_test_support.v2.v2_delivery_helpers import Observation
from sqlalchemy import update

from omni.application.documents import DocumentService
from omni.application.maintenance import MaintenanceService
from omni.domain.memory_decay import DECAY_DAILY_HOUR, DECAY_TICK_LIMIT, should_auto_decay


def at_hour(hour: int, day: int = 20) -> dt.datetime:
    return dt.datetime(2026, 11, day, hour, 0, tzinfo=dt.timezone.utc)


@pytest.mark.parametrize(
    "last,today,hour,daily,expected",
    [
        (None, "2026-11-20", 5, 4, True),
        (None, "2026-11-20", 4, 4, True),
        (None, "2026-11-20", 3, 4, False),
        ("2026-11-20", "2026-11-20", 5, 4, False),
        ("2026-11-19", "2026-11-20", 5, 4, True),
        ("2026-11-19", "2026-11-20", 3, 4, False),
    ],
)
def test_should_auto_decay_hour_and_date_gate(last, today, hour, daily, expected):
    assert should_auto_decay(last, today, hour, daily) is expected


def test_auto_tick_waits_for_daily_hour_then_promotes_fresh(tmp_path):
    async def scenario():
        clock = Clock()
        clock.current = at_hour(3)
        async with opened(tmp_path / "instance", clock) as (_, db, _, agent):
            row = await db.write(lambda u: u.remember(agent, "fresh fact"))
            assert await db.write(lambda u: u.memory_decay_auto_tick(DECAY_DAILY_HOUR, DECAY_TICK_LIMIT)) == 0
            early = await db.read(lambda u: u._one(s.memory_entries, agent, str(row["id"])))
            assert early["clarity"] == "clear"
            assert (await db.read(lambda u: u.memory_decay_state(agent)))["last_decay_date"] is None
            clock.current = at_hour(5)
            assert await db.write(lambda u: u.memory_decay_auto_tick(DECAY_DAILY_HOUR, DECAY_TICK_LIMIT)) == 1
            promoted = await db.read(lambda u: u._one(s.memory_entries, agent, str(row["id"])))
            assert promoted["clarity"] == "vivid"
            assert (await db.read(lambda u: u.memory_decay_state(agent)))["last_decay_date"] == "2026-11-20"

    asyncio.run(scenario())


def test_auto_tick_once_per_day_and_next_day(tmp_path):
    async def scenario():
        clock = Clock()
        clock.current = at_hour(5)
        async with opened(tmp_path / "instance", clock) as (_, db, _, agent):
            await db.write(lambda u: u.remember(agent, "aging fact"))
            assert await db.write(lambda u: u.memory_decay_auto_tick(4, 8)) == 1
            assert await db.write(lambda u: u.memory_decay_auto_tick(4, 8)) == 0
            clock.current = at_hour(5, day=21)
            assert await db.write(lambda u: u.memory_decay_auto_tick(4, 8)) == 1
            assert await db.write(lambda u: u.memory_decay_auto_tick(4, 8)) == 0

    asyncio.run(scenario())


def test_auto_tick_bounded_limit_across_agents(tmp_path):
    async def scenario():
        clock = Clock()
        clock.current = at_hour(5)
        async with opened(tmp_path / "instance", clock) as (_, db, _, agent):
            second = await db.write(lambda u: u.create_agent("Second"))
            third = await db.write(lambda u: u.create_agent("Third"))
            for identity in (agent, str(second["id"]), str(third["id"])):
                await db.write(lambda u, identity=identity: u.remember(identity, "fact"))
            assert await db.write(lambda u: u.memory_decay_auto_tick(4, 2)) == 2
            assert await db.write(lambda u: u.memory_decay_auto_tick(4, 2)) == 1
            assert await db.write(lambda u: u.memory_decay_auto_tick(4, 2)) == 0

    asyncio.run(scenario())


def test_auto_tick_skips_disabled_agent(tmp_path):
    async def scenario():
        clock = Clock()
        clock.current = at_hour(5)
        async with opened(tmp_path / "instance", clock) as (_, db, _, agent):
            await db.write(lambda u: u.remember(agent, "fact"))
            await db.write(
                lambda u: u.connection.execute(update(s.agents).where(s.agents.c.id == agent).values(enabled=False))
            )
            assert await db.write(lambda u: u.memory_decay_auto_tick(4, 8)) == 0
            assert (await db.read(lambda u: u.memory_decay_state(agent)))["last_decay_date"] is None

    asyncio.run(scenario())


def test_auto_tick_respects_manual_pass_today(tmp_path):
    async def scenario():
        clock = Clock()
        clock.current = at_hour(5)
        async with opened(tmp_path / "instance", clock) as (_, db, _, agent):
            await db.write(lambda u: u.remember(agent, "fact"))
            await db.write(lambda u: u.memory_decay_pass(agent))
            assert await db.write(lambda u: u.memory_decay_auto_tick(4, 8)) == 0

    asyncio.run(scenario())


def test_auto_tick_protected_and_documents_never_fade(tmp_path):
    async def scenario():
        clock = Clock()
        clock.current = at_hour(5)
        async with opened(tmp_path / "instance", clock) as (_, db, _, agent):
            protected = await db.write(lambda u: u.remember(agent, "owner fact", importance=70))
            pinned = await db.write(lambda u: u.remember(agent, "pinned fact", importance=10, pinned=True))
            person = await db.write(lambda u: u.person_create(agent, "Owner"))
            document = await db.write(lambda u: u.remember(agent, "profile", kind="profile", person_id=person["id"]))
            assert await db.write(lambda u: u.memory_decay_auto_tick(4, 8)) == 1
            clock.current = at_hour(5) + dt.timedelta(days=400)
            assert await db.write(lambda u: u.memory_decay_auto_tick(4, 8)) == 1
            prot = await db.read(lambda u: u._one(s.memory_entries, agent, str(protected["id"])))
            pin = await db.read(lambda u: u._one(s.memory_entries, agent, str(pinned["id"])))
            doc = await db.read(lambda u: u._one(s.memory_entries, agent, str(document["id"])))
            assert prot["clarity"] != "forgotten" and prot["deleted_at"] is None
            assert pin["clarity"] == "vivid"
            assert doc["clarity"] == "clear" and doc["recall_count"] == 0

    asyncio.run(scenario())


def test_maintenance_tick_decay_disabled_by_default(tmp_path):
    async def scenario():
        clock = Clock()
        clock.current = at_hour(5)
        async with opened(tmp_path / "instance", clock) as (_, db, objects, agent):
            documents = DocumentService(db, objects)
            row = await db.write(lambda u: u.remember(agent, "fact"))
            service = MaintenanceService(db, documents, clock, Observation())
            assert await service.tick() == 0
            unchanged = await db.read(lambda u: u._one(s.memory_entries, agent, str(row["id"])))
            assert unchanged["clarity"] == "clear"
            assert (await db.read(lambda u: u.memory_decay_state(agent)))["last_decay_date"] is None

    asyncio.run(scenario())


def test_maintenance_tick_runs_daily_decay_and_idempotent(tmp_path):
    async def scenario():
        clock = Clock()
        clock.current = at_hour(5)
        async with opened(tmp_path / "instance", clock) as (_, db, objects, agent):
            documents = DocumentService(db, objects)
            row = await db.write(lambda u: u.remember(agent, "fact"))
            service = MaintenanceService(db, documents, clock, Observation(), decay_daily_hour=DECAY_DAILY_HOUR)
            assert await service.tick() == 1
            promoted = await db.read(lambda u: u._one(s.memory_entries, agent, str(row["id"])))
            assert promoted["clarity"] == "vivid"
            state = await db.read(lambda u: u.memory_decay_state(agent))
            assert state["last_decay_date"] == "2026-11-20" and state["decayed"] == 0
            assert await service.tick() == 0

    asyncio.run(scenario())


def test_production_wiring_enables_decay_tick_isolated_disables(tmp_path):
    from omni_test_support.v2.memory_semantics import NoModel

    from omni.composition.instance import open_instance

    async def scenario():
        isolated = await open_instance(tmp_path / "isolated", model=NoModel(), capabilities=())
        try:
            assert isolated.maintenance._decay_daily_hour is None
        finally:
            assert (await isolated.close())["complete"]
        production = await open_instance(tmp_path / "production", model=NoModel())
        try:
            assert production.maintenance._decay_daily_hour == DECAY_DAILY_HOUR
        finally:
            assert (await production.close())["complete"]

    asyncio.run(scenario())
