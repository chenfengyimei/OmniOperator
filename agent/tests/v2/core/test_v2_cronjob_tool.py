"""K13 model tool batch: cronjob_manage contract, thin scheduling adapter and the write approval gate."""

import asyncio
import uuid
from contextlib import nullcontext

import pytest
from jsonschema import Draft202012Validator
from omni_test_support.v2.execution import configure, wait_state
from omni_test_support.v2.scheduling import Clock, interval, seed_skill, setup
from sqlalchemy import update

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.database import Database
from omni.adapters.primitives import UUIDs
from omni.adapters.timezones import PinnedTimezones
from omni.adapters.token_counter import ConservativeTokenCounter
from omni.application.context import ContextService
from omni.application.cronjob import CronjobCapabilities
from omni.application.engine import Application, Capability
from omni.application.model_calls import ModelCalls
from omni.application.scheduling import SchedulingService
from omni.composition.instance import open_instance
from omni.domain.contracts import Effect, Invocation, InvocationContext, ModelReply, Outcome, ToolSpec
from omni.domain.cronjob import (
    ACTIONS,
    CRONJOB_MANAGE,
    PROMPT_PREVIEW,
    compile_schedule,
    job_projection,
    parse_request,
)
from omni.domain.routing import RETRIEVAL_TOOLS, select_tools
from omni.domain.scheduling import due_selection

NOW = "2026-11-03T00:00:00.000000+00:00"
JOB = "11111111-1111-4111-8111-111111111111"


def context(agent):
    return InvocationContext(agent, str(uuid.uuid4()), str(uuid.uuid4()), 1)


async def invoke(capability, agent, arguments):
    return await capability.invoke("cronjob_manage", arguments, operation_id=str(uuid.uuid4()), context=context(agent))


def test_spec_is_a_bounded_write_tool_outside_read_tiers():
    assert CRONJOB_MANAGE.name == "cronjob_manage"
    assert CRONJOB_MANAGE.writes is True
    assert CRONJOB_MANAGE.resource == "schedules"
    assert CRONJOB_MANAGE.timeout_s == 30
    parameters = CRONJOB_MANAGE.parameters
    assert parameters["additionalProperties"] is False
    assert parameters["required"] == ["action"]
    assert parameters["properties"]["action"]["enum"] == list(ACTIONS)
    assert ACTIONS == ("create", "list", "update", "pause", "resume", "remove")
    assert "cronjob_manage" not in RETRIEVAL_TOOLS
    assert select_tools("chat", (CRONJOB_MANAGE,), escalate=False) == ()
    assert select_tools("retrieval", (CRONJOB_MANAGE,), escalate=False) == ()
    assert select_tools("agent", (CRONJOB_MANAGE,), escalate=False) == (CRONJOB_MANAGE,)


@pytest.mark.parametrize(
    "text,kind,seconds",
    [
        ("0 9 * * *", "cron", None),
        ("*/15 9-17 * * 1-5", "cron", None),
        ("30m", "interval", 1800),
        ("90s", "interval", 90),
        ("2h", "interval", 7200),
        ("1d", "interval", 86400),
        ("60s", "interval", 60),
        ("365d", "interval", 31536000),
        ("every 30m", "interval", 1800),
        ("every 2 hours", "interval", 7200),
        ("every 1 day", "interval", 86400),
    ],
)
def test_compile_accepts_decidable_forms(text, kind, seconds):
    value = compile_schedule(text, NOW)
    assert value["kind"] == kind
    assert value["interval_seconds"] == seconds
    assert value["start_at"] is None
    if kind == "cron":
        assert value["expression"] == text
    else:
        assert value["expression"] == ""


