"""Component physical-point and approved move/click business integration; native input/accounts all injected."""

import asyncio
import json
import sys
import uuid
from types import SimpleNamespace

import omni_test_support.v2.v2_linux_control_helpers as shared
import pytest
from omni_test_support.v2.desktop import begin, listed, verification
from omni_test_support.v2.desktop_actions import approve, queued
from omni_test_support.v2.execution import wait_state
from omni_test_support.v2.v2_linux_click_helpers import ClickBundle, click_factory, command, model
from omni_test_support.v2.v2_linux_control_helpers import EDIT
from omni_test_support.v2.v2_linux_desktop_helpers import WINDOW

from omni.adapters.platform.desktop import backend as host
from omni.adapters.platform.desktop.linux.linux_node_point import rectangle
from omni.adapters.workers.linux_action import run
from omni.adapters.workers.linux_desktop import perform
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError
from omni.domain.desktop import parse_request
from omni.domain.desktop_actions import action_result
from omni.domain.foreground import permit as foreground_permit


def test_component_hit_native_geometry_permit_move_pair_and_concrete_checked_readback():
    async def scenario():
        value = model()
        query = await command(value)
        bundle = ClickBundle(value)

        async def permit():
            assert not value.pointer_mutations and not bundle.input.input_requests
            assert bundle.bindings[0].focus is not None
            return True

        response = await perform(parse_request(query), permit=permit, **bundle.arguments)
        assert response["ok"] and response["result"]["status"] == "ok", response
        assert response["result"]["verification"] == "click_verified"
        action_result(response["result"], "click")
        assert value.pointer_mutations == ["click"]
        assert [item[4] for item in bundle.input.input_requests] == [6, 4, 5]
        assert all(item.closed for item in bundle.monitors + bundle.connections)

    asyncio.run(scenario())


@pytest.mark.parametrize("capability,state,initial", [(41, 4, True), (22, 23, False), (9, 10, True), (11, 12, False)])
def test_click_success_requires_the_specific_observable_checked_selected_expanded_or_focus_transition(
    capability, state, initial
):
    async def scenario():
        value = model()
        value.nodes[EDIT]["flags"][1] = 0
        value.nodes[EDIT]["flags"][capability // 32] |= 1 << (capability % 32)
        if initial:
            value.nodes[EDIT]["flags"][0] |= 1 << state
        value.click_state_bit = state
        query = await command(value)
        bundle = ClickBundle(value)
        response = await perform(parse_request(query), permit=lambda: asyncio.sleep(0, result=True), **bundle.arguments)
        assert response["ok"] and response["result"]["verification"] == "click_verified", response
        assert bool(value.nodes[EDIT]["flags"][0] & (1 << state)) is (not initial)
        assert value.pointer_mutations == ["click"] and [r[4] for r in bundle.input.input_requests] == [6, 4, 5]

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["self", "overlay", "bus", "contains", "scale", "offset"])
def test_incorrect_provider_hit_or_coordinate_contract_sends_no_native_input(fault):
    async def scenario():
        value = model()
        value.point_fault = fault
        query = await command(value)
        bundle = ClickBundle(value)
        response = await perform(parse_request(query), permit=lambda: asyncio.sleep(0, result=True), **bundle.arguments)
        assert response["ok"] and response["result"]["status"] == "denied", response
        assert not value.pointer_mutations and not bundle.input.input_requests

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault", ["denied", "hit", "bounds", "descriptor", "inactive", "password", "physical", "mapping"]
)
def test_revocation_or_drift_during_permit_does_not_move_or_press(fault):
    async def scenario():
        value = model()
        query = await command(value)
        bundle = ClickBundle(value)

        async def permit():
            if fault == "hit":
                value.point_fault = "overlay"
            elif fault == "bounds":
                value.nodes[EDIT]["bounds"][0] += 1
            elif fault == "descriptor":
                bundle.factory.attributes["avalonia-window-handle"] = "4194306"
            elif fault == "inactive":
                value.nodes[WINDOW]["flags"][0] &= ~(1 << 1)
            elif fault == "password":
                value.nodes[EDIT]["role"] = 40
            elif fault == "physical":
                bundle.input.event = bytes([35, 130]) + bytes(6) + bytes([13]) + bytes(23)
            elif fault == "mapping":
                bundle.input.maps[4] = bytes([2, 1, 3])
            return fault != "denied"

        response = await perform(parse_request(query), permit=permit, **bundle.arguments)
        assert response["ok"] and response["result"]["status"] == "denied", response
        assert not value.pointer_mutations and not bundle.input.input_requests
        assert all(item.closed for item in bundle.monitors + bundle.connections)

    asyncio.run(scenario())


