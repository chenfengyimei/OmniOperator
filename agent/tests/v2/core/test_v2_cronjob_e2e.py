"""K13 end-to-end: model-created jobs through approval, trigger, skill/context injection and lifecycle."""

import asyncio
import uuid

from omni_test_support.v2.execution import wait_state
from omni_test_support.v2.scheduling import Clock, interval
from omni_test_support.v2.skills import enable, markdown
from omni_test_support.v2.v2_configuration_helpers import model_instance
from sqlalchemy import select

from omni.adapters.persistence import schema as s
from omni.composition.instance import open_instance
from omni.domain.contracts import Invocation, ModelReply


class ScriptedModel:
    """Run replies are scripted; the read-only background-review channel never consumes them."""

    def __init__(self, replies=()):
        self.replies = list(replies)
        self.run_prompts = []

    async def complete(self, messages, tools, settings):
        if "cronjob_manage" not in {tool.name for tool in tools}:
            return ModelReply("no candidates", cost_micro=0)
        self.run_prompts.append(messages)
        if self.replies:
            return self.replies.pop(0)
        return ModelReply("done", cost_micro=0)


def call(arguments):
    return ModelReply("", (Invocation(str(uuid.uuid4()), "cronjob_manage", arguments),), cost_micro=0)


async def drive(instance, agent, run_id, approvals):
    for _ in range(approvals):
        await wait_state(instance.application, agent, run_id, {"waiting_approval"})
        approval = (await instance.application.approvals(agent))[0]
        assert approval["tool"] == "cronjob_manage"
        await instance.application.approve(agent, approval["id"], True, approval["policy_revision"])
    return await wait_state(instance.application, agent, run_id, {"succeeded", "failed", "interrupted", "cancelled"})


async def accepted_text(database, agent, run_id):
    run = await database.read(lambda u: u.get_run(agent, run_id))
    return await database.read(
        lambda u: u.connection.execute(
            select(s.messages.c.content).where(s.messages.c.id == run["input_message_id"])
        ).scalar_one()
    )


async def latest_run(instance, agent, schedule_id):
    rows = await instance.schedules.occurrences(agent, schedule_id)
    assert rows and rows[-1]["run_id"], "the schedule did not enqueue a run"
    return rows[-1]["run_id"]


async def wait_review_settled(instance, agent, run_id):
    """The schedule admission guard counts an in-flight post-turn review's reserved model call
    as unresolved work; wait for the real batch ledger to leave queued/running before triggering
    the next occurrence (production scheduler retries on its next tick)."""
    async with asyncio.timeout(10):
        while True:
            states = await instance._database.read(
                lambda u: list(
                    u.connection.execute(
                        select(s.background_review_batches.c.state).where(
                            s.background_review_batches.c.agent_id == agent,
                            s.background_review_batches.c.run_id == run_id,
                        )
                    ).scalars()
                )
            )
            if states and all(state not in {"queued", "running"} for state in states):
                return
            await asyncio.sleep(0.01)