@pytest.mark.parametrize(
    "text,code",
    [
        ("every monday 9am", "invalid_schedule"),
        ("weekdays at 9am", "invalid_schedule"),
        ("", "invalid_schedule"),
        ("   ", "invalid_schedule"),
        ("tomorrow at 9", "invalid_schedule"),
        ("59s", "invalid_schedule"),
        ("0m", "invalid_schedule"),
        ("366d", "invalid_schedule"),
        ("31536001s", "invalid_schedule"),
        ("30x", "invalid_schedule"),
        ("-30m", "invalid_schedule"),
        ("in 0s", "invalid_schedule"),
        ("in", "invalid_schedule"),
        ("in monday", "invalid_schedule"),
        ("0 9 * *", "invalid_schedule"),
        ("60 9 * * *", "invalid_schedule"),
        ("2026-12-01T09:00:00", "invalid_schedule_time"),
    ],
)
def test_compile_rejects_undecidable_or_out_of_bounds(text, code):
    with pytest.raises(Exception) as error:
        compile_schedule(text, NOW)
    assert error.value.code == code and error.value.status == 422


def test_relative_and_absolute_one_shots_resolve_on_the_server_clock():
    assert compile_schedule("in 30m", NOW) == {
        "kind": "once",
        "expression": "",
        "interval_seconds": None,
        "start_at": "2026-11-03T00:30:00.000000+00:00",
    }
    assert compile_schedule("in 2h", NOW)["start_at"] == "2026-11-03T02:00:00.000000+00:00"
    assert compile_schedule("2026-12-01T09:00:00+08:00", NOW)["start_at"] == "2026-12-01T01:00:00.000000+00:00"


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"action": "run"},
        {"action": "create"},
        {"action": "create", "schedule": "30m"},
        {"action": "update", "job_id": JOB, "revision": 1},
        {"action": "pause", "job_id": JOB},
        {"action": "resume", "job_id": JOB, "revision": 0},
        {"action": "remove", "job_id": "not-a-uuid", "revision": 1},
        {"action": "list", "limit": 0},
        {"action": "list", "limit": 51},
        {"action": "list", "limit": True},
        {"action": "list", "after": "not-a-uuid"},
        {"action": "create", "schedule": "30m", "prompt": "x", "continuity": "yes"},
        {"action": "create", "schedule": "30m", "prompt": "x", "skills": "s1"},
        {"action": "update", "job_id": JOB, "revision": True, "prompt": "x"},
    ],
)
def test_parse_requires_action_fields_and_strict_types(arguments):
    with pytest.raises(Exception) as error:
        parse_request(arguments)
    assert error.value.code == "invalid_tool_arguments" and error.value.status == 422


def test_parse_keeps_omitted_and_cleared_bindings_distinct():
    created = parse_request({"action": "create", "schedule": "30m", "prompt": "collect"})
    assert created.skills is None and created.context_from is None and created.continuity is None
    assert created.limit == 50 and created.revision == 0
    cleared = parse_request(
        {"action": "update", "job_id": JOB, "revision": 2, "skills": [], "context_from": [], "continuity": False}
    )
    assert cleared.skills == () and cleared.context_from == () and cleared.continuity is False
    assert cleared.action == "update" and cleared.revision == 2


@pytest.mark.parametrize(
    "extra",
    [
        {"action": "run"},
        {"deliver": True},
        {"failure_deliver": True},
        {"repeat": 3},
        {"script": "print(1)"},
        {"monitor": "https://example.invalid"},
        {"no_agent": True},
        {"pinned": True},
        {"model": "gpt-4o"},
        {"provider": "other"},
        {"base_url": "https://example.invalid"},
        {"enabled_toolsets": ["files"]},
        {"workdir": "/tmp"},
        {"attach_to_session": JOB},
        {"skills": [JOB] * 5},
        {"context_from": [JOB] * 5},
        {"prompt": "x" * 20001},
        {"name": "x" * 101},
    ],
)
def test_schema_rejects_reference_only_actions_and_fields(extra):
    validator = Draft202012Validator(CRONJOB_MANAGE.parameters)
    arguments = {"action": "create", "schedule": "30m", "prompt": "x", **extra}
    assert not validator.is_valid(arguments)


