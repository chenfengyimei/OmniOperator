"""One shutdown budget preserves ownership until actual cleanup has finished."""

import asyncio

import pytest

from omni.adapters.platform.paths import InstanceLock
from omni.composition.instance import open_instance
from omni.composition.lifecycle import Lifetime
from omni.domain.contracts import BusinessError


def test_shared_budget_reuses_pending_cleanup_and_retains_instance_lock(tmp_path):
    async def scenario():
        instance = await open_instance(tmp_path, capabilities=())
        release = asyncio.Event()
        calls = 0

        async def stalled(_):
            nonlocal calls
            calls += 1
            await release.wait()
            return True

        instance._lifetime.own("fixture_stalled", 0, stalled)
        try:
            result = await instance.close(0.02)
            assert not result["complete"]
            assert "fixture_stalled" in result["residual"]
            assert not instance.health()["ready"]
            assert not instance._database.metrics["closed"]
            with pytest.raises(BusinessError, match="already running"):
                InstanceLock(instance.paths).acquire()
            assert not (await instance.close(0.02))["complete"]
            assert calls == 1
            release.set()
            assert (await instance.close(10))["complete"]
            assert instance._database.metrics["closed"]
            recovered = InstanceLock(instance.paths)
            recovered.acquire()
            recovered.close()
        finally:
            release.set()
            await instance.close()

    asyncio.run(scenario())


def test_partial_startup_rollback_closes_components_and_releases_lock(tmp_path, monkeypatch):
    import omni.composition.instance as module

    original = module.Application

    class Failure(original):
        async def start(self):
            raise RuntimeError("fixture-start-failure")

    async def scenario():
        with monkeypatch.context() as patch:
            patch.setattr(module, "Application", Failure)
            with pytest.raises(RuntimeError, match="fixture-start-failure"):
                await module.open_instance(tmp_path, capabilities=())
        instance = await module.open_instance(tmp_path, capabilities=())
        assert instance.health()["ready"]
        assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_cleanup_false_retries_but_pending_cleanup_never_duplicates():
    async def scenario():
        lifetime = Lifetime()
        calls = []

        async def close(_):
            calls.append("close")
            return len(calls) > 1

        lifetime.own("fixture", 0, close)
        assert not (await lifetime.close(1))["complete"]
        assert (await lifetime.close(1))["complete"]
        assert calls == ["close", "close"]

    asyncio.run(scenario())


def test_a_fresh_close_budget_retries_a_previous_coordinator_that_finishes_false():
    async def scenario():
        lifetime = Lifetime()
        started, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def close(_):
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                await release.wait()
                return False
            return True

        lifetime.own("fixture", 0, close)
        assert not (await lifetime.close(0))["complete"]
        await asyncio.wait_for(started.wait(), 10)
        caller = asyncio.create_task(lifetime.close(10))
        await asyncio.sleep(0)
        release.set()
        assert (await caller)["complete"] and calls == 2

    asyncio.run(scenario())


def test_fresh_coordinator_retries_inherited_short_budget_component_once_before_downstream_close():
    async def scenario():
        entered, release, second = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class Observed(Lifetime):
            runs = 0

            async def _perform(self, timeout_s):
                self.runs += 1
                if self.runs == 2:
                    second.set()
                return await super()._perform(timeout_s)

        lifetime, calls, downstream = Observed(), [], []

        async def close(_):
            calls.append("component")
            if len(calls) == 1:
                entered.set()
                await release.wait()
                return False
            return True

        async def dependent(_):
            downstream.append("closed")
            return True

        lifetime.own("component", 0, close)
        lifetime.own("storage", 1, dependent)
        assert not (await lifetime.close(0.01))["complete"]
        await entered.wait()
        assert not (await lifetime._coordinator)["complete"] and not downstream
        caller = asyncio.create_task(lifetime.close(1))
        await asyncio.wait_for(second.wait(), 1)
        release.set()
        assert (await caller)["complete"] and len(calls) == 2 and downstream == ["closed"]

    asyncio.run(scenario())
