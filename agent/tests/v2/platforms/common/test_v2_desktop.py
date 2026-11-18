"""Real application/storage lifecycles with an injected, gated desktop port."""

from __future__ import annotations

from omni_test_support.v2.desktop import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.desktop import (
    DesktopClock as DesktopClock,
)
from omni_test_support.v2.desktop import (
    FixtureDesktopBackend as FixtureDesktopBackend,
)
from omni_test_support.v2.desktop import (
    InstanceLock as InstanceLock,
)
from omni_test_support.v2.desktop import (
    asynccontextmanager as asynccontextmanager,
)
from omni_test_support.v2.desktop import (
    asyncio as asyncio,
)
from omni_test_support.v2.desktop import (
    begin as begin,
)
from omni_test_support.v2.desktop import (
    json as json,
)
from omni_test_support.v2.desktop import (
    listed as listed,
)
from omni_test_support.v2.desktop import (
    no_pending as no_pending,
)
from omni_test_support.v2.desktop import (
    open_instance as open_instance,
)
from omni_test_support.v2.desktop import (
    prepared as prepared,
)
from omni_test_support.v2.desktop import (
    pytest as pytest,
)
from omni_test_support.v2.desktop import (
    target_identity as target_identity,
)
from omni_test_support.v2.desktop import (
    uuid as uuid,
)
from omni_test_support.v2.desktop import (
    verification as verification,
)


def test_observation_is_explicit_scoped_and_old_view_cleanup_cannot_clear_new_snapshot(tmp_path):
    async def scenario():
        async with prepared(tmp_path) as (instance, backend, _clock, agent):
            service = instance.desktop
            assert (await service.status(agent))["capabilities"] is None
            assert backend.calls == []
            other = (await instance._database.write(lambda u: u.create_agent("Another space")))["id"]
            owner = await begin(service, agent)
            first = await listed(service, agent, owner)
            foreign = await begin(service, other)
            with pytest.raises(BusinessError) as error:
                await service.verify(other, verification(foreign, first))
            assert error.value.code == "desktop_snapshot_expired"
            fresh = await begin(service, agent)
            second = await listed(service, agent, fresh)
            assert not (await service.forget(agent, owner["view_id"]))["forgotten"]
            assert (await service.status(agent))["snapshot_id"] == second["snapshot_id"]
            assert (await service.begin(agent, fresh))["view_id"] == fresh["view_id"]
            assert (await service.status(agent))["snapshot_id"] == second["snapshot_id"]
            with pytest.raises(BusinessError) as error:
                await service.probe(agent, owner)
            assert error.value.code == "desktop_view_changed"
            result = await service.verify(agent, verification(fresh, second))
            assert result["matches"] and result["observation_only"]
            assert (await service.forget(agent, fresh["view_id"]))["forgotten"]
            assert (await service.status(agent))["snapshot_id"] is None
            assert [name for name, _ in backend.calls] == ["targets", "targets", "verify"]

    asyncio.run(scenario())


@pytest.mark.parametrize("after", [False, True], ids=["before-native-after-request-audit", "after-completion-audit"])
def test_expiry_is_rechecked_before_native_and_after_completion_audit(tmp_path, monkeypatch, after):
    async def scenario():
        async with prepared(tmp_path) as (instance, backend, clock, agent):
            service = instance.desktop
            owner = await begin(service, agent)
            snapshot = await listed(service, agent, owner)
            original = service._audit

            async def expiry(agent_id, operation_id, operation, *, completed=False, **metadata):
                await original(agent_id, operation_id, operation, completed=completed, **metadata)
                if operation == "verify" and completed is after:
                    clock.advance(61)

            monkeypatch.setattr(service, "_audit", expiry)
            with pytest.raises(BusinessError) as error:
                await service.verify(agent, verification(owner, snapshot))
            assert error.value.code == "desktop_snapshot_expired"
            assert sum(name == "verify" for name, _ in backend.calls) == int(after)
            assert (await service.status(agent))["snapshot_id"] is None

    asyncio.run(scenario())


def test_expiry_while_worker_starts_is_checked_at_dispatch_gate(tmp_path):
    async def scenario():
        async with prepared(tmp_path) as (instance, backend, clock, agent):
            service = instance.desktop
            owner = await begin(service, agent)
            snapshot = await listed(service, agent, owner)
            backend.hold("verify")
            checking = asyncio.create_task(service.verify(agent, verification(owner, snapshot)))
            await asyncio.wait_for(backend.entered["verify"].wait(), 5)
            clock.advance(61)
            backend.release("verify")
            with pytest.raises(BusinessError) as error:
                await checking
            assert error.value.code == "desktop_snapshot_expired"
            assert not any(name == "verify" for name, _ in backend.calls)

    asyncio.run(scenario())


def test_four_snapshot_reservations_and_eight_diagnostic_views_preserve_live_snapshots(tmp_path):
    async def scenario():
        async with prepared(tmp_path) as (instance, backend, clock, agent):
            service = instance.desktop
            agents = [agent] + [
                (await instance._database.write(lambda u, index=index: u.create_agent(f"Space {index}")))["id"]
                for index in range(9)
            ]
            snapshots, owners = {}, {}
            for identity in agents[:4]:
                owners[identity] = await begin(service, identity)
                snapshots[identity] = await listed(service, identity, owners[identity])
            for identity in agents[4:]:
                owners[identity] = await begin(service, identity)
                await service.probe(identity, owners[identity])
            assert service.health()["cached_views"] == 8
            for identity in agents[:4]:
                assert (await service.status(identity))["snapshot_id"] == snapshots[identity]["snapshot_id"]
            prior = len(backend.calls)
            with pytest.raises(BusinessError) as error:
                await listed(service, agents[-1], owners[agents[-1]])
            assert error.value.code == "desktop_snapshot_capacity"
            assert len(backend.calls) == prior
            clock.advance(61)
            assert (await listed(service, agents[-1], owners[agents[-1]]))["items"]
            for identity in agents[:4]:
                assert (await service.status(identity))["snapshot_id"] is None

    asyncio.run(scenario())