def test_model_created_job_triggers_with_skills_and_redacted_context(tmp_path):
    async def scenario():
        clock = Clock()
        model = ScriptedModel()
        instance = await open_instance(tmp_path, model=model, clock=clock)
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            await model_instance(instance, agent)
            skill = await enable(instance, agent, await instance.skills.save(agent, markdown(name="schedule-skill")))
            source = await instance.schedules.save(agent, "source", True, interval("collect source"))
            model.replies.append(ModelReply("source output sk-abcdefghijklmnop", cost_micro=0))
            clock.advance(61)
            await instance.schedules.tick()
            source_run = await latest_run(instance, agent, source["id"])
            await wait_state(instance.application, agent, source_run, {"succeeded"})
            model.replies.extend(
                [
                    call(
                        {
                            "action": "create",
                            "schedule": "60s",
                            "prompt": "summarize",
                            "name": "consumer",
                            "skills": [skill["id"]],
                            "context_from": [source["id"]],
                            "continuity": True,
                        }
                    ),
                    ModelReply("created", cost_micro=0),
                ]
            )
            receipt = await instance.application.submit_turn(agent, None, "e2e-create", "create the consumer job")
            run = await drive(instance, agent, receipt["run_id"], 1)
            assert run["state"] == "succeeded"
            jobs = await instance.schedules.page(agent)
            consumer = next(job for job in jobs if job["name"] == "consumer")
            assert consumer["value"]["skills"] == [skill["id"]]
            clock.advance(61)
            await instance.schedules.tick()
            first_run = await latest_run(instance, agent, consumer["id"])
            first = await wait_state(
                instance.application, agent, first_run, {"succeeded", "failed", "interrupted", "cancelled"}
            )
            assert first["state"] == "succeeded", (first["state"], first.get("error_code"))
            prompt = await accepted_text(instance._database, agent, first_run)
            assert prompt.startswith("Scheduled job context (source): source output ")
            assert "sk-abcdefghijklmnop" not in prompt
            assert prompt.endswith("\n\nTask: summarize")
            activated = await instance._database.read(
                lambda u: list(
                    u.connection.execute(
                        select(s.run_skill_activations.c.skill_id).where(s.run_skill_activations.c.run_id == first_run)
                    ).scalars()
                )
            )
            assert activated == [skill["id"]]
            await wait_review_settled(instance, agent, first_run)
            clock.advance(61)
            await instance.schedules.tick()
            second_run = await latest_run(instance, agent, consumer["id"])
            second = await wait_state(
                instance.application, agent, second_run, {"succeeded", "failed", "interrupted", "cancelled"}
            )
            assert second["state"] == "succeeded", (second["state"], second.get("error_code"))
            prompt2 = await accepted_text(instance._database, agent, second_run)
            assert "Scheduled job context (consumer): done" in prompt2
            assert "Scheduled job context (source):" in prompt2
            assert prompt2.endswith("\n\nTask: summarize")
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_update_pause_resume_list_remove_all_pass_the_approval_gate(tmp_path):
    async def scenario():
        clock = Clock()
        model = ScriptedModel(
            [
                call({"action": "create", "schedule": "60s", "prompt": "lifecycle"}),
                ModelReply("created", cost_micro=0),
            ]
        )
        instance = await open_instance(tmp_path, model=model, clock=clock)
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            await model_instance(instance, agent)
            receipt = await instance.application.submit_turn(agent, None, "e2e-lifecycle", "create the job")
            run = await drive(instance, agent, receipt["run_id"], 1)
            assert run["state"] == "succeeded"
            job = (await instance.schedules.page(agent))[0]
            model.replies.extend(
                [
                    call({"action": "update", "job_id": job["id"], "revision": 1, "prompt": "changed"}),
                    call({"action": "pause", "job_id": job["id"], "revision": 2}),
                    call({"action": "resume", "job_id": job["id"], "revision": 3}),
                    call({"action": "list"}),
                    call({"action": "remove", "job_id": job["id"], "revision": 4}),
                    ModelReply("all done", cost_micro=0),
                ]
            )
            receipt2 = await instance.application.submit_turn(agent, None, "e2e-lifecycle-2", "manage the job")
            run2 = await drive(instance, agent, receipt2["run_id"], 5)
            assert run2["state"] == "succeeded"
            actions = await instance.application.query("actions", agent)
            assert len(actions) == 6 and all(a["tool"] == "cronjob_manage" and a["state"] == "ok" for a in actions)
            listed = next(a for a in actions if a["arguments"]["action"] == "list")
            assert listed["effect"] == "none"
            assert all(a["effect"] == "applied" for a in actions if a is not listed)
            assert await instance.schedules.page(agent) == []
            assert await instance.schedules.occurrences(agent, job["id"]) == []
            assert (await instance.schedules.get(agent, job["id"]))["deleted_at"]
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_stale_revision_conflict_is_visible_and_list_reconciles(tmp_path):
    async def scenario():
        clock = Clock()
        model = ScriptedModel()
        instance = await open_instance(tmp_path, model=model, clock=clock)
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            await model_instance(instance, agent)
            model.replies.extend(
                [
                    call({"action": "create", "schedule": "60s", "prompt": "guarded"}),
                    ModelReply("created", cost_micro=0),
                ]
            )
            receipt = await instance.application.submit_turn(agent, None, "e2e-guard", "create then conflict")
            run = await drive(instance, agent, receipt["run_id"], 1)
            assert run["state"] == "succeeded"
            job = (await instance.schedules.page(agent))[0]
            model.replies.extend(
                [
                    call({"action": "update", "job_id": job["id"], "revision": 99, "prompt": "stale"}),
                    call({"action": "list"}),
                    ModelReply("reconciled", cost_micro=0),
                ]
            )
            receipt2 = await instance.application.submit_turn(agent, None, "e2e-conflict", "stale update")
            run2 = await drive(instance, agent, receipt2["run_id"], 2)
            assert run2["state"] == "succeeded"
            actions = await instance.application.query("actions", agent)
            conflict = next(a for a in actions if a["arguments"].get("revision") == 99)
            assert conflict["state"] == "error" and conflict["effect"] == "none"
            assert conflict["result"]["error_code"] == "revision_conflict"
            current = await instance.schedules.get(agent, job["id"])
            assert current["revision"] == 1 and current["value"]["prompt"] == "guarded"
        finally:
            await instance.close()

    asyncio.run(scenario())
