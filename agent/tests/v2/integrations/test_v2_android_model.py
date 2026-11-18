"""Model requests per-run app access and each input through real approvals; only fake local ADB devices."""

from __future__ import annotations

from omni_test_support.v2.android_model import (
    ACCESS_SPEC as ACCESS_SPEC,
)
from omni_test_support.v2.android_model import (
    MODEL_SPEC as MODEL_SPEC,
)
from omni_test_support.v2.android_model import (
    OBSERVE_SPEC as OBSERVE_SPEC,
)
from omni_test_support.v2.android_model import (
    SERIAL as SERIAL,
)
from omni_test_support.v2.android_model import (
    AndroidModel as AndroidModel,
)
from omni_test_support.v2.android_model import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.android_model import (
    Invocation as Invocation,
)
from omni_test_support.v2.android_model import (
    InvocationContext as InvocationContext,
)
from omni_test_support.v2.android_model import (
    ModelReply as ModelReply,
)
from omni_test_support.v2.android_model import (
    Outcome as Outcome,
)
from omni_test_support.v2.android_model import (
    access_request as access_request,
)
from omni_test_support.v2.android_model import (
    approve as approve,
)
from omni_test_support.v2.android_model import (
    approve_tool as approve_tool,
)
from omni_test_support.v2.android_model import (
    asynccontextmanager as asynccontextmanager,
)
from omni_test_support.v2.android_model import (
    asyncio as asyncio,
)
from omni_test_support.v2.android_model import (
    dt as dt,
)
from omni_test_support.v2.android_model import (
    fake_server as fake_server,
)
from omni_test_support.v2.android_model import (
    input_params as input_params,
)
from omni_test_support.v2.android_model import (
    json as json,
)
from omni_test_support.v2.android_model import (
    model_instance as model_instance,
)
from omni_test_support.v2.android_model import (
    open_instance as open_instance,
)
from omni_test_support.v2.android_model import (
    prepared as prepared,
)
from omni_test_support.v2.android_model import (
    pytest as pytest,
)
from omni_test_support.v2.android_model import (
    request_for as request_for,
)
from omni_test_support.v2.android_model import (
    s as s,
)
from omni_test_support.v2.android_model import (
    scroll_verified as scroll_verified,
)
from omni_test_support.v2.android_model import (
    update as update,
)
from omni_test_support.v2.android_model import (
    uuid as uuid,
)
from omni_test_support.v2.android_model import (
    wait_state as wait_state,
)