def test_projection_bounds_and_last_occurrence():
    record = {
        "id": JOB,
        "name": "digest",
        "enabled": True,
        "revision": 3,
        "value": {
            "kind": "interval",
            "timezone": "UTC",
            "expression": "",
            "interval_seconds": 1800,
            "start_at": "2026-11-03T00:30:00.000000+00:00",
            "session_id": "private-session",
            "prompt": "p" * 300,
            "misfire": "latest",
            "skills": ["s1"],
            "context_from": ["c1"],
            "continuity": True,
        },
        "progress": {"next_at": "2026-11-03T01:00:00.000000+00:00", "last_scheduled_at": None, "sequence": 2},
        "tzdb_version": "2025b",
        "blocker": None,
    }
    projection = job_projection(record, {"state": "enqueued", "run_state": "succeeded", "error_code": ""})
    assert projection["job_id"] == JOB and projection["revision"] == 3
    assert projection["prompt_preview"] == "p" * PROMPT_PREVIEW
    assert projection["skills"] == ["s1"] and projection["context_from"] == ["c1"]
    assert projection["continuity"] is True and projection["interval_seconds"] == 1800
    assert projection["last_occurrence"] == {"state": "succeeded", "error_code": ""}
    assert "session_id" not in projection and "prompt" not in projection and "tzdb_version" not in projection
    assert job_projection(record, None)["last_occurrence"] is None
    blocked = job_projection(record, {"state": "blocked", "run_state": None, "error_code": "accept_rejected"})
    assert blocked["last_occurrence"] == {"state": "blocked", "error_code": "accept_rejected"}


def test_create_persists_job_with_default_name_and_projection(tmp_path):
    async def scenario():
        db, agent, clock, service = await setup(tmp_path)
        capability = CronjobCapabilities(service, clock)
        try:
            prompt = "collect links " * 30
            result = await invoke(capability, agent, {"action": "create", "schedule": "30m", "prompt": prompt})
            assert result.status == Outcome.OK and result.effect == Effect.APPLIED
            job_id = result.data["job_id"]
            row = await service.get(agent, job_id)
            assert row["name"] == prompt.strip()[:50]
            assert row["enabled"] is True and row["revision"] == 1
            assert row["value"]["kind"] == "interval" and row["value"]["interval_seconds"] == 1800
            assert row["progress"]["next_at"] == "2026-11-03T00:30:00.000000+00:00"
            assert result.data["prompt_preview"] == prompt[:PROMPT_PREVIEW]
            assert "session_id" not in result.data
        finally:
            await db.close()

    asyncio.run(scenario())


def test_create_with_explicit_fields_binds_skills_and_context(tmp_path):
    async def scenario():
        db, agent, clock, service = await setup(tmp_path)
        capability = CronjobCapabilities(service, clock)
        try:
            skill = await seed_skill(db, agent)
            source = await service.save(agent, "source", True, interval("source task"))
            result = await invoke(
                capability,
                agent,
                {
                    "action": "create",
                    "schedule": "0 9 * * *",
                    "prompt": "bound prompt",
                    "name": "bound job",
                    "timezone": "UTC",
                    "skills": [skill["id"]],
                    "context_from": [source["id"]],
                    "continuity": True,
                },
            )
            assert result.status == Outcome.OK and result.effect == Effect.APPLIED
            row = await service.get(agent, result.data["job_id"])
            assert row["name"] == "bound job" and row["value"]["kind"] == "cron"
            assert row["value"]["skills"] == [skill["id"]]
            assert row["value"]["context_from"] == [source["id"]]
            assert row["value"]["continuity"] is True
        finally:
            await db.close()

    asyncio.run(scenario())


def test_list_paginates_with_bounded_projection(tmp_path):
    async def scenario():
        db, agent, clock, service = await setup(tmp_path)
        capability = CronjobCapabilities(service, clock)
        try:
            for index in range(3):
                created = await invoke(
                    capability, agent, {"action": "create", "schedule": "30m", "prompt": f"job {index} " + "x" * 300}
                )
                assert created.status == Outcome.OK
            first = await invoke(capability, agent, {"action": "list", "limit": 2})
            assert first.status == Outcome.OK and first.effect == Effect.NONE
            assert len(first.data["jobs"]) == 2
            second = await invoke(
                capability, agent, {"action": "list", "limit": 2, "after": first.data["jobs"][-1]["job_id"]}
            )
            assert len(second.data["jobs"]) == 1
            ids = [job["job_id"] for job in first.data["jobs"] + second.data["jobs"]]
            assert len(set(ids)) == 3
            for job in first.data["jobs"]:
                assert len(job["prompt_preview"]) == PROMPT_PREVIEW
                assert "session_id" not in job and "prompt" not in job and "tzdb_version" not in job
                assert job["revision"] == 1 and job["skills"] == [] and job["continuity"] is False
            empty = await invoke(capability, agent, {"action": "list", "after": second.data["jobs"][-1]["job_id"]})
            assert empty.data["jobs"] == [] and empty.text == "No scheduled jobs"
        finally:
            await db.close()

    asyncio.run(scenario())