@pytest.mark.parametrize("effect", ["unchanged", "foreign_value"])
def test_transport_and_unrelated_application_changes_remain_unknown_without_replay(effect):
    async def scenario():
        value = model()
        query = await command(value)
        bundle = ClickBundle(value)
        bundle.effect = effect
        response = await perform(parse_request(query), permit=lambda: asyncio.sleep(0, result=True), **bundle.arguments)
        assert response["ok"] and response["result"]["status"] == "unknown", response
        assert value.pointer_mutations == ["click"] and [item[4] for item in bundle.input.input_requests] == [6, 4, 5]
        assert all(item.closed for item in bundle.monitors + bundle.connections)

    asyncio.run(scenario())


@pytest.mark.parametrize("identity", ["request_id", "grant_id", "run_id", "operation_id"])
def test_ready_is_after_native_preflight_and_only_exact_parent_permit_allows_cursor_input(identity):
    async def scenario():
        value = model()
        query = await command(value)
        bundle = ClickBundle(value)
        reader = asyncio.StreamReader(limit=65536)
        reader.feed_data(json.dumps(query).encode() + b"\n")
        frames = []

        async def send(frame):
            parsed = json.loads(frame)
            frames.append(parsed)
            if parsed.get("phase") == "ready":
                assert bundle.bindings[0].focus is not None and not bundle.input.input_requests
                permitted = foreground_permit(query)
                permitted[identity] = str(uuid.uuid4())
                reader.feed_data(json.dumps(permitted).encode() + b"\n")
                reader.feed_eof()

        assert await run(reader, send, **bundle.arguments) == 0
        assert frames[-1]["result"]["status"] == "denied"
        assert not bundle.input.input_requests and not value.pointer_mutations

    asyncio.run(scenario())


def test_cancelled_business_waiter_retains_the_actual_button_barrier_and_both_connections_until_settled():
    async def scenario():
        value = model()
        query = await command(value)
        bundle = ClickBundle(value)
        original = bundle.input.after_move

        def block_after_button():
            original()
            bundle.input.query_started.clear()
            bundle.input.block_query = True

        bundle.input.after_move = block_after_button
        operation = asyncio.create_task(
            perform(parse_request(query), permit=lambda: asyncio.sleep(0, result=True), **bundle.arguments)
        )
        # Motion also has a QueryPointer; wait for the button-specific point.
        while not bundle.input.button_requests:
            await asyncio.sleep(0)
        await bundle.input.query_started.wait()
        operation.cancel()
        await asyncio.sleep(0)
        try:
            assert not operation.done()
            assert all(not item.closed for item in bundle.monitors + bundle.connections)
            bundle.input.query_release.set()
            with pytest.raises(asyncio.CancelledError):
                await operation
            assert all(item.closed for item in bundle.monitors + bundle.connections)
            assert [item[4] for item in bundle.input.input_requests] == [6, 4, 5]
        finally:
            bundle.input.query_release.set()

    asyncio.run(scenario())


