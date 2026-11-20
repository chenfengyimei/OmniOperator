"""K13 trigger chain: scheduled-output injection, redaction and skill binding at accept time."""

import asyncio

from omni_test_support.v2.scheduling import interval, seed_skill, setup
from sqlalchemy import select

from omni.adapters.diagnostics import Redactor
from omni.adapters.persistence import schema as s
from omni.domain.scheduling import CONTEXT_MISSING, CONTEXT_UNAVAILABLE, EXCERPT_CHARS, EXCERPT_MARKER


async def enqueue(service, agent, clock, schedule_id, seconds=60):
    """Advance the clock, run one tick and return the run enqueued for one schedule."""
    clock.advance(seconds)
    await service.tick()
    rows = await service.occurrences(agent, schedule_id)
    assert rows and rows[-1]["run_id"], "the schedule did not enqueue a run"
    return rows[-1]["run_id"]


async def succeed(db, agent, run_id, text):
    """Drive one queued run to succeeded with a finished assistant message."""
    await db.write(lambda u: u.claim_runs(8))
    await db.write(lambda u: u.transition(agent, run_id, "running"))
    await db.write(lambda u: u.finish(agent, run_id, text))


async def accepted_text(db, agent, run_id):
    """Read the prompt the run was accepted with, from its persisted user message."""
    run = await db.read(lambda u: u.get_run(agent, run_id))
    return await db.read(
        lambda u: u.connection.execute(
            select(s.messages.c.content).where(s.messages.c.id == run["input_message_id"])
        ).scalar_one()
    )


def test_context_injects_only_the_latest_succeeded_output(tmp_path):
    async def scenario():
        db, agent, clock, service = await setup(tmp_path)
        try:
            source = await service.save(agent, "collector", True, interval("collect links"))
            await succeed(db, agent, await enqueue(service, agent, clock, source["id"], 601), "first batch")
            await succeed(db, agent, await enqueue(service, agent, clock, source["id"]), "second batch")
            consumer = await service.save(
                agent, "summary", True, {**interval("summarize"), "context_from": [source["id"]]}
            )
            prompt = await accepted_text(db, agent, await enqueue(service, agent, clock, consumer["id"]))
            assert prompt == "Scheduled job context (collector): second batch\n\nTask: summarize"
        finally:
            await db.close()

    asyncio.run(scenario())


def test_context_ignores_runs_that_did_not_succeed(tmp_path):
    async def scenario():
        db, agent, clock, service = await setup(tmp_path)
        try:
            source = await service.save(agent, "collector", True, interval("collect links"))
            await succeed(db, agent, await enqueue(service, agent, clock, source["id"], 601), "kept output")
            failed = await enqueue(service, agent, clock, source["id"])
            await db.write(lambda u: u.cancel(agent, failed))
            consumer = await service.save(
                agent, "summary", True, {**interval("summarize"), "context_from": [source["id"]]}
            )
            prompt = await accepted_text(db, agent, await enqueue(service, agent, clock, consumer["id"]))
            assert prompt == "Scheduled job context (collector): kept output\n\nTask: summarize"
        finally:
            await db.close()

    asyncio.run(scenario())


def test_context_redacts_before_truncating(tmp_path):
    async def scenario():
        redactor = Redactor()
        redactor.register("runtime-credential-value")
        db, agent, clock, service = await setup(tmp_path, redact=redactor.text)
        try:
            source = await service.save(agent, "collector", True, interval("collect links"))
            output = "sk-abcdefghijklmnop runtime-credential-value " + "y" * (EXCERPT_CHARS * 2)
            await succeed(db, agent, await enqueue(service, agent, clock, source["id"], 601), output)
            consumer = await service.save(
                agent, "summary", True, {**interval("summarize"), "context_from": [source["id"]]}
            )
            prompt = await accepted_text(db, agent, await enqueue(service, agent, clock, consumer["id"]))
            head, _, tail = prompt.partition("\n\nTask: ")
            assert tail == "summarize"
            assert head.startswith("Scheduled job context (collector): ")
            value = head.removeprefix("Scheduled job context (collector): ")
            assert "sk-abcdefghijklmnop" not in value and "runtime-credential-value" not in value
            assert value.endswith(EXCERPT_MARKER)
            assert len(value) == EXCERPT_CHARS + len(EXCERPT_MARKER)
        finally:
            await db.close()

    asyncio.run(scenario())


def test_context_marks_missing_and_archived_references(tmp_path):
    async def scenario():
        db, agent, clock, service = await setup(tmp_path)
        try:
            silent = await service.save(agent, "never-ran", True, interval("never"))
            retired = await service.save(agent, "retired", True, interval("retired"))
            consumer = await service.save(
                agent,
                "merge",
                True,
                {**interval("combine"), "context_from": [silent["id"], retired["id"]]},
            )
            await service.archive(agent, retired["id"], retired["revision"])
            prompt = await accepted_text(db, agent, await enqueue(service, agent, clock, consumer["id"]))
            assert prompt == (
                f"Scheduled job context (never-ran): {CONTEXT_MISSING}\n"
                f"Scheduled job context (retired): {CONTEXT_UNAVAILABLE}\n\nTask: combine"
            )
        finally:
            await db.close()

    asyncio.run(scenario())


def test_continuity_reads_the_jobs_own_previous_output(tmp_path):
    async def scenario():
        db, agent, clock, service = await setup(tmp_path)
        try:
            job = await service.save(agent, "digest", True, {**interval("collect"), "continuity": True})
            await succeed(db, agent, await enqueue(service, agent, clock, job["id"], 601), "yesterday digest")
            prompt = await accepted_text(db, agent, await enqueue(service, agent, clock, job["id"]))
            assert prompt == "Scheduled job context (digest): yesterday digest\n\nTask: collect"
        finally:
            await db.close()

    asyncio.run(scenario())


def test_plain_schedule_keeps_prompt_and_activates_bound_skills(tmp_path):
    async def scenario():
        db, agent, clock, service = await setup(tmp_path, platform="linux")
        try:
            skill = await seed_skill(db, agent, "schedule-skill")
            job = await service.save(agent, "skill job", True, {**interval("plain task"), "skills": [skill["id"]]})
            run_id = await enqueue(service, agent, clock, job["id"], 601)
            assert await accepted_text(db, agent, run_id) == "plain task"
            activated = await db.read(
                lambda u: (
                    u.connection.execute(
                        select(s.run_skill_activations.c.skill_id).where(s.run_skill_activations.c.run_id == run_id)
                    )
                    .scalars()
                    .all()
                )
            )
            assert list(activated) == [skill["id"]]
        finally:
            await db.close()

    asyncio.run(scenario())
