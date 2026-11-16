"""Approved model skill writes keep one owner across preparation, final guard and commit delivery."""

from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest
from test_v2_skills import markdown

from omni.adapters.skill_packages import capture
from omni.application.skills import SkillService
from omni.domain.contracts import BusinessError, Effect, InvocationContext, Outcome
from omni.domain.skills import SkillPackage

TOOLS = ("skill_create", "skill_rewrite", "skill_patch", "skill_rollback", "skill_archive", "skill_restore")
CONTEXT = InvocationContext("agent", "run", "session", 1)
STAGES = [
    ("skill_create", "parse"),
    ("skill_create", "publish"),
    ("skill_create", "commit"),
    ("skill_rewrite", "parse"),
    ("skill_rewrite", "row"),
    ("skill_rewrite", "package"),
    ("skill_rewrite", "commit"),
    ("skill_patch", "row"),
    ("skill_patch", "parse"),
    ("skill_patch", "commit"),
    ("skill_rollback", "row"),
    ("skill_rollback", "package"),
    ("skill_rollback", "commit"),
    ("skill_archive", "row"),
    ("skill_archive", "commit"),
    ("skill_restore", "row"),
    ("skill_restore", "commit"),
]


def arguments(name):
    if name == "skill_create":
        return {"content": markdown("created-skill")}
    result = {"skill_id": "skill", "revision": 7}
    if name == "skill_rewrite":
        result["content"] = markdown(text="Explicit rewritten instructions.")
    elif name == "skill_patch":
        result.update(section="Procedure", text="Explicit patched instructions.")
    elif name == "skill_rollback":
        result["revision_id"] = "historical-revision"
    return result