def test_actual_owner_approval_pipe_sqlite_result_and_single_use_follow_the_click_path(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.setattr(host, "sys", SimpleNamespace(platform="linux", executable=sys.executable))
        value = model()
        bundles = []

        async def native_run(reader, send, **injected):
            bundle = ClickBundle(value, selected_factory=injected["factory"])
            bundles.append(bundle)
            return await run(reader, send, **bundle.arguments)

        monkeypatch.setattr(shared, "factory", click_factory)
        monkeypatch.setattr(shared, "run", native_run)
        spawn = shared.Spawn(value)
        backend = host.DesktopBackend(process_factory=spawn)
        instance = await open_instance(tmp_path / "home", capabilities=(), desktop_backend=backend)
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            turn = await instance._database.write(
                lambda unit: unit.accept_turn(agent, None, "native click fixture", "fixture", {})
            )
            view = await begin(instance.desktop, agent)
            snapshot = await listed(instance.desktop, agent, view)
            observation = await instance.desktop.observe(agent, verification(view, snapshot))
            node = next(node for node in observation["nodes"] if "click" in node.get("actions", []))
            request = {
                **verification(view, snapshot),
                "session_id": turn["session_id"],
                "request_id": str(uuid.uuid4()),
                "observation_id": observation["observation_id"],
                "node_id": node["node_id"],
                "kind": "click",
                "text": "",
                "takeover": True,
            }
            receipt, approval = await queued(instance, agent, request)
            assert not value.pointer_mutations
            await approve(instance, agent, approval)
            outcome = await wait_state(instance.application, agent, receipt["run_id"], {"succeeded", "failed"})
            assert outcome["state"] == "succeeded", outcome
            assert value.pointer_mutations == ["click"]
            assert (await instance.desktop_actions.submit(agent, request))["duplicate"]
            assert len(bundles) == 1 and [item[4] for item in bundles[0].input.input_requests] == [6, 4, 5]
            frames = spawn.owners[-1].process.stdin.frames
            assert len(frames) == 2 and frames[1]["phase"] == "permit"
            assert instance.desktop_actions.health()["leases"] == 0
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "value", [None, [], [0, 0, 1], [False, 0, 1, 1], [0, 0, -1, 2], [0, 0, 1, 0], [10000001, 0, 1, 1], [0, 0, 1.5, 1]]
)
def test_provider_geometry_requires_real_bounded_pixel_integers(value):
    with pytest.raises(BusinessError):
        rectangle(value)


def test_parent_close_after_click_permit_retains_native_release_worker_pipes_and_shared_slot(monkeypatch):
    async def scenario():
        monkeypatch.setattr(host, "sys", SimpleNamespace(platform="linux", executable=sys.executable))
        value = model()
        query = await command(value)
        prepared, button = asyncio.Event(), asyncio.Event()
        bundles, terminations = [], []
        original_terminate = shared.Owned.terminate

        async def native_run(reader, send, **injected):
            bundle = ClickBundle(value, selected_factory=injected["factory"])
            bundles.append(bundle)
            apply = bundle.input.after_move

            def after_button():
                apply()
                bundle.input.query_started.clear()
                bundle.input.block_query = True
                button.set()

            bundle.input.after_move = after_button
            prepared.set()
            return await run(reader, send, **bundle.arguments)

        async def terminated(owner, budget):
            assert owner.process.worker.done(), "Permitted pointer release owner must not be forcibly terminated"
            terminations.append(owner)
            return await original_terminate(owner, budget)

        monkeypatch.setattr(shared, "factory", click_factory)
        monkeypatch.setattr(shared, "run", native_run)
        monkeypatch.setattr(shared.Owned, "terminate", terminated)
        backend = host.DesktopBackend(process_factory=shared.Spawn(value))
        operation = asyncio.create_task(
            backend.foreground(query["target"], query["action"], before_dispatch=lambda: asyncio.sleep(0))
        )
        try:
            await prepared.wait()
            await button.wait()
            bundle = bundles[0]
            await bundle.input.query_started.wait()
            assert not await backend.close(0)
            await asyncio.sleep(0)
            assert len(backend._hosts) == 1 and next(iter(backend._hosts)).native_key_permitted
            assert not terminations and not operation.done()
            assert all(not item.closed for item in bundle.connections + bundle.monitors)
            bundle.input.query_release.set()
            outcome = await operation
            assert outcome["status"] == "ok" and value.pointer_mutations == ["click"]
            assert await backend.close(2) and len(terminations) == 1
        finally:
            if bundles:
                bundles[0].input.query_release.set()
            await asyncio.gather(operation, return_exceptions=True)
            assert await backend.close(2)

    asyncio.run(scenario())