def test_update_patch_keeps_omitted_and_clears_empty(tmp_path):
    async def scenario():
        db, agent, clock, service = await setup(tmp_path)
        capability = CronjobCapabilities(service, clock)
        try:
            skill = await seed_skill(db, agent)
            source = await service.save(agent, "source", True, interval("source task"))
            created = await invoke(
                capability,
                agent,
                {
                    "action": "create",
                    "schedule": "30m",
                    "prompt": "original",
                    "skills": [skill["id"]],
                    "context_from": [source["id"]],
                    "continuity": True,
                },
            )
            job_id = created.data["job_id"]
            patched = await invoke(
                capability, agent, {"action": "update", "job_id": job_id, "revision": 1, "prompt": "changed"}
            )
            assert patched.status == Outcome.OK and patched.effect == Effect.APPLIED
            row = await service.get(agent, job_id)
            assert row["revision"] == 2 and row["value"]["prompt"] == "changed"
            assert row["value"]["skills"] == [skill["id"]]
            assert row["value"]["context_from"] == [source["id"]] and row["value"]["continuity"] is True
            cleared = await invoke(
                capability,
                agent,
                {
                    "action": "update",
                    "job_id": job_id,
                    "revision": 2,
                    "skills": [],
                    "context_from": [],
                    "continuity": False,
                },
            )
            assert cleared.status == Outcome.OK
            row = await service.get(agent, job_id)
            assert row["revision"] == 3
            assert row["value"]["skills"] == [] and row["value"]["context_from"] == []
            assert row["value"]["continuity"] is False
        finally:
            await db.close()

    asyncio.run(scenario())


def test_update_schedule_recomputes_next_at(tmp_path):
    async def scenario():
        db, agent, clock, service = await setup(tmp_path)
        capability = CronjobCapabilities(service, clock)
        try:
            created = await invoke(capability, agent, {"action": "create", "schedule": "30m", "prompt": "retime"})
            job_id = created.data["job_id"]
            updated = await invoke(
                capability, agent, {"action": "update", "job_id": job_id, "revision": 1, "schedule": "0 9 * * *"}
            )
            assert updated.status == Outcome.OK
            row = await service.get(agent, job_id)
            assert row["value"]["kind"] == "cron" and row["value"]["expression"] == "0 9 * * *"
            assert row["value"]["interval_seconds"] is None
            assert row["progress"]["next_at"] == "2026-11-03T09:00:00.000000+00:00"
        finally:
            await db.close()

    asyncio.run(scenario())


def test_update_conflict_and_missing_job(tmp_path):
    async def scenario():
        db, agent, clock, service = await setup(tmp_path)
        capability = CronjobCapabilities(service, clock)
        try:
            created = await invoke(capability, agent, {"action": "create", "schedule": "30m", "prompt": "guard"})
            job_id = created.data["job_id"]
            conflict = await invoke(
                capability, agent, {"action": "update", "job_id": job_id, "revision": 99, "prompt": "stale"}
            )
            assert conflict.status == Outcome.ERROR and conflict.error_code == "revision_conflict"
            assert conflict.effect == Effect.NONE
            row = await service.get(agent, job_id)
            assert row["revision"] == 1 and row["value"]["prompt"] == "guard"
            missing = await invoke(
                capability, agent, {"action": "update", "job_id": str(uuid.uuid4()), "revision": 1, "prompt": "x"}
            )
            assert missing.status == Outcome.ERROR and missing.error_code == "not_found"
        finally:
            await db.close()

    asyncio.run(scenario())