def test_model_access_and_input_are_separately_approved_and_application_reads_are_scoped(tmp_path):
    async def scenario():
        model = AndroidModel()
        async with model_instance(tmp_path, model) as (instance, fake, agent, _, _):
            before = len(fake.services)
            turn = await instance.application.submit_turn(
                agent, None, "model-android", "Use the explicitly supplied Android device reference"
            )
            await wait_state(instance.application, agent, turn["run_id"], {"waiting_approval"})
            assert len(fake.services) == before and not fake.writes
            access_approval = await approve_tool(instance, agent, turn["run_id"], ACCESS_SPEC.name)
            await wait_state(instance.application, agent, turn["run_id"], {"waiting_approval"})
            assert not fake.writes
            input_approval = await approve_tool(instance, agent, turn["run_id"], MODEL_SPEC.name)
            assert access_approval["action_id"] != input_approval["action_id"]
            run = await wait_state(instance.application, agent, turn["run_id"], {"succeeded", "failed"})
            assert run["state"] == "succeeded", run
            assert fake.text == "hello model" and len(fake.writes) == 1
            apps = json.loads(model.tool_bodies[-1])
            assert apps["items"] == ["org.fixture"] and "org.private" not in model.tool_bodies[-1]
            assert "android_input" not in model.catalogs[0]
            assert all(t in model.catalogs[0] for t in [ACCESS_SPEC.name, OBSERVE_SPEC.name, MODEL_SPEC.name])
            accesses = await instance.android.access_page(agent)
            assert len(accesses["items"]) == 1 and accesses["items"][0]["availability"] != "available"
            row = accesses["items"][0]
            with pytest.raises(BusinessError, match="data constraint"):
                await instance._database.write(
                    lambda u: u.connection.execute(
                        update(s.android_access_kinds)
                        .where(s.android_access_kinds.c.access_id == row["id"])
                        .values(kind="launch")
                    )
                )

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["access", "device", "config", "context", "target_preview", "kind_scope"])
def test_revoke_or_changed_scope_between_observation_and_input_approval_never_writes(tmp_path, fault):
    async def scenario():
        model = AndroidModel()
        async with model_instance(tmp_path, model) as (instance, fake, agent, cfg, grant):
            if fault == "kind_scope":
                model.request["kinds"] = []
            turn = await instance.application.submit_turn(agent, None, "revoke-model", "Use the provided device")
            await approve_tool(instance, agent, turn["run_id"], ACCESS_SPEC.name)
            await wait_state(instance.application, agent, turn["run_id"], {"waiting_approval"})
            access = (await instance.android.access_page(agent))["items"][0]
            if fault == "access":
                await instance.android.revoke_access(agent, access["id"], access["revision"])
            elif fault == "device":
                await instance.android.revoke(agent, grant["id"], grant["revision"])
            elif fault == "config":
                await instance.configurations.save(agent, cfg["id"], cfg["name"], False, cfg["value"], cfg["revision"])
            elif fault == "context":
                await instance._database.write(
                    lambda u: u.connection.execute(
                        update(s.sessions)
                        .where(s.sessions.c.id == turn["session_id"])
                        .values(content_epoch=s.sessions.c.content_epoch + 1)
                    )
                )
            elif fault == "target_preview":
                # The model supplies an incorrect approval label; host must bind it to its own observed identity.
                original = instance.android._observations[model.view["observation_id"]]
                original["nodes"][model.view["nodes"][0]["node_id"]]["resource_id"] = "changed-resource"
            approval = next(a for a in await instance.application.approvals(agent) if a["run_id"] == turn["run_id"])
            try:
                await instance.application.approve(agent, approval["id"], True, approval["policy_revision"])
            except BusinessError:
                assert fault == "context"
            await wait_state(instance.application, agent, turn["run_id"], {"succeeded", "failed", "interrupted"})
            assert not fake.writes

    asyncio.run(scenario())


def test_unapproved_foreground_app_is_rejected_before_ui_dump_and_no_text_reaches_model(tmp_path):
    async def scenario():
        model = AndroidModel(read_only=True, packages=["org.second"])
        async with model_instance(tmp_path, model) as (instance, fake, agent, _, _):
            turn = await instance.application.submit_turn(
                agent, None, "wrong-package", "Observe only the permitted application"
            )
            await approve_tool(instance, agent, turn["run_id"], ACCESS_SPEC.name)
            await wait_state(instance.application, agent, turn["run_id"], {"succeeded", "failed"})
            assert not any("uiautomator dump" in service for service in fake.services)
            assert "hello" not in "".join(model.tool_bodies) and not fake.writes

    asyncio.run(scenario())