def test_forget_during_requested_audit_prevents_any_new_native_dispatch(tmp_path, monkeypatch):
    async def scenario():
        async with prepared(tmp_path) as (instance, backend, _clock, agent):
            service = instance.desktop
            owner = await begin(service, agent)
            entered, release = asyncio.Event(), asyncio.Event()
            original = service._audit

            async def waiting(agent_id, operation_id, operation, *, completed=False, **metadata):
                await original(agent_id, operation_id, operation, completed=completed, **metadata)
                if operation == "targets" and not completed:
                    entered.set()
                    await release.wait()

            monkeypatch.setattr(service, "_audit", waiting)
            request = asyncio.create_task(listed(service, agent, owner))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                assert (await service.forget(agent, owner["view_id"]))["forgotten"]
            finally:
                release.set()
            with pytest.raises(BusinessError) as error:
                await request
            assert error.value.code == "desktop_view_changed"
            assert backend.calls == []
            assert (await service.status(agent))["snapshot_id"] is None

    asyncio.run(scenario())


def test_failed_reprobe_discards_previous_permissions_and_snapshots(tmp_path):
    async def scenario():
        async with prepared(tmp_path) as (instance, backend, _clock, agent):
            service = instance.desktop
            owner = await begin(service, agent)
            await service.probe(agent, owner)
            await listed(service, agent, owner)
            backend.errors["probe"] = BusinessError("fixture_probe_failed", "Injected unavailable desktop")
            with pytest.raises(BusinessError):
                await service.probe(agent, owner)
            status = await service.status(agent)
            assert status["capabilities"] is None and status["snapshot_id"] is None

    asyncio.run(scenario())


def test_cancelled_waiter_and_failed_close_keep_actual_observation_backend_database_and_instance_lock(tmp_path):
    async def scenario():
        async with prepared(tmp_path) as (instance, backend, _clock, agent):
            service = instance.desktop
            owner = await begin(service, agent)
            backend.hold("probe", after_dispatch=True)
            waiting = asyncio.create_task(service.probe(agent, owner))
            await asyncio.wait_for(backend.dispatched["probe"].wait(), 5)
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
            assert service.health()["pending"] == 1
            first = await instance.close(timeout_s=0.02)
            assert not first["complete"]
            assert not instance._database.metrics["closed"]
            lock = InstanceLock(instance.paths)
            with pytest.raises(BusinessError, match="already running"):
                lock.acquire()
            backend.close_blocked = True
            backend.release("probe", after_dispatch=True)
            await no_pending(service)
            second = await instance.close(timeout_s=0.1)
            assert not second["complete"] and not backend.closed
            assert service.health()["backend"]["cleanup_pending"] == 1
            assert not instance._database.metrics["closed"]
            with pytest.raises(BusinessError):
                lock.acquire()
            backend.close_blocked = False
            assert (await instance.close())["complete"]
            assert backend.closed
            lock.acquire()
            lock.close()

    asyncio.run(scenario())


def test_audit_and_debug_logs_correlate_operations_without_native_identity_or_window_contents(tmp_path):
    async def scenario():
        async with prepared(tmp_path) as (instance, backend, _clock, agent):
            backend.items = [
                target_identity(
                    native_id="native-identity-private-canary",
                    application_id="application-private-canary",
                    session_id="native-session-private-canary",
                )
            ]
            backend.report["session"]["identity"] = "native-session-private-canary"
            service = instance.desktop
            owner = await begin(service, agent)
            capture = await instance.operations.debug_start(agent, 300)
            probed = await service.probe(agent, owner)
            snapshot = await listed(service, agent, owner)
            verified = await service.verify(agent, verification(owner, snapshot))
            operations = {probed["operation_id"], snapshot["operation_id"], verified["operation_id"]}
            audit = [
                row for row in await instance.application.query("audit", agent) if row["kind"].startswith("desktop.")
            ]
            assert len(audit) == 6
            assert {row["operation_id"] for row in audit} == operations
            for row in audit:
                assert set(row["data"]) <= {"operation", "observation_only", "boot_id", "count", "truncated", "matches"}
            await asyncio.wait_for(asyncio.to_thread(instance._logging.sink.queue.join), 5)
            log_page = await instance.operations.debug_logs(agent, capture["operation_id"], {"limit": 100})
            logs = [row for row in log_page["items"] if row.get("operation") == "desktop"]
            assert {row["operation_id"] for row in logs} == operations
            assert all(row.get("agent_id") == agent and row.get("trace_id") for row in logs)
            combined = json.dumps({"audit": audit, "logs": log_page}, ensure_ascii=False)
            for private in (
                "native-identity-private-canary",
                "application-private-canary",
                "native-session-private-canary",
                "window_text",
            ):
                assert private not in combined
            await instance.operations.debug_stop(agent, capture["operation_id"])

    asyncio.run(scenario())