def fixture(name, stage=None):
    state = SimpleNamespace(calls=[], cancelled=[], commits=[], guards=[], published=[], arrivals=0, error=None)
    entered, two, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = capture(markdown().encode(), "markdown")
    old_files = [{"path": "SKILL.md", "sha256": "a" * 64, "bytes": len(markdown().encode())}]
    old = {
        "id": "skill",
        "revision": 7,
        "content_revision": 3,
        "revision_id": "old-revision",
        "manifest_sha256": "old-manifest",
        "state": "disabled",
        "files": old_files,
    }

    async def pause(label):
        if stage == label:
            state.arrivals += 1
            entered.set()
            if state.arrivals == 2:
                two.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                state.cancelled.append(label)
                raise

    class Database:
        async def read(self, operation):
            value = operation(self)
            await pause(state.calls[-1])
            return value

        async def write(self, operation):
            value = operation(self)
            await pause("commit")
            return value

        def skill_get(self, agent, skill, revision=None):
            assert (agent, skill) == ("agent", "skill") and revision in (None, "historical-revision")
            state.calls.append("row")
            return old

        def approved_dispatch(self, agent, run, operation, params, tool):
            assert (agent, run, operation, params, tool) == ("agent", "run", "operation", arguments(name), name)
            state.calls.append("guard")
            state.guards.append(operation)
            if state.error:
                raise state.error

        def skill_install(self, agent, metadata, files, skill, revision):
            assert agent == "agent" and revision == (0 if name == "skill_create" else 7)
            assert skill == (None if name == "skill_create" else "skill")
            assert state.calls[-1] == "guard"
            state.commits.append(name)
            state.calls.append("commit")
            return {
                **old,
                "id": skill or "created",
                "revision": 1 if skill is None else 8,
                "content_revision": 1 if skill is None else 4,
                "revision_id": "new-revision",
                "files": files,
            }

        def skill_state(self, agent, skill, revision, status, revision_id, digest):
            assert (agent, skill, revision, revision_id, digest) == (
                "agent",
                "skill",
                7,
                "old-revision",
                "old-manifest",
            )
            assert status == ("archived" if name == "skill_archive" else "disabled") and state.calls[-1] == "guard"
            state.commits.append(name)
            state.calls.append("commit")
            return {**old, "revision": 8, "state": status}

    class Packages:
        async def parse(self, content, format):
            state.calls.append("parse")
            await pause("parse")
            return capture(content, format)

    class Objects:
        async def publish(self, agent, content, mime):
            assert (agent, mime) == ("agent", "text/plain")
            state.calls.append("publish")
            state.published.append(content)
            await pause("publish")
            return {"id": "object", "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}

    service = SkillService(Database(), Objects(), Packages(), "windows")

    async def package(row):
        assert row is old
        state.calls.append("package")
        await pause("package")
        return SkillPackage(original.metadata, (*original.files, ("references/guide.md", b"Retained reference text.")))

    service._package = package
    return service, state, entered, two, release


async def invoke(service, name):
    return await service.invoke(name, arguments(name), operation_id="operation", context=CONTEXT)


def assert_result(result, name):
    assert result.status == Outcome.OK and result.effect == Effect.APPLIED
    value = json.loads(result.text)
    assert result.data == value and value["skill_id"] == ("created" if name == "skill_create" else "skill")
    assert value["state"] == ("archived" if name == "skill_archive" else "disabled")
    assert value["content_revision"] == (
        1 if name == "skill_create" else 3 if name in {"skill_archive", "skill_restore"} else 4
    )


@pytest.mark.parametrize("name", TOOLS)
def test_closed_write_tools_refuse_before_any_preparation_or_head_query(name):
    async def scenario():
        service, state, _, _, _ = fixture(name)
        assert await service.close()
        result = await invoke(service, name)
        assert result.status == Outcome.ERROR and result.error_code == "skills_busy"
        assert not state.calls and not service._pending

    asyncio.run(scenario())


@pytest.mark.parametrize("name", TOOLS)
def test_write_tools_share_eight_admissions_and_two_execution_slots_from_first_stage(name):
    async def scenario():
        stage = "parse" if name in {"skill_create", "skill_rewrite"} else "row"
        service, state, _, two, release = fixture(name, stage)
        waiters = [asyncio.create_task(invoke(service, name)) for _ in range(8)]
        try:
            await asyncio.wait_for(two.wait(), 5)
            await asyncio.sleep(0)
            assert len(service._pending) == 8 and state.calls == [stage, stage]
            assert (await invoke(service, name)).error_code == "skills_busy" and state.calls == [stage, stage]
        finally:
            release.set()
            results = await asyncio.gather(*waiters, return_exceptions=True)
            assert await service.close(5)
        for result in results:
            assert_result(result, name)

    asyncio.run(scenario())


@pytest.mark.parametrize("name,stage", STAGES)
def test_cancelled_write_waiter_retains_preparation_guard_commit_and_receipt_without_replay(name, stage):
    async def scenario():
        service, state, entered, _, release = fixture(name, stage)
        waiter = asyncio.create_task(invoke(service, name))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            owner = next(iter(service._pending), None)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert owner is not None and len(service._pending) == 1 and not state.cancelled
            assert not await service.close(0)
            release.set()
            assert await service.close(5)
            assert_result(owner.result(), name)
            assert state.commits == [name] and state.guards == ["operation"] and not state.cancelled
            if name in {"skill_rewrite", "skill_patch", "skill_rollback"}:
                assert b"Retained reference text." in state.published
            if name == "skill_patch":
                assert "Explicit patched instructions." in state.published[0].decode()
        finally:
            release.set()
            await asyncio.gather(waiter, return_exceptions=True)
            assert await service.close(5)

    asyncio.run(scenario())


@pytest.mark.parametrize("name", ["skill_patch", "skill_archive"])
def test_cancelling_close_keeps_the_admitted_write_and_allows_later_steps(monkeypatch, name):
    async def scenario():
        service, state, entered, _, release = fixture(name, "row")
        started, original_stop = asyncio.Event(), service.stop_accepting

        def stop():
            original_stop()
            started.set()

        monkeypatch.setattr(service, "stop_accepting", stop)
        waiter = asyncio.create_task(invoke(service, name))
        closer = None
        try:
            await asyncio.wait_for(entered.wait(), 5)
            closer = asyncio.create_task(service.close(8))
            await asyncio.wait_for(started.wait(), 5)
            closer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closer
            release.set()
            assert_result(await waiter, name)
            assert state.commits == [name] and not state.cancelled
        finally:
            release.set()
            await asyncio.gather(waiter, *([closer] if closer else []), return_exceptions=True)
            assert await service.close(5)

    asyncio.run(scenario())


@pytest.mark.parametrize("name", ["skill_rewrite", "skill_patch"])
def test_two_parallel_writes_do_not_reenter_admission_and_deadlock(name):
    async def scenario():
        service, _, _, two, release = fixture(name, "parse")
        waiters = [asyncio.create_task(invoke(service, name)) for _ in range(2)]
        try:
            await asyncio.wait_for(two.wait(), 5)
            assert len(service._pending) == 2
            release.set()
            for result in await asyncio.wait_for(asyncio.gather(*waiters), 5):
                assert_result(result, name)
        finally:
            release.set()
            for waiter in waiters:
                if not waiter.done():
                    waiter.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)
            owners = tuple(service._pending)
            for owner in owners:
                owner.cancel()
            await asyncio.gather(*owners, return_exceptions=True)
            assert await service.close(5)

    asyncio.run(scenario())


@pytest.mark.parametrize("code", ["policy_changed", "skill_authority_revoked", "revision_conflict"])
def test_final_approval_or_revision_failure_still_prevents_commit(code):
    async def scenario():
        service, state, _, _, _ = fixture("skill_rewrite")
        state.error = BusinessError(code, "Fixture final guard rejection")
        try:
            if code == "revision_conflict":
                assert (await invoke(service, "skill_rewrite")).error_code == code
            else:
                with pytest.raises(BusinessError) as caught:
                    await invoke(service, "skill_rewrite")
                assert caught.value is state.error
            assert not state.commits and state.guards == ["operation"]
        finally:
            assert await service.close()

    asyncio.run(scenario())


def test_unknown_tool_keeps_not_found_without_io():
    async def scenario():
        service, state, _, _, _ = fixture("skill_create")
        result = await invoke(service, "unknown_skill_tool")
        assert result.error_code == "tool_not_found" and not state.calls
        assert await service.close()

    asyncio.run(scenario())