def test_model_observation_is_bound_to_run_session_and_access_and_cannot_be_used_as_owner_node(tmp_path):
    async def scenario():
        model = AndroidModel(read_only=True)
        model.hold.clear()
        async with model_instance(tmp_path, model) as (instance, fake, agent, _, grant):
            turn = await instance.application.submit_turn(agent, None, "scope-model", "Observe within this run only")
            await approve_tool(instance, agent, turn["run_id"], ACCESS_SPEC.name)
            await asyncio.wait_for(model.entered.wait(), 6)
            access = (await instance.android.access_page(agent))["items"][0]
            assert access["availability"] == "available"
            observed = json.loads(model.tool_bodies[-1])
            before = len(fake.services)
            wrong = InvocationContext(agent, turn["run_id"], str(uuid.uuid4()), access["policy_revision"])
            result = await instance.android.invoke(
                OBSERVE_SPEC.name, {"access_id": access["id"]}, operation_id=str(uuid.uuid4()), context=wrong
            )
            assert result.status == Outcome.DENIED and len(fake.services) == before
            request = {
                "consent": True,
                "grant_id": grant["id"],
                "observation_id": observed["observation_id"],
                "node_id": observed["nodes"][0]["node_id"],
                "session_id": turn["session_id"],
                "request_id": str(uuid.uuid4()),
                "kind": "tap",
                "text": "",
            }
            with pytest.raises(BusinessError, match="another run or access"):
                await instance.android.submit(agent, request)
            model.hold.set()
            await wait_state(instance.application, agent, turn["run_id"], {"succeeded", "failed"})

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "kind,params",
    [
        ("swipe", {"direction": "up", "duration_ms": 600}),
        ("long_press", {"duration_ms": 700}),
        ("key", {"key": "BACK"}),
        ("launch", {"package": "org.second"}),
    ],
)
def test_owner_extended_actions_and_application_inventory_use_fixed_commands(tmp_path, kind, params):
    async def scenario():
        async with prepared(tmp_path) as (instance, fake, agent, _, session, grant):
            apps = await instance.android.apps(agent, grant["id"])
            assert apps["items"] == sorted(fake.packages)
            request, _ = await request_for(instance, agent, session, grant, kind)
            request["params"] = params
            turn = await instance.android.submit(agent, request)
            instance.application.notify()
            await approve(instance, agent, turn)
            run = await wait_state(instance.application, agent, turn["run_id"], {"succeeded", "failed"})
            assert len(fake.writes) == 1
            assert run["state"] == ("succeeded" if kind == "launch" else "failed"), run
            if kind == "launch":
                assert fake.package == "org.second"
            else:
                action = await instance._database.read(
                    lambda u: u.action(agent, u.owner_command(agent, turn["run_id"])["action_id"])
                )
                assert action["effect"] == "unknown"

    asyncio.run(scenario())


def test_android_input_scope_validators_reject_wildcards_extra_commands_and_unbounded_durations():
    for kind, params in [
        ("swipe", {"direction": "diagonal", "duration_ms": 100}),
        ("long_press", {"duration_ms": 60000}),
        ("key", {"key": "BACK;echo bad"}),
        ("launch", {"package": "org.x;echo bad"}),
    ]:
        with pytest.raises(BusinessError):
            input_params(kind, params)
    value = {
        "grant_id": str(uuid.uuid4()),
        "target_serial": SERIAL,
        "target_user": 0,
        "packages": ["*"],
        "kinds": [],
        "ttl_seconds": 900,
    }
    with pytest.raises(BusinessError):
        access_request(value)


def test_verified_scroll_requires_stable_children_moving_in_the_approved_direction(tmp_path):
    async def scenario():
        async with prepared(tmp_path) as (instance, fake, agent, _, session, grant):
            fake.scrolling = True
            request, _ = await request_for(instance, agent, session, grant, "swipe")
            request["params"] = {"direction": "up", "duration_ms": 600}
            receipt = await instance.android.submit(agent, request)
            instance.application.notify()
            await approve(instance, agent, receipt)
            run = await wait_state(instance.application, agent, receipt["run_id"], {"succeeded", "failed"})
            assert run["state"] == "succeeded" and len(fake.writes) == 1
            action = await instance._database.read(
                lambda u: u.action(agent, u.owner_command(agent, receipt["run_id"])["action_id"])
            )
            assert action["result"]["verification"] == "scroll_content_moved"

    asyncio.run(scenario())


def test_scroll_text_changes_duplicates_or_opposite_motion_are_not_verification():
    def node(path, name, y):
        return {
            "path": path,
            "resource_id": name,
            "class": "TextView",
            "text": name,
            "description": "",
            "password": False,
            "bounds": [0, y, 20, y + 10],
        }

    container = {"path": [0]}
    old = [node([0, 0], "first", 40), node([0, 1], "second", 80)]
    moved = [node([0, 0], "first", 30), node([0, 1], "second", 70)]
    assert scroll_verified(old, moved, container, "up")
    assert not scroll_verified(old, moved, container, "down")
    assert not scroll_verified(old, [node([0, 0], "changed", 30), node([0, 1], "other", 70)], container, "up")
    assert not scroll_verified(old, [*moved, moved[0]], container, "up")


