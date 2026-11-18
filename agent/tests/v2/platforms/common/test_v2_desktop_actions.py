"""Semantic actions use real V2 storage/approvals and isolated injected native effects."""

from __future__ import annotations

from omni_test_support.v2.desktop_actions import (
    POLICY as POLICY,
)
from omni_test_support.v2.desktop_actions import (
    ActionsBackend as ActionsBackend,
)
from omni_test_support.v2.desktop_actions import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.desktop_actions import (
    FixtureDesktopBackend as FixtureDesktopBackend,
)
from omni_test_support.v2.desktop_actions import (
    action_result as action_result,
)
from omni_test_support.v2.desktop_actions import (
    approve as approve,
)
from omni_test_support.v2.desktop_actions import (
    asynccontextmanager as asynccontextmanager,
)
from omni_test_support.v2.desktop_actions import (
    asyncio as asyncio,
)
from omni_test_support.v2.desktop_actions import (
    begin as begin,
)
from omni_test_support.v2.desktop_actions import (
    hashlib as hashlib,
)
from omni_test_support.v2.desktop_actions import (
    listed as listed,
)
from omni_test_support.v2.desktop_actions import (
    open_instance as open_instance,
)
from omni_test_support.v2.desktop_actions import (
    owner_action as owner_action,
)
from omni_test_support.v2.desktop_actions import (
    prepared as prepared,
)
from omni_test_support.v2.desktop_actions import (
    pytest as pytest,
)
from omni_test_support.v2.desktop_actions import (
    queued as queued,
)
from omni_test_support.v2.desktop_actions import (
    s as s,
)
from omni_test_support.v2.desktop_actions import (
    select as select,
)
from omni_test_support.v2.desktop_actions import (
    update as update,
)
from omni_test_support.v2.desktop_actions import (
    uuid as uuid,
)
from omni_test_support.v2.desktop_actions import (
    verification as verification,
)
from omni_test_support.v2.desktop_actions import (
    wait_state as wait_state,
)


def test_owner_action_without_model_has_one_body_approval_and_verified_completion(tmp_path):
    async def scenario():
        async with prepared(tmp_path) as (instance, backend, agent, request):
            receipt, approval = await queued(instance, agent, request)
            assert backend.action_calls == []
            command = await instance._database.read(lambda u: u.owner_command(agent, receipt["run_id"]))
            assert command["arguments"] is None and command["action_id"] == approval["action_id"]
            with pytest.raises(BusinessError):
                await instance._database.read(lambda u: u.assert_model_authorized(agent, receipt["run_id"]))
            await approve(instance, agent, approval)
            run = await wait_state(instance.application, agent, receipt["run_id"], {"succeeded", "failed"})
            assert run["state"] == "succeeded", run
            assert len(backend.action_calls) == 1
            assert backend.action_calls[0]["text"] == request["text"]
            assert await instance._database.read(lambda u: u.connection.execute(select(s.model_calls)).first()) is None
            assert instance.desktop_actions.health()["leases"] == 0
            replay = await instance.desktop_actions.submit(agent, request)
            assert replay["duplicate"] and replay["run_id"] == receipt["run_id"]
            with pytest.raises(BusinessError) as error:
                await instance.desktop_actions.submit(agent, {**request, "text": "changed"})
            assert error.value.code == "idempotency_conflict"

    asyncio.run(scenario())


@pytest.mark.parametrize("revoke", ["view", "session", "cancel", "policy"])
def test_revocation_while_native_helper_queued_prevents_dispatch(tmp_path, revoke):
    async def scenario():
        async with prepared(tmp_path) as (instance, backend, agent, request):
            backend.action_gate.clear()
            receipt, approval = await queued(instance, agent, request)
            await approve(instance, agent, approval)
            await asyncio.wait_for(backend.action_entered.wait(), 5)
            if revoke == "view":
                await instance.desktop.forget(agent, request["view_id"])
            elif revoke == "session":
                await instance._database.write(
                    lambda u: u.connection.execute(
                        update(s.sessions)
                        .where(s.sessions.c.id == request["session_id"])
                        .values(content_epoch=s.sessions.c.content_epoch + 1)
                    )
                )
            elif revoke == "cancel":
                await instance.application.cancel(agent, receipt["run_id"])
            else:
                await instance._database.write(
                    lambda u: u.connection.execute(
                        update(s.settings)
                        .where(s.settings.c.agent_id == agent)
                        .values(permission_epoch=s.settings.c.permission_epoch + 1)
                    )
                )
            backend.action_gate.set()
            await wait_state(instance.application, agent, receipt["run_id"], {"failed", "cancelled"})
            async with asyncio.timeout(5):
                while instance.desktop_actions.health()["pending"]:
                    await asyncio.sleep(0.01)
            assert backend.action_calls == []

    asyncio.run(scenario())


