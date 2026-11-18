"""Default composition uses owned native workers and durable authority; fake pipes/store only."""

import asyncio

import pytest
from omni_test_support.v2.v2_credential_worker_helpers import Model, Spawn

from omni.adapters.native_credentials import NativeCredentials
from omni.adapters.platform.runtime.paths import InstanceLock
from omni.composition import instance as composition
from omni.domain.contracts import BusinessError


@pytest.mark.parametrize(
    "fault,state,outcome",
    [(None, "ready", "applied"), ("wrong_ready", "ready", "not_applied"), ("lost", "unknown", "unknown")],
)
def test_native_default_integrates_intent_and_authority_without_storing_secret_text(
    tmp_path, monkeypatch, fault, state, outcome
):
    async def scenario():
        spawn = Spawn(Model(fault))
        monkeypatch.setattr(
            composition,
            "NativeCredentials",
            lambda namespace, redactor: NativeCredentials(namespace, redactor, process_factory=spawn),
        )
        instance = await composition.open_instance(tmp_path / "home", capabilities=())
        try:
            assert instance._credential_io.health()["backend"] == "native_process" and not spawn.calls
            if fault is None:
                await instance.credentials.change("fixture/key", "only-native-fixture-secret")
                assert await instance._credential_io.get("fixture/key") == "only-native-fixture-secret"
            else:
                with pytest.raises(BusinessError):
                    await instance.credentials.change("fixture/key", "only-native-fixture-secret")
            row = (await instance.credentials.page())["items"][0]
            assert row["state"] == state, row
            operation = (await instance.credentials.history(row["id"]))["items"][0]
            assert operation["state"] == outcome, operation
            if fault == "lost":
                with pytest.raises(BusinessError):
                    await instance.credentials.change("fixture/key", "no-auto-retry")
                assert len(spawn.calls) == 1
            assert "only-native-fixture-secret" not in str(await instance.credentials.audit())
        finally:
            assert (await instance.close())["complete"]
        for path in (tmp_path / "home").rglob("*.jsonl"):
            assert "only-native-fixture-secret" not in path.read_text(encoding="utf-8")

    asyncio.run(scenario())


def test_caller_cancellation_and_failed_process_reclaim_keep_instance_exclusive_until_retry(tmp_path, monkeypatch):
    async def scenario():
        model = Model()
        model.result_release.clear()
        model.cleanup_allowed = False
        spawn = Spawn(model)
        monkeypatch.setattr(
            composition,
            "NativeCredentials",
            lambda namespace, redactor: NativeCredentials(namespace, redactor, process_factory=spawn),
        )
        instance = await composition.open_instance(tmp_path / "home", capabilities=())
        caller = asyncio.create_task(instance.credentials.change("fixture/key", "only-isolated-secret"))
        await model.dispatched.wait()
        caller.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert not (await instance.close(0.02))["complete"]
            with pytest.raises(BusinessError):
                InstanceLock(instance.paths).acquire()
            model.result_release.set()
            async with asyncio.timeout(5):
                while instance.credentials.health()["pending_changes"]:
                    await asyncio.sleep(0)
            assert not (await instance.close(0.02))["complete"]
            assert not instance._database.metrics["closed"]
            assert instance._credential_io.health()["cleanup_pending"] == 1
            model.cleanup_allowed = True
            assert (await instance.close(5))["complete"]
            lock = InstanceLock(instance.paths)
            lock.acquire()
            lock.close()
        finally:
            model.cleanup_allowed = True
            model.result_release.set()
            await asyncio.gather(caller, return_exceptions=True)
            await instance.close(5)

    asyncio.run(scenario())


def test_unknown_credential_cannot_be_reviewed_while_native_process_remains_owned(tmp_path, monkeypatch):
    async def scenario():
        model = Model("lost")
        model.cleanup_allowed = False
        spawn = Spawn(model)
        monkeypatch.setattr(
            composition,
            "NativeCredentials",
            lambda namespace, redactor: NativeCredentials(namespace, redactor, process_factory=spawn),
        )
        instance = await composition.open_instance(tmp_path / "home", capabilities=())
        try:
            with pytest.raises(BusinessError):
                await instance.credentials.change("fixture/key", "only-isolated-native-secret")
            row = (await instance.credentials.page())["items"][0]
            assert row["state"] == "unknown"
            with pytest.raises(BusinessError) as busy:
                await instance.credentials.review(
                    row["id"],
                    row["revision"],
                    row["authority_epoch"],
                    row["fingerprint"],
                    row["policy"],
                    "allow_current",
                    "Injected cleanup is still pending",
                )
            assert busy.value.code == "credential_review_busy"
            assert (await instance.credentials.get(row["id"]))["state"] == "unknown"
            assert not (await instance.credentials.history(row["id"], reviews=True))["items"]
        finally:
            model.cleanup_allowed = True
            assert (await instance.close(5))["complete"]

    asyncio.run(scenario())
