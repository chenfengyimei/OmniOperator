"""Public skill metadata queries share bounded ownership; model history never queues twice."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_v2_routing import Model
from test_v2_skill_notice import ReadGate, prepared_instance
from test_v2_skill_reads import assert_result, invoke
from test_v2_skill_reads import fixture as model_fixture
from test_v2_skills import markdown

from omni.application.skills import SkillService
from omni.composition.http import http_app
from omni.domain.contracts import BusinessError

QUERIES = ("page", "available", "history", "run_report")


def fixture(*, blocked=False, empty=False):
    state = SimpleNamespace(calls=[], cancelled=False, error=None)
    entered, two, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    rows = [] if empty else [{"id": "skill-c", "revision": 2}, {"id": "skill-d", "revision": 3}]
    report = {"captured": True, "items": rows}

    class Database:
        async def read(self, operation):
            result = operation(self)
            entered.set()
            if len(state.calls) == 2:
                two.set()
            if blocked:
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    state.cancelled = True
                    raise
            if state.error:
                raise state.error
            return result

        def skill_page(self, agent, after, limit):
            assert (agent, after, limit) == ("agent", "skill-b", 2)
            state.calls.append("page")
            return rows

        def skill_available(self, agent, platform):
            assert (agent, platform) == ("agent", "windows")
            state.calls.append("available")
            return rows

        def skill_history(self, agent, skill, after, limit):
            assert (agent, skill, after, limit) == ("agent", "skill-a", 1, 2)
            state.calls.append("history")
            return rows

        def skill_run_report(self, agent, run, platform):
            assert (agent, run, platform) == ("agent", "run", "windows")
            state.calls.append("run_report")
            return report

    expected = {
        "page": {"items": rows, "next_after": None if empty else "skill-d", "platform": "windows"},
        "available": {"items": rows, "platform": "windows"},
        "history": {"items": rows, "next_after": None if empty else 3},
        "run_report": report,
    }
    return SkillService(Database(), None, None, "windows"), state, entered, two, release, expected


async def query(service, name):
    if name == "page":
        return await service.page("agent", "skill-b", 2)
    if name == "available":
        return await service.available("agent")
    if name == "history":
        return await service.history("agent", "skill-a", 1, 2)
    return await service.run_report("agent", "run")


@pytest.mark.parametrize("name", QUERIES)
def test_stopped_public_query_rejects_before_database_read(name):
    async def scenario():
        service, state, *_ = fixture()
        service.stop_accepting()
        with pytest.raises(BusinessError) as caught:
            await query(service, name)
        assert caught.value.code == "skills_busy" and caught.value.status == 503 and caught.value.retryable
        assert not state.calls and not service._pending
        assert await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("name", QUERIES)
def test_public_query_has_eight_admissions_and_two_actual_database_reads(name):
    async def scenario():
        service, state, _, two, release, expected = fixture(blocked=True)
        waiters = [asyncio.create_task(query(service, name)) for _ in range(8)]
        try:
            await asyncio.wait_for(two.wait(), 5)
            await asyncio.sleep(0)
            assert state.calls == [name, name] and len(service._pending) == 8
            with pytest.raises(BusinessError) as caught:
                await query(service, name)
            assert caught.value.code == "skills_busy" and state.calls == [name, name]
            release.set()
            results = await asyncio.wait_for(asyncio.gather(*waiters), 5)
            assert results == [expected[name]] * 8 and state.calls == [name] * 8
        finally:
            release.set()
            await asyncio.gather(*waiters, return_exceptions=True)
            assert await service.close(5)

    asyncio.run(scenario())


@pytest.mark.parametrize("name", QUERIES)
def test_cancelled_public_waiter_keeps_actual_query_owned_until_delivery(name):
    async def scenario():
        service, state, entered, _, release, expected = fixture(blocked=True)
        waiter = asyncio.create_task(query(service, name))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            owner = next(iter(service._pending), None)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert owner is not None and service._pending == {owner} and not state.cancelled
            assert not await service.close(0)
            release.set()
            assert await service.close(5) and owner.result() == expected[name]
            assert state.calls == [name] and not state.cancelled
        finally:
            release.set()
            await asyncio.gather(waiter, return_exceptions=True)
            assert await service.close(5)

    asyncio.run(scenario())


@pytest.mark.parametrize("name", QUERIES)
def test_cancelling_close_waiter_does_not_cancel_public_metadata_query(monkeypatch, name):
    async def scenario():
        service, state, entered, _, release, expected = fixture(blocked=True)
        started, stop = asyncio.Event(), service.stop_accepting

        def stopping():
            stop()
            started.set()

        monkeypatch.setattr(service, "stop_accepting", stopping)
        waiter = asyncio.create_task(query(service, name))
        closer = None
        try:
            await asyncio.wait_for(entered.wait(), 5)
            closer = asyncio.create_task(service.close(8))
            await asyncio.wait_for(started.wait(), 5)
            closer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closer
            assert len(service._pending) == 1 and not state.cancelled and not waiter.done()
            release.set()
            assert await waiter == expected[name] and state.calls == [name]
        finally:
            release.set()
            await asyncio.gather(waiter, *([closer] if closer else []), return_exceptions=True)
            assert await service.close(5)

    asyncio.run(scenario())


@pytest.mark.parametrize("name", QUERIES)
@pytest.mark.parametrize("empty", [False, True])
def test_public_query_keeps_pagination_platform_and_empty_payloads(name, empty):
    async def scenario():
        service, state, _, _, _, expected = fixture(empty=empty)
        assert await query(service, name) == expected[name]
        assert state.calls == [name] and not service._pending and await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("name", QUERIES)
def test_repository_error_keeps_identity_and_fresh_query_remains_possible(name):
    async def scenario():
        service, state, _, _, _, expected = fixture()
        state.error = BusinessError("not_found", "Fixture repository error", status=404)
        with pytest.raises(BusinessError) as caught:
            await query(service, name)
        assert caught.value is state.error and not service._pending
        state.error = None
        assert await query(service, name) == expected[name]
        assert state.calls == [name, name] and await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("closing", [False, True])
def test_two_admitted_model_histories_complete_without_nested_public_admission(closing):
    async def scenario():
        service, state, _, two, release = model_fixture("catalog")
        waiters = [asyncio.create_task(invoke(service, "skill_history")) for _ in range(2)]
        try:
            await asyncio.wait_for(two.wait(), 5)
            assert len(service._pending) == 2
            if closing:
                service.stop_accepting()
            release.set()
            for result in await asyncio.wait_for(asyncio.gather(*waiters), 5):
                assert_result(result, "skill_history")
            assert state.calls.count("history") == 2 and not state.activations
        finally:
            release.set()
            for waiter in waiters:
                waiter.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)
            owners = tuple(service._pending)
            for owner in owners:
                owner.cancel()
            await asyncio.gather(*owners, return_exceptions=True)
            assert await service.close(5)

    asyncio.run(scenario())


@pytest.mark.parametrize("name", ["page", "history"])
def test_real_sqlite_query_delivery_retains_database_until_actual_owner_finishes(tmp_path, monkeypatch, name):
    async def scenario():
        instance, model, agent, row, _ = await prepared_instance(tmp_path, True)
        gate = ReadGate(instance._database)
        monkeypatch.setattr(instance.skills, "_db", gate)
        operation = instance.skills.page(agent) if name == "page" else instance.skills.history(agent, row["id"])
        waiter = asyncio.create_task(operation)
        try:
            await asyncio.wait_for(gate.entered.wait(), 5)
            owner = next(iter(instance.skills._pending), None)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert owner is not None and not gate.cancelled and len(instance.skills._pending) == 1
            report = await instance.close(0.01)
            assert not report["complete"] and not instance._database.metrics["closed"]
            assert not instance.skills._packages._closing
            gate.release.set()
            assert (await instance.close(10))["complete"]
            result = owner.result()
            assert len(result["items"]) == 1 and result["next_after"] is None
            assert result["items"][0]["id"] == row["id" if name == "page" else "revision_id"]
            assert len(model.requests) == 1 and not gate.cancelled
        finally:
            gate.release.set()
            await asyncio.gather(waiter, return_exceptions=True)
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


@pytest.mark.parametrize("name", QUERIES)
def test_owner_http_stopped_metadata_query_returns_503_without_skill_read(tmp_path, monkeypatch, name):
    with TestClient(http_app(tmp_path, model=Model())) as client:
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text()
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        base = f"/api/v1/agents/{agent}"
        saved = client.post(base + "/skills", json={"content": markdown()})
        assert saved.status_code == 201
        row, calls = saved.json(), []
        service = client.app.state.runtime.skills
        original_read = service._db.read

        async def read(operation):
            calls.append(True)
            return await original_read(operation)

        monkeypatch.setattr(service, "_db", SimpleNamespace(read=read))
        client.portal.call(service.stop_accepting)
        paths = {
            "page": "/skills",
            "available": "/skills/available",
            "history": f"/skills/{row['id']}/history",
            "run_report": f"/runs/{uuid.uuid4()}/skills",
        }
        response = client.get(base + paths[name])
        assert response.status_code == 503 and "skills_busy" in response.text
        assert response.headers["cache-control"] == "no-store" and not calls