def test_installed_application_pagination_is_stable_and_does_not_scan_other_devices(tmp_path):
    async def scenario():
        async with prepared(tmp_path) as (instance, fake, agent, _, _, grant):
            fake.packages = [f"org.fixture.p{i:03}" for i in range(230)]
            first = await instance.android.apps(agent, grant["id"])
            second = await instance.android.apps(agent, grant["id"], first["next_after"])
            assert len(first["items"]) == 200 and first["truncated"]
            assert len(second["items"]) == 30 and second["next_after"] is None
            assert first["items"] + second["items"] == fake.packages
            assert not fake.writes

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["unknown_source", "scope_insertion"])
def test_unknown_access_action_or_unapproved_relational_scope_cannot_authorize_reads(tmp_path, fault):
    async def scenario():
        model = AndroidModel(read_only=True)
        model.hold.clear()
        async with model_instance(tmp_path, model) as (instance, fake, agent, _, _):
            turn = await instance.application.submit_turn(
                agent, None, "scope-integrity", "Read the approved application"
            )
            await approve_tool(instance, agent, turn["run_id"], ACCESS_SPEC.name)
            await asyncio.wait_for(model.entered.wait(), 6)
            row = (await instance.android.access_page(agent))["items"][0]
            if fault == "unknown_source":
                await instance._database.write(
                    lambda u: u.connection.execute(
                        update(s.actions)
                        .where(s.actions.c.id == row["source_action_id"])
                        .values(state="unknown", effect="unknown")
                    )
                )
            else:
                await instance._database.write(
                    lambda u: u._record(
                        s.android_access_packages, {"access_id": row["id"], "package": "org.private"}, agent
                    )
                )
            before = len(fake.services)
            context = InvocationContext(agent, turn["run_id"], turn["session_id"], row["policy_revision"])
            result = await instance.android.invoke(
                OBSERVE_SPEC.name, {"access_id": row["id"]}, operation_id=str(uuid.uuid4()), context=context
            )
            assert result.status == Outcome.DENIED and len(fake.services) == before
            model.hold.set()
            await wait_state(instance.application, agent, turn["run_id"], {"succeeded", "failed"})

    asyncio.run(scenario())


def test_expired_run_scope_is_denied_even_while_the_execution_lease_remains_valid(tmp_path):
    async def scenario():
        model = AndroidModel(read_only=True)
        model.hold.clear()
        async with model_instance(tmp_path, model) as (instance, _, agent, _, _):
            turn = await instance.application.submit_turn(
                agent, None, "expiry-scope", "Observe this run's approved device"
            )
            await approve_tool(instance, agent, turn["run_id"], ACCESS_SPEC.name)
            await asyncio.wait_for(model.entered.wait(), 6)
            row = (await instance.android.access_page(agent))["items"][0]
            future = dt.datetime.fromisoformat(row["expires_at"]) + dt.timedelta(seconds=1)

            class FutureClock:
                def now(self):
                    return future.isoformat(timespec="microseconds")

            def expired(u):
                # Keep the isolated run lease valid, so the assertion specifically exercises access expiry.
                u.connection.execute(
                    update(s.leases)
                    .where(s.leases.c.run_id == turn["run_id"])
                    .values(expires_at=(future + dt.timedelta(hours=1)).isoformat(timespec="microseconds"))
                )
                u.clock = FutureClock()
                with pytest.raises(BusinessError, match="expired, was revoked"):
                    u.android_access_authorize(agent, turn["run_id"], row["id"])

            await instance._database.write(expired)
            model.hold.set()
            await wait_state(instance.application, agent, turn["run_id"], {"succeeded", "failed"})

    asyncio.run(scenario())