def test_pause_and_resume_flip_enabled_without_backfill(tmp_path):
    async def scenario():
        db, agent, clock, service = await setup(tmp_path)
        capability = CronjobCapabilities(service, clock)
        try:
            created = await invoke(capability, agent, {"action": "create", "schedule": "30m", "prompt": "toggle"})
            job_id = created.data["job_id"]
            paused = await invoke(capability, agent, {"action": "pause", "job_id": job_id, "revision": 1})
            assert paused.status == Outcome.OK and paused.effect == Effect.APPLIED
            row = await service.get(agent, job_id)
            assert row["enabled"] is False and row["progress"]["next_at"] is None
            stale = await invoke(capability, agent, {"action": "pause", "job_id": job_id, "revision": 1})
            assert stale.error_code == "revision_conflict"
            clock.advance(3600)
            resumed = await invoke(capability, agent, {"action": "resume", "job_id": job_id, "revision": 2})
            assert resumed.status == Outcome.OK
            row = await service.get(agent, job_id)
            assert row["enabled"] is True
            assert row["progress"]["next_at"] == "2026-11-03T01:30:00.000000+00:00"
        finally:
            await db.close()

    asyncio.run(scenario())


def test_remove_archives_and_cancels_pending_occurrence(tmp_path):
    async def scenario():
        db, agent, clock, service = await setup(tmp_path)
        capability = CronjobCapabilities(service, clock)
        try:
            created = await invoke(capability, agent, {"action": "create", "schedule": "60s", "prompt": "retire"})
            job_id = created.data["job_id"]
            clock.advance(61)
            captured = await service.get(agent, job_id)
            zones = PinnedTimezones()
            selection = due_selection(
                captured["value"], str(captured["progress"]["next_at"]), clock.now(), zones.resolve("UTC")
            )
            occurrence = await db.write(lambda u: u.schedule_materialize(captured, selection, zones.version))
            assert occurrence is not None and occurrence["state"] == "pending"
            removed = await invoke(capability, agent, {"action": "remove", "job_id": job_id, "revision": 1})
            assert removed.status == Outcome.OK and removed.effect == Effect.APPLIED
            rows = await service.occurrences(agent, job_id)
            assert rows[-1]["state"] == "cancelled" and rows[-1]["error_code"] == "schedule_archived"
            assert (await service.get(agent, job_id))["deleted_at"]
            listed = await invoke(capability, agent, {"action": "list"})
            assert listed.data["jobs"] == []
            again = await invoke(capability, agent, {"action": "remove", "job_id": job_id, "revision": 1})
            assert again.error_code == "revision_conflict"
        finally:
            await db.close()

    asyncio.run(scenario())


def test_enabled_cap_rejects_create(tmp_path):
    async def scenario():
        db, agent, clock, service = await setup(tmp_path)
        capability = CronjobCapabilities(service, clock)
        try:
            for index in range(256):
                await service.save(agent, f"seed {index}", True, interval("seed"))
            result = await invoke(capability, agent, {"action": "create", "schedule": "30m", "prompt": "overflow"})
            assert result.status == Outcome.ERROR and result.error_code == "schedule_limit"
            assert result.effect == Effect.NONE
        finally:
            await db.close()

    asyncio.run(scenario())


def test_binding_errors_surface_as_error_results(tmp_path):
    async def scenario():
        db, agent, clock, service = await setup(tmp_path)
        capability = CronjobCapabilities(service, clock)
        try:
            bad_skill = await invoke(
                capability,
                agent,
                {"action": "create", "schedule": "30m", "prompt": "x", "skills": [str(uuid.uuid4())]},
            )
            assert bad_skill.error_code == "invalid_skill"
            bad_ref = await invoke(
                capability,
                agent,
                {"action": "create", "schedule": "30m", "prompt": "x", "context_from": [str(uuid.uuid4())]},
            )
            assert bad_ref.error_code == "invalid_context_ref"
            first = await invoke(capability, agent, {"action": "create", "schedule": "30m", "prompt": "a"})
            second = await invoke(
                capability,
                agent,
                {"action": "create", "schedule": "30m", "prompt": "b", "context_from": [first.data["job_id"]]},
            )
            cycle = await invoke(
                capability,
                agent,
                {
                    "action": "update",
                    "job_id": first.data["job_id"],
                    "revision": 1,
                    "context_from": [second.data["job_id"]],
                },
            )
            assert cycle.error_code == "invalid_context_cycle"
            assert (await service.get(agent, first.data["job_id"]))["value"]["context_from"] == []
        finally:
            await db.close()

    asyncio.run(scenario())


