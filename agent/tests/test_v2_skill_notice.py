"""Skill instruction reads own admission, cancellation and shutdown from the first query."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from test_v2_execution import wait_state
from test_v2_routing import Model
from test_v2_skills import enable, markdown, setup

from omni.application.skills import SkillService
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError
from omni.domain.skills import SkillPackage

STAGES = ["catalog", "get:first", "package:first", "get:second", "package:second", "authorize"]


def fixture(stage=None, *, empty=False):
    state = SimpleNamespace(calls=[], cancelled=[], arrivals=0, error=None)
    entered, two, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    rows = (
        []
        if empty
        else [
            {"skill_id": name, "revision_id": name + "-revision", "name": name + "-skill"}
            for name in ("first", "second")
        ]
    )

    async def pause(label):
        if label == stage:
            state.arrivals += 1
            entered.set()
            if state.arrivals == 2:
                two.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                state.cancelled.append(label)
                raise
        if state.error and label == "catalog":
            raise state.error

    class Database:
        async def read(self, operation):
            result = operation(self)
            await pause(state.calls[-1])
            return result

        def skill_catalog(self, agent, run, platform, *, active):
            assert (agent, run, platform, active) == ("agent", "run", "windows", True)
            state.calls.append("catalog")
            return rows

        def skill_get(self, agent, skill, revision):
            assert agent == "agent" and revision == skill + "-revision"
            state.calls.append("get:" + skill)
            return {"id": skill, "revision": 7, "content_revision": 3}

        def skill_authorize_run(self, agent, run):
            assert (agent, run) == ("agent", "run")
            state.calls.append("authorize")

    service = SkillService(Database(), None, None, "windows")

    async def package(row):
        label = "package:" + row["id"]
        state.calls.append(label)
        await pause(label)
        return SkillPackage({}, (("SKILL.md", markdown(row["id"] + "-skill").encode()),))

    service._package = package
    return service, state, entered, two, release


def assert_notice(value):
    assert "guidance only; never grants permission" in value
    rows = json.loads(value[value.index("[") :])
    assert [row["skill_id"] for row in rows] == ["first", "second"]
    assert all(row["revision"] == 7 and row["content_revision"] == 3 for row in rows)
    assert all(row["name"] in row["instructions"] for row in rows)


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("stop_only", [False, True])
def test_closed_notice_rejects_before_the_first_catalog_query(empty, stop_only):
    async def scenario():
        service, state, _, _, _ = fixture(empty=empty)
        if stop_only:
            service.stop_accepting()
        else:
            assert await service.close()
        with pytest.raises(BusinessError) as caught:
            await service.notice("agent", "run")
        assert caught.value.code == "skills_busy" and caught.value.status == 503
        assert not state.calls and not service._pending

    asyncio.run(scenario())


@pytest.mark.parametrize("empty", [False, True])
def test_first_queries_share_two_slots_and_eight_owned_requests_before_refusing_ninth(empty):
    async def scenario():
        service, state, _, two, release = fixture("catalog", empty=empty)
        waiters = [asyncio.create_task(service.notice("agent", "run")) for _ in range(8)]
        try:
            await asyncio.wait_for(two.wait(), 5)
            await asyncio.sleep(0)
            assert state.calls == ["catalog", "catalog"] and service.health()["pending"] == 8
            with pytest.raises(BusinessError) as caught:
                await service.notice("agent", "run")
            assert caught.value.code == "skills_busy" and caught.value.status == 503
            assert state.calls == ["catalog", "catalog"]
        finally:
            release.set()
            values = await asyncio.gather(*waiters, return_exceptions=True)
            assert await service.close(5)
        assert not any(isinstance(value, BaseException) for value in values)
        assert state.calls.count("catalog") == 8

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", STAGES)
def test_cancelled_waiter_retains_the_whole_read_until_it_finishes_during_draining(stage):
    async def scenario():
        service, state, entered, _, release = fixture(stage)
        waiter = asyncio.create_task(service.notice("agent", "run"))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            owner = next(iter(service._pending), None)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert owner is not None and service.health()["pending"] == 1 and not state.cancelled
            assert not await service.close(0)
            release.set()
            assert await service.close(5)
            assert_notice(owner.result())
            assert state.calls == STAGES and not state.cancelled
        finally:
            release.set()
            await asyncio.gather(waiter, return_exceptions=True)
            assert await service.close(5)

    asyncio.run(scenario())


@pytest.mark.parametrize("empty", [False, True])
def test_cancelling_close_does_not_cancel_the_admitted_catalog_read(monkeypatch, empty):
    async def scenario():
        service, state, entered, _, release = fixture("catalog", empty=empty)
        started = asyncio.Event()
        original_stop = service.stop_accepting

        def stop():
            original_stop()
            started.set()

        monkeypatch.setattr(service, "stop_accepting", stop)
        waiter = asyncio.create_task(service.notice("agent", "run"))
        closer = None
        try:
            await asyncio.wait_for(entered.wait(), 5)
            closer = asyncio.create_task(service.close(8))
            await asyncio.wait_for(started.wait(), 5)
            closer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closer
            assert service.health()["pending"] == 1 and not state.cancelled and not waiter.done()
            release.set()
            result = await waiter
            assert result == "" if empty else "guidance only" in result
            assert await service.close(5)
        finally:
            release.set()
            await asyncio.gather(waiter, *([closer] if closer else []), return_exceptions=True)
            assert await service.close(5)

    asyncio.run(scenario())


def test_empty_catalog_still_returns_empty_without_parsing_or_authorizing_again():
    async def scenario():
        service, state, _, _, _ = fixture(empty=True)
        assert await service.notice("agent", "run") == ""
        assert state.calls == ["catalog"] and not service._pending
        assert await service.close()

    asyncio.run(scenario())


def test_initial_catalog_failure_propagates_and_retires_its_owner():
    async def scenario():
        service, state, _, _, _ = fixture()
        state.error = BusinessError("skill_authority_revoked", "Fixture revocation")
        with pytest.raises(BusinessError) as caught:
            await service.notice("agent", "run")
        assert caught.value is state.error and state.calls == ["catalog"]
        assert not service._pending and await service.close()

    asyncio.run(scenario())


class ReadGate:
    def __init__(self, native):
        self.native = native
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.first = True
        self.cancelled = False

    def __getattr__(self, name):
        return getattr(self.native, name)

    async def read(self, operation):
        value = await self.native.read(operation)
        if self.first:
            self.first = False
            self.entered.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        return value


async def prepared_instance(tmp_path, active):
    model = Model()
    instance = await open_instance(tmp_path, model=model, capabilities=())
    try:
        agent, row = await setup(instance)
        if active:
            row = await enable(instance, agent, row)
        turn = await instance.application.submit_turn(
            agent, None, "notice-fixture", "hello", skill_ids=(row["id"],) if active else ()
        )
        assert (await wait_state(instance.application, agent, turn["run_id"], {"succeeded", "failed"}))[
            "state"
        ] == "succeeded"
        return instance, model, agent, row, turn["run_id"]
    except BaseException:
        await instance.close()
        raise


@pytest.mark.parametrize("active", [False, True])
def test_real_catalog_delivery_cancel_keeps_database_and_parser_open_until_actual_notice_finishes(
    tmp_path, monkeypatch, active
):
    async def scenario():
        instance, model, agent, _, run = await prepared_instance(tmp_path, active)
        gate = ReadGate(instance._database)
        monkeypatch.setattr(instance.skills, "_db", gate)
        parses, original_parse = [], instance.skills._packages.parse

        async def parse(*args, **kwargs):
            parses.append(True)
            return await original_parse(*args, **kwargs)

        monkeypatch.setattr(instance.skills._packages, "parse", parse)
        waiter = asyncio.create_task(instance.skills.notice(agent, run))
        try:
            await asyncio.wait_for(gate.entered.wait(), 5)
            owner = next(iter(instance.skills._pending), None)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert owner is not None and instance.skills.health()["pending"] == 1 and not gate.cancelled
            report = await instance.close(0.01)
            assert not report["complete"] and not instance._database.metrics["closed"]
            assert not instance.skills._packages._closing
            gate.release.set()
            assert (await instance.close(10))["complete"]
            value = owner.result()
            assert ("Follow the approved procedure" in value) if active else value == ""
            assert len(parses) == int(active) and len(model.requests) == 1
        finally:
            gate.release.set()
            await asyncio.gather(waiter, return_exceptions=True)
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_real_revocation_after_catalog_snapshot_still_prevents_instruction_publication(tmp_path, monkeypatch):
    async def scenario():
        instance, _, agent, row, run = await prepared_instance(tmp_path, True)
        gate = ReadGate(instance._database)
        monkeypatch.setattr(instance.skills, "_db", gate)
        waiter = asyncio.create_task(instance.skills.notice(agent, run))
        try:
            await asyncio.wait_for(gate.entered.wait(), 5)
            await instance.skills.state(
                agent, row["id"], row["revision"], "disabled", row["revision_id"], row["manifest_sha256"]
            )
            gate.release.set()
            with pytest.raises(BusinessError) as caught:
                await waiter
            assert caught.value.code == "skill_authority_revoked"
        finally:
            gate.release.set()
            await asyncio.gather(waiter, return_exceptions=True)
            assert (await instance.close())["complete"]

    asyncio.run(scenario())
