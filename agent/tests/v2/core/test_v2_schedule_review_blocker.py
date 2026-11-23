"""Scheduling blocker scope: auxiliary summary calls never hold the next occurrence back."""

from __future__ import annotations

import asyncio

from omni_test_support.v2.scheduling import interval, setup
from sqlalchemy import update

from omni.adapters.persistence import schema as s


async def enqueue(service, agent, clock, schedule_id, seconds=60):
    """Advance the clock, run one tick and return the run enqueued for one schedule."""
    clock.advance(seconds)
    await service.tick()
    rows = await service.occurrences(agent, schedule_id)
    assert rows and rows[-1]["run_id"], "the schedule did not enqueue a run"
    return rows[-1]["run_id"]


async def succeed(db, agent, run_id, text="scheduled output"):
    """Drive one queued run to succeeded through the real claim/transition/finish path."""
    await db.write(lambda u: u.claim_runs(8))
    await db.write(lambda u: u.transition(agent, run_id, "running"))
    await db.write(lambda u: u.finish(agent, run_id, text))


async def reserve(db, agent, run_id, purpose):
    return await db.write(lambda u: u.reserve_call(agent, run_id, 1, 10**9, "fixture", purpose=purpose))


async def call_state(db, call_id, state):
    await db.write(
        lambda u: u.connection.execute(update(s.model_calls).where(s.model_calls.c.id == call_id).values(state=state))
    )


async def blocker(db, agent, schedule_id):
    return await db.read(lambda u: u.schedule_blocker(agent, schedule_id))


def test_reserved_summary_call_does_not_block_the_next_occurrence(tmp_path):
    async def scenario():
        db, agent, clock, service = await setup(tmp_path)
        try:
            job = await service.save(agent, "nightly", True, interval())
            first = await enqueue(service, agent, clock, job["id"])
            await succeed(db, agent, first)
            await reserve(db, agent, first, "summary")

            assert await blocker(db, agent, job["id"]) is None
            # The next occurrence is admitted while the review-time reservation is still open.
            second = await enqueue(service, agent, clock, job["id"])
            await succeed(db, agent, second)
            assert await blocker(db, agent, job["id"]) is None
            assert len(await service.occurrences(agent, job["id"])) == 2
        finally:
            await db.close()

    asyncio.run(scenario())


def test_unknown_summary_call_does_not_block_after_a_crash(tmp_path):
    async def scenario():
        db, agent, clock, service = await setup(tmp_path)
        try:
            job = await service.save(agent, "nightly", True, interval())
            run = await enqueue(service, agent, clock, job["id"])
            await succeed(db, agent, run)
            call_id = await reserve(db, agent, run, "summary")
            await call_state(db, call_id, "unknown")

            assert await blocker(db, agent, job["id"]) is None
            await enqueue(service, agent, clock, job["id"])
        finally:
            await db.close()

    asyncio.run(scenario())


def test_reserved_turn_call_still_blocks_the_next_occurrence(tmp_path):
    async def scenario():
        db, agent, clock, service = await setup(tmp_path)
        try:
            job = await service.save(agent, "nightly", True, interval())
            run = await enqueue(service, agent, clock, job["id"])
            await succeed(db, agent, run)
            await reserve(db, agent, run, "turn")

            blocked = await blocker(db, agent, job["id"])
            assert blocked is not None and blocked["reason"] == "unknown"
            assert [item["kind"] for item in blocked["items"]] == ["model_calls"]

            clock.advance(60)
            assert await service.tick() == 0
            row = await service.get(agent, job["id"])
            assert row["progress"]["error_code"] == "previous_outcome_unknown"
        finally:
            await db.close()

    asyncio.run(scenario())


def test_unknown_turn_call_still_blocks_until_reviewed(tmp_path):
    async def scenario():
        db, agent, clock, service = await setup(tmp_path)
        try:
            job = await service.save(agent, "nightly", True, interval())
            run = await enqueue(service, agent, clock, job["id"])
            await succeed(db, agent, run)
            call_id = await reserve(db, agent, run, "turn")
            await call_state(db, call_id, "unknown")

            blocked = await blocker(db, agent, job["id"])
            assert blocked is not None and blocked["reason"] == "unknown"

            # An owner review of the exact occurrence clears the turn-call blocker.
            fingerprint = blocked["fingerprint"]
            await service.review(agent, job["id"], blocked["id"], fingerprint)
            assert await blocker(db, agent, job["id"]) is None
        finally:
            await db.close()

    asyncio.run(scenario())


def test_summary_reservation_never_hides_an_open_turn_call(tmp_path):
    async def scenario():
        db, agent, clock, service = await setup(tmp_path)
        try:
            job = await service.save(agent, "nightly", True, interval())
            run = await enqueue(service, agent, clock, job["id"])
            await succeed(db, agent, run)
            await reserve(db, agent, run, "summary")
            await reserve(db, agent, run, "turn")

            blocked = await blocker(db, agent, job["id"])
            assert blocked is not None and blocked["reason"] == "unknown"
            assert [item["kind"] for item in blocked["items"]] == ["model_calls"]
        finally:
            await db.close()

    asyncio.run(scenario())