def test_syntax_and_past_errors_map_to_codes(tmp_path):
    async def scenario():
        db, agent, clock, service = await setup(tmp_path)
        capability = CronjobCapabilities(service, clock)
        try:
            natural = await invoke(
                capability, agent, {"action": "create", "schedule": "every monday 9am", "prompt": "x"}
            )
            assert natural.error_code == "invalid_schedule"
            naive = await invoke(
                capability, agent, {"action": "create", "schedule": "2026-12-01T09:00:00", "prompt": "x"}
            )
            assert naive.error_code == "invalid_schedule_time"
            past = await invoke(
                capability, agent, {"action": "create", "schedule": "2020-01-01T00:00:00+00:00", "prompt": "x"}
            )
            assert past.error_code == "schedule_in_past"
            assert await service.page(agent) == []
        finally:
            await db.close()

    asyncio.run(scenario())


def test_unknown_tool_name_is_not_found(tmp_path):
    async def scenario():
        db, agent, clock, service = await setup(tmp_path)
        capability = CronjobCapabilities(service, clock)
        try:
            result = await capability.invoke(
                "cronjob_run", {"action": "list"}, operation_id="op", context=context(agent)
            )
            assert result.status == Outcome.ERROR and result.error_code == "tool_not_found"
        finally:
            await db.close()

    asyncio.run(scenario())


class _Observer:
    def context(self, **fields):
        return nullcontext()

    def span(self, name):
        return nullcontext()


class _Model:
    def __init__(self, arguments):
        self.arguments = arguments
        self.tool_lists = []

    async def complete(self, messages, tools, settings):
        self.tool_lists.append([tool.name for tool in tools])
        if messages[-1]["role"] == "tool":
            return ModelReply("done", cost_micro=0)
        return ModelReply("", (Invocation(str(uuid.uuid4()), "cronjob_manage", self.arguments),), cost_micro=0)


def _cancel(database):
    async def cancel(agent_id, run_id):
        await database.write(lambda u: u.cancel(agent_id, run_id))

    return cancel


async def engine_setup(tmp_path, arguments):
    clock = Clock()
    database = Database(tmp_path / "omni.db", clock, UUIDs())
    model = _Model(arguments)
    calls = ModelCalls(database, model, _Observer(), ConservativeTokenCounter())
    app = Application(database, calls, ContextService(database, calls), (), clock, _Observer())
    schedules = SchedulingService(
        database, clock, PinnedTimezones(), lambda: None, _cancel(database), "linux", redact=lambda text: text
    )
    app.attach(Capability(CRONJOB_MANAGE, CronjobCapabilities(schedules, clock), lambda: True))
    await app.start()
    agent = await configure(app, database)
    return app, database, agent, schedules, model


def test_attach_rejects_duplicate_reserved_and_late(tmp_path):
    async def scenario():
        clock = Clock()
        database = Database(tmp_path / "omni.db", clock, UUIDs())
        calls = ModelCalls(database, _Model({}), _Observer(), ConservativeTokenCounter())
        schedules = SchedulingService(
            database, clock, PinnedTimezones(), lambda: None, _cancel(database), "linux", redact=lambda text: text
        )
        adapter = CronjobCapabilities(schedules, clock)
        app = Application(
            database,
            calls,
            ContextService(database, calls),
            (),
            clock,
            _Observer(),
            owner_capabilities=(Capability(ToolSpec("owned", "owner", {}), adapter),),
        )
        capability = Capability(CRONJOB_MANAGE, adapter, lambda: True)
        app.attach(capability)
        assert app._tools["cronjob_manage"] is capability
        with pytest.raises(ValueError):
            app.attach(capability)
        with pytest.raises(ValueError):
            app.attach(Capability(ToolSpec("owned", "collision", {}), adapter))
        with pytest.raises(ValueError):
            app.attach(Capability(ToolSpec("todo_list", "reserved", {}), adapter))
        with pytest.raises(ValueError):
            app.attach(Capability(ToolSpec("request_mode", "reserved", {}), adapter))
        await app.start()
        try:
            with pytest.raises(ValueError):
                app.attach(Capability(ToolSpec("late", "late", {}), adapter))
        finally:
            await app.close()

    asyncio.run(scenario())