def test_dispatched_unknown_retains_lease_and_blocks_review_until_reclaimed(tmp_path):
    async def scenario():
        async with prepared(tmp_path) as (instance, backend, agent, request):
            backend.outcome = {
                "status": "unknown",
                "effect": "unknown",
                "error_code": "desktop_postcondition_unverified",
                "verification": "not_verified",
            }
            backend.close_blocked = True
            receipt, approval = await queued(instance, agent, request)
            await approve(instance, agent, approval)
            run = await wait_state(instance.application, agent, receipt["run_id"], {"failed"})
            assert run["error_code"] == "action_outcome_unknown" and len(backend.action_calls) == 1
            assert not instance.desktop_actions.quiescent() and instance.desktop_actions.health()["leases"] == 1
            action = await instance._database.read(lambda u: u.action(agent, approval["action_id"]))
            assert action["state"] == "unknown" and action["effect"] == "unknown"
            await instance.desktop_actions.submit(agent, request)
            assert len(backend.action_calls) == 1
            backend.close_blocked = False

    asyncio.run(scenario())


def test_command_identity_database_constraint_and_cross_agent_scope(tmp_path):
    async def scenario():
        async with prepared(tmp_path) as (instance, backend, agent, request):
            receipt, _approval = await queued(instance, agent, request)
            with pytest.raises(BusinessError) as error:
                await instance._database.write(
                    lambda u: u.connection.execute(
                        update(s.owner_commands)
                        .where(s.owner_commands.c.run_id == receipt["run_id"])
                        .values(boot_id=str(uuid.uuid4()))
                    )
                )
            assert error.value.code == "data_constraint"
            other = (await instance._database.write(lambda u: u.create_agent("other")))["id"]
            with pytest.raises(BusinessError):
                await instance.desktop_actions.submit(other, request)
            await instance.application.cancel(agent, receipt["run_id"])
            assert backend.action_calls == []

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "kind,text", [("click", ""), ("invoke", "unexpected"), ("set_text", "x" * 513), ("set_text", "secret\0")]
)
def test_semantic_contract_rejects_unbounded_or_unsupported_action(kind, text):
    value = {
        k: str(uuid.uuid4())
        for k in ("view_id", "snapshot_id", "target_id", "observation_id", "node_id", "session_id", "request_id")
    }
    with pytest.raises(BusinessError):
        owner_action({**value, "consent": True, "kind": kind, "text": text})


def test_result_cannot_claim_success_without_effect_and_postcondition():
    with pytest.raises(BusinessError):
        action_result({"status": "ok", "effect": "none", "error_code": "", "verification": "not_verified"})
    assert POLICY == "desktop-semantic-1"


def test_draining_application_refuses_owner_command_before_database_admission(tmp_path):
    async def scenario():
        async with prepared(tmp_path) as (instance, backend, agent, request):
            instance.application.stop_accepting()
            with pytest.raises(BusinessError) as error:
                await instance.desktop_actions.submit(agent, request)
            assert error.value.code == "not_ready"
            assert (
                await instance._database.read(lambda u: u.connection.execute(select(s.owner_commands)).first()) is None
            )
            assert not backend.action_calls

    asyncio.run(scenario())


def test_restart_interrupts_queued_owner_command_without_replaying_or_retaining_body(tmp_path):
    from omni.adapters.persistence.database import Database
    from omni.adapters.primitives import SystemClock, UUIDs

    async def scenario():
        path = tmp_path / "omni.db"
        db = Database(path, SystemClock(), UUIDs(), boot_id=str(uuid.uuid4()))
        try:

            def seed(u):
                u.initialize()
                agent = u.query("agents", "")[0]["id"]
                session = u._record(s.sessions, {"title": "fixture", "revision": 1, "next_seq": 0}, agent)
                args = {
                    "binding_id": str(uuid.uuid4()),
                    "kind": "set_text",
                    "text": "private payload",
                    "target_application": "fixture",
                    "target_role": "Edit",
                    "policy": POLICY,
                }
                receipt = u.accept_owner_command(agent, session["id"], str(uuid.uuid4()), "a" * 64, args)
                return agent, receipt

            agent, receipt = await db.write(seed)
        finally:
            await db.close()
        db = Database(path, SystemClock(), UUIDs(), boot_id=str(uuid.uuid4()))
        try:
            await db.write(lambda u: u.recover())
            run = await db.read(lambda u: u.get_run(agent, receipt["run_id"]))
            command = await db.read(lambda u: u.owner_command(agent, receipt["run_id"]))
            assert run["state"] == "interrupted" and run["error_code"] == "desktop_binding_expired"
            assert command["arguments"] is None
            assert await db.write(lambda u: u.claim_runs(4)) == []
            assert await db.read(lambda u: u.connection.execute(select(s.actions)).first()) is None
        finally:
            await db.close()

    asyncio.run(scenario())
