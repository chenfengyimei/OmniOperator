"""Model skill reads retain one owner from captured catalog lookup through activation commit."""

from __future__ import annotations

from omni_test_support.v2.skill_reads import (
    CONTEXT as CONTEXT,
)
from omni_test_support.v2.skill_reads import (
    REFERENCE as REFERENCE,
)
from omni_test_support.v2.skill_reads import (
    STAGES as STAGES,
)
from omni_test_support.v2.skill_reads import (
    TOOLS as TOOLS,
)
from omni_test_support.v2.skill_reads import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.skill_reads import (
    Effect as Effect,
)
from omni_test_support.v2.skill_reads import (
    InvocationContext as InvocationContext,
)
from omni_test_support.v2.skill_reads import (
    Outcome as Outcome,
)
from omni_test_support.v2.skill_reads import (
    ReadGate as ReadGate,
)
from omni_test_support.v2.skill_reads import (
    SimpleNamespace as SimpleNamespace,
)
from omni_test_support.v2.skill_reads import (
    SkillPackage as SkillPackage,
)
from omni_test_support.v2.skill_reads import (
    SkillService as SkillService,
)
from omni_test_support.v2.skill_reads import (
    arguments as arguments,
)
from omni_test_support.v2.skill_reads import (
    assert_result as assert_result,
)
from omni_test_support.v2.skill_reads import (
    asyncio as asyncio,
)
from omni_test_support.v2.skill_reads import (
    fixture as fixture,
)
from omni_test_support.v2.skill_reads import (
    invoke as invoke,
)
from omni_test_support.v2.skill_reads import (
    json as json,
)
from omni_test_support.v2.skill_reads import (
    markdown as markdown,
)
from omni_test_support.v2.skill_reads import (
    prepared_instance as prepared_instance,
)
from omni_test_support.v2.skill_reads import (
    pytest as pytest,
)


@pytest.mark.parametrize("name", TOOLS)
def test_closed_read_tools_refuse_before_catalog_io(name):
    async def scenario():
        service, state, _, _, _ = fixture()
        assert await service.close()
        result = await invoke(service, name)
        assert result.status == Outcome.ERROR and result.error_code == "skills_busy"
        assert not state.calls and not service._pending

    asyncio.run(scenario())


@pytest.mark.parametrize("name", TOOLS)
def test_read_tools_charge_first_query_to_two_slots_and_eight_admissions(name):
    async def scenario():
        service, state, _, two, release = fixture("catalog")
        waiters = [asyncio.create_task(invoke(service, name)) for _ in range(8)]
        try:
            await asyncio.wait_for(two.wait(), 5)
            await asyncio.sleep(0)
            assert state.calls == ["catalog", "catalog"] and len(service._pending) == 8
            rejected = await invoke(service, name)
            assert rejected.error_code == "skills_busy" and state.calls == ["catalog", "catalog"]
        finally:
            release.set()
            results = await asyncio.gather(*waiters, return_exceptions=True)
            assert await service.close(5)
        for result in results:
            assert_result(result, name)
        assert len(state.activations) == (8 if name in {"skill_activate", "skill_view"} else 0)

    asyncio.run(scenario())


@pytest.mark.parametrize("name,stage", STAGES)
def test_cancelled_tool_waiter_keeps_one_owner_through_activation_and_shutdown(name, stage):
    async def scenario():
        service, state, entered, _, release = fixture(stage)
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
            assert len(state.activations) == (1 if name in {"skill_activate", "skill_view"} else 0)
            assert not state.cancelled and state.calls.count("catalog") == 1
        finally:
            release.set()
            await asyncio.gather(waiter, return_exceptions=True)
            assert await service.close(5)

    asyncio.run(scenario())


@pytest.mark.parametrize("name", ["skills_list", "skill_view"])
def test_close_waiter_cancellation_leaves_actual_read_owned(monkeypatch, name):
    async def scenario():
        service, state, entered, _, release = fixture("catalog")
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
            assert len(service._pending) == 1 and not state.cancelled
            release.set()
            assert_result(await waiter, name)
        finally:
            release.set()
            await asyncio.gather(waiter, *([closer] if closer else []), return_exceptions=True)
            assert await service.close(5)

    asyncio.run(scenario())


def test_two_parallel_body_reads_use_one_owner_each_and_do_not_deadlock_on_nested_admission():
    async def scenario():
        service, _, _, two, release = fixture("package")
        waiters = [asyncio.create_task(invoke(service, "skill_view")) for _ in range(2)]
        try:
            await asyncio.wait_for(two.wait(), 5)
            assert len(service._pending) == 2
            release.set()
            results = await asyncio.wait_for(asyncio.gather(*waiters), 5)
            for result in results:
                assert_result(result, "skill_view")
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


@pytest.mark.parametrize("case", ["not_captured", "wrong_revision", "revoked"])
def test_existing_capture_and_final_activation_authority_checks_remain(case):
    async def scenario():
        service, state, _, _, _ = fixture()
        params = arguments("skill_view")
        if case == "not_captured":
            state.captured = False
        elif case == "wrong_revision":
            params["revision_id"] = "different"
        else:
            state.revoked = True
        try:
            if case == "revoked":
                with pytest.raises(BusinessError) as caught:
                    await invoke(service, "skill_view", params)
                assert caught.value.code == "skill_authority_revoked"
            else:
                assert (await invoke(service, "skill_view", params)).error_code == "skill_not_captured"
            assert not state.activations
        finally:
            assert await service.close()

    asyncio.run(scenario())


def test_empty_list_still_avoids_body_reads_and_missing_reference_is_not_activated():
    async def scenario():
        service, state, _, _, _ = fixture()
        try:
            state.captured = False
            result = await invoke(service, "skills_list")
            assert result.status == Outcome.OK and json.loads(result.text) == [] and state.calls == ["catalog"]
            state.captured = True
            result = await invoke(service, "skill_view", {**arguments("skill_view"), "path": "references/missing.md"})
            assert result.status == Outcome.ERROR and result.error_code == "not_found" and not state.activations
        finally:
            assert await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("name", ["skills_list", "skill_history"])
def test_real_sqlite_tool_catalog_cancel_retains_storage_until_actual_read_finishes(tmp_path, monkeypatch, name):
    async def scenario():
        instance, model, agent, row, run = await prepared_instance(tmp_path, True)
        gate = ReadGate(instance._database)
        monkeypatch.setattr(instance.skills, "_db", gate)
        context = InvocationContext(agent, run, "fixture-session", 1)
        params = {} if name == "skills_list" else {"skill_id": row["id"]}
        waiter = asyncio.create_task(instance.skills.invoke(name, params, operation_id="fixture-read", context=context))
        try:
            await asyncio.wait_for(gate.entered.wait(), 5)
            owner = next(iter(instance.skills._pending), None)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert owner is not None and len(instance.skills._pending) == 1 and not gate.cancelled
            report = await instance.close(0.01)
            assert not report["complete"] and not instance._database.metrics["closed"]
            gate.release.set()
            assert (await instance.close(10))["complete"]
            result = owner.result()
            assert result.status == Outcome.OK and len(json.loads(result.text)) == 1
            assert len(model.requests) == 1
        finally:
            gate.release.set()
            await asyncio.gather(waiter, return_exceptions=True)
            assert (await instance.close())["complete"]

    asyncio.run(scenario())