def test_create_waits_for_approval_then_persists(tmp_path):
    async def scenario():
        arguments = {"action": "create", "schedule": "30m", "prompt": "collect links"}
        app, _database, agent, schedules, model = await engine_setup(tmp_path, arguments)
        try:
            receipt = await app.submit_turn(agent, None, "cron", "schedule it")
            await wait_state(app, agent, receipt["run_id"], {"waiting_approval"})
            assert "cronjob_manage" in model.tool_lists[0]
            assert await schedules.page(agent) == []
            approval = (await app.approvals(agent))[0]
            assert approval["tool"] == "cronjob_manage" and approval["arguments"] == arguments
            await app.approve(agent, approval["id"], True, approval["policy_revision"])
            run = await wait_state(app, agent, receipt["run_id"], {"succeeded", "failed"})
            assert run["state"] == "succeeded"
            action = (await app.query("actions", agent))[0]
            assert action["tool"] == "cronjob_manage"
            assert action["state"] == "ok" and action["effect"] == "applied"
            jobs = await schedules.page(agent)
            assert len(jobs) == 1
            assert jobs[0]["value"]["kind"] == "interval" and jobs[0]["value"]["interval_seconds"] == 1800
        finally:
            await app.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["denied", "revoked"])
def test_denied_and_revoked_approvals_create_nothing(tmp_path, mode):
    async def scenario():
        arguments = {"action": "create", "schedule": "30m", "prompt": "collect links"}
        app, _database, agent, schedules, _model = await engine_setup(tmp_path, arguments)
        try:
            receipt = await app.submit_turn(agent, None, "cron", "schedule it")
            await wait_state(app, agent, receipt["run_id"], {"waiting_approval"})
            approval = (await app.approvals(agent))[0]
            if mode == "denied":
                await app.approve(agent, approval["id"], False, approval["policy_revision"])
            else:
                await app.cancel(agent, receipt["run_id"])
            run = await wait_state(app, agent, receipt["run_id"], {"succeeded", "cancelled"})
            assert run["state"] == ("succeeded" if mode == "denied" else "cancelled")
            action = (await app.query("actions", agent))[0]
            assert action["state"] == ("denied" if mode == "denied" else "cancelled")
            assert action["effect"] == "none"
            assert await schedules.page(agent) == []
        finally:
            await app.close()

    asyncio.run(scenario())


def test_expired_approval_fails_run_without_dispatch(tmp_path):
    async def scenario():
        arguments = {"action": "create", "schedule": "30m", "prompt": "collect links"}
        app, database, agent, schedules, _model = await engine_setup(tmp_path, arguments)
        try:
            receipt = await app.submit_turn(agent, None, "cron", "schedule it")
            await wait_state(app, agent, receipt["run_id"], {"waiting_approval"})
            await database.write(
                lambda u: u.connection.execute(
                    update(s.approvals)
                    .where(s.approvals.c.agent_id == agent)
                    .values(expires_at="2020-01-01T00:00:00.000000+00:00")
                )
            )
            run = await wait_state(app, agent, receipt["run_id"], {"interrupted"})
            assert run["error_code"] == "approval_expired"
            action = (await app.query("actions", agent))[0]
            assert action["effect"] == "none"
            assert await schedules.page(agent) == []
        finally:
            await app.close()

    asyncio.run(scenario())


def test_production_attaches_cronjob_and_isolated_does_not(tmp_path):
    from omni_test_support.v2.memory_semantics import NoModel

    async def scenario():
        production = await open_instance(tmp_path / "production", model=NoModel())
        try:
            capability = production.application._tools.get("cronjob_manage")
            assert capability is not None and capability.spec is CRONJOB_MANAGE
            assert capability.quiescent is not None and capability.quiescent()
        finally:
            await production.close()
        isolated = await open_instance(tmp_path / "isolated", model=NoModel(), capabilities=())
        try:
            assert "cronjob_manage" not in isolated.application._tools
        finally:
            await isolated.close()

    asyncio.run(scenario())
