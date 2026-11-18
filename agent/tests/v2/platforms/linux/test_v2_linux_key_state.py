"""Nontext Enter results require a sealed state and one exact bounded-tree change; native ports are injected."""

import asyncio
import sys
import uuid
from types import SimpleNamespace

import omni_test_support.v2.v2_linux_control_helpers as shared
import pytest
from omni_test_support.v2.desktop import begin, listed, verification
from omni_test_support.v2.desktop_actions import approve, queued
from omni_test_support.v2.execution import wait_state
from omni_test_support.v2.v2_linux_control_helpers import BUTTON, EDIT
from omni_test_support.v2.v2_linux_desktop_helpers import APP, WINDOW, node
from omni_test_support.v2.v2_linux_key_helpers import Bundle, command, model, profile_factory

from omni.adapters.platform.desktop import backend as host
from omni.adapters.platform.desktop.linux.linux_atspi import AtspiDesktop
from omni.adapters.platform.desktop.linux.linux_semantic import Semantics
from omni.adapters.platform.desktop.linux.linux_session import SessionInspector
from omni.adapters.workers.linux_action import run
from omni.adapters.workers.linux_desktop import perform
from omni.composition.instance import open_instance
from omni.domain.desktop import parse_request
from omni.domain.desktop_actions import action_result


def flag(value, path, bit, enabled):
    words = value.nodes[path]["flags"]
    while len(words) <= bit // 32:
        words.append(0)
    if enabled:
        words[bit // 32] |= 1 << (bit % 32)
    else:
        words[bit // 32] &= ~(1 << (bit % 32))


def stateful(capability=41, state=4, enabled=False):
    value = model()
    flag(value, EDIT, 12, False)
    for bit in (11, 12, capability):
        flag(value, BUTTON, bit, True)
    flag(value, BUTTON, state, enabled)
    return value


async def state_command(value):
    query = await command(value, "Enter")
    factory = profile_factory(value)
    desktop = AtspiDesktop(SessionInspector(factory.ops, factory))
    await desktop.setup()
    try:
        query["action"]["node"] = (await Semantics(desktop).resolve(query["target"], [1]))[-1]
    finally:
        assert await desktop.close()
    return query


@pytest.mark.parametrize("capability,state", [(41, 4), (22, 23), (9, 10)])
@pytest.mark.parametrize("enabled", [False, True])
def test_enter_verifies_one_actual_state_flip_on_the_approved_nontext_control(capability, state, enabled):
    async def scenario():
        value = stateful(capability, state, enabled)
        query = await state_command(value)
        bundle = Bundle(value, "Enter")

        def apply():
            value.key_mutations.append("Enter")
            flag(value, BUTTON, state, not enabled)

        bundle.input.after_copy = apply

        async def permit():
            assert not value.key_mutations
            return True

        response = await perform(parse_request(query), permit=permit, **bundle.arguments)
        assert response["ok"] and response["result"]["status"] == "ok", response
        assert response["result"]["verification"] == "key_verified"
        action_result(response["result"], "press_key")
        assert value.key_mutations == ["Enter"] and len(bundle.input.input_requests) == 2
        assert all(item.closed for item in bundle.connections + bundle.monitors)
        assert not value.mutations

    asyncio.run(scenario())


def test_observed_state_is_in_approved_proof_and_readable_label_is_retained():
    async def scenario():
        value = stateful()
        value.nodes[BUTTON]["text"] = "Checkbox label"
        factory = profile_factory(value)
        desktop = AtspiDesktop(SessionInspector(factory.ops, factory))
        await desktop.setup()
        try:
            target = await desktop.target((APP, WINDOW))
            first = await Semantics(desktop).resolve(target, [1])
            assert first[3]["press_key"]["state"] == {"4": False}
            flag(value, BUTTON, 4, True)
            after = await Semantics(desktop).resolve(target, [1])
            assert after[3]["press_key"]["state"] == {"4": True}
            assert first[-1]["actions_sha256"] != after[-1]["actions_sha256"]
            assert first[-1]["value_sha256"] == after[-1]["value_sha256"]
        finally:
            assert await desktop.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["approved_state", "other_name", "other_flags", "denied"])
def test_approval_wait_revalidates_sealed_state_and_complete_tree_before_send(fault):
    async def scenario():
        value = stateful()
        query = await state_command(value)
        bundle = Bundle(value, "Enter")

        async def permit():
            if fault == "approved_state":
                flag(value, BUTTON, 4, True)
            elif fault == "other_name":
                value.nodes[EDIT]["name"] = "changed"
            elif fault == "other_flags":
                flag(value, EDIT, 23, True)
            return fault != "denied"

        response = await perform(parse_request(query), permit=permit, **bundle.arguments)
        assert response["ok"] and response["result"]["status"] == "denied", response
        assert not bundle.input.input_requests and not value.key_mutations
        assert all(item.closed for item in bundle.connections + bundle.monitors)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault",
    [
        "unchanged",
        "other_name",
        "other_text",
        "other_flags",
        "focus",
        "capability",
        "extra_state",
        "two_states",
        "child",
    ],
)
def test_unrelated_or_ambiguous_postconditions_are_unknown_without_second_press(fault):
    async def scenario():
        value = stateful()
        if fault == "two_states":
            flag(value, BUTTON, 9, True)
        query = await state_command(value)
        bundle = Bundle(value, "Enter")

        def apply():
            value.key_mutations.append("Enter")
            if fault == "unchanged":
                return
            flag(value, BUTTON, 4, True)
            if fault == "other_name":
                value.nodes[EDIT]["name"] = "changed"
            elif fault == "other_text":
                value.nodes[EDIT]["text"] = "changed"
            elif fault == "other_flags":
                flag(value, EDIT, 23, True)
            elif fault == "focus":
                flag(value, BUTTON, 12, False)
                flag(value, EDIT, 12, True)
            elif fault == "capability":
                flag(value, BUTTON, 41, False)
            elif fault == "extra_state":
                flag(value, BUTTON, 23, True)
            elif fault == "two_states":
                flag(value, BUTTON, 10, True)
            elif fault == "child":
                value.nodes[WINDOW]["children"].append([APP, "/extra"])
                value.nodes["/extra"], value.parents["/extra"] = node(31), WINDOW

        bundle.input.after_copy = apply
        response = await perform(parse_request(query), permit=lambda: asyncio.sleep(0, result=True), **bundle.arguments)
        assert response["ok"] and response["result"]["status"] == response["result"]["effect"] == "unknown", response
        assert value.key_mutations == ["Enter"] and len(bundle.input.input_requests) == 2
        assert all(item.closed for item in bundle.connections + bundle.monitors)

    asyncio.run(scenario())


def test_non_enter_key_cannot_claim_success_from_an_unrelated_state_flip():
    async def scenario():
        value = stateful()
        query = await state_command(value)
        query["action"]["text"] = "Left"
        bundle = Bundle(value, "Left")

        def apply():
            value.key_mutations.append("Left")
            flag(value, BUTTON, 4, True)

        bundle.input.after_copy = apply
        response = await perform(parse_request(query), permit=lambda: asyncio.sleep(0, result=True), **bundle.arguments)
        assert response["ok"] and response["result"]["status"] == "unknown", response
        assert value.key_mutations == ["Left"] and len(bundle.input.input_requests) == 2

    asyncio.run(scenario())


def test_editable_control_keeps_text_caret_contract_even_when_checkable():
    async def scenario():
        value = model()
        flag(value, EDIT, 41, True)
        query = await command(value, "Enter")
        bundle = Bundle(value, "Enter")
        response = await perform(parse_request(query), permit=lambda: asyncio.sleep(0, result=True), **bundle.arguments)
        assert response["ok"] and response["result"]["status"] == "ok", response
        assert value.nodes[EDIT]["text"] == "before\n" and value.key_mutations == ["Enter"]

    asyncio.run(scenario())


def test_cancelled_state_action_keeps_original_release_and_both_connections_until_settled():
    async def scenario():
        value = stateful()
        query = await state_command(value)
        bundle = Bundle(value, "Enter")

        def apply():
            value.key_mutations.append("Enter")
            flag(value, BUTTON, 4, True)
            bundle.input.block_query = True

        bundle.input.after_copy = apply
        caller = asyncio.create_task(
            perform(parse_request(query), permit=lambda: asyncio.sleep(0, result=True), **bundle.arguments)
        )
        try:
            await bundle.input.query_started.wait()
            caller.cancel()
            await asyncio.sleep(0)
            assert not caller.done() and all(not item.closed for item in bundle.connections + bundle.monitors)
            bundle.input.query_release.set()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert value.key_mutations == ["Enter"] and len(bundle.input.input_requests) == 2
            assert all(item.closed for item in bundle.connections + bundle.monitors)
        finally:
            bundle.input.query_release.set()
            await asyncio.gather(caller, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("capability,state", [(41, 4), (22, 23), (9, 10)])
def test_sqlite_approval_exact_permit_and_duplicate_turn_execute_one_state_action(
    tmp_path, monkeypatch, capability, state
):
    async def scenario():
        monkeypatch.setattr(host, "sys", SimpleNamespace(platform="linux", executable=sys.executable))
        value, bundles = stateful(capability, state), []

        async def native_run(reader, send, **injected):
            bundle = Bundle(value, "Enter", selected_factory=injected["factory"])
            bundles.append(bundle)

            def apply():
                value.key_mutations.append("Enter")
                flag(value, BUTTON, state, True)

            bundle.input.after_copy = apply
            return await run(reader, send, **bundle.arguments)

        monkeypatch.setattr(shared, "factory", profile_factory)
        monkeypatch.setattr(shared, "run", native_run)
        spawn = shared.Spawn(value)
        backend = host.DesktopBackend(process_factory=spawn)
        instance = await open_instance(tmp_path / "home", capabilities=(), desktop_backend=backend)
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            turn = await instance._database.write(
                lambda unit: unit.accept_turn(agent, None, "state-key", "fixture", {})
            )
            view = await begin(instance.desktop, agent)
            snapshot = await listed(instance.desktop, agent, view)
            observation = await instance.desktop.observe(agent, verification(view, snapshot))
            observed = next(item for item in observation["nodes"] if item["name"] == "fixture-button")
            request = {
                **verification(view, snapshot),
                "session_id": turn["session_id"],
                "request_id": str(uuid.uuid4()),
                "observation_id": observation["observation_id"],
                "node_id": observed["node_id"],
                "kind": "press_key",
                "text": "Enter",
                "takeover": True,
            }
            receipt, approval = await queued(instance, agent, request)
            assert not value.key_mutations
            await approve(instance, agent, approval)
            outcome = await wait_state(instance.application, agent, receipt["run_id"], {"succeeded", "failed"})
            assert outcome["state"] == "succeeded", outcome
            assert value.key_mutations == ["Enter"]
            assert (await instance.desktop_actions.submit(agent, request))["duplicate"]
            assert len(bundles) == 1 and len(bundles[0].input.input_requests) == 2
            frames = spawn.owners[-1].process.stdin.frames
            assert len(frames) == 2 and frames[1]["phase"] == "permit"
            assert all(frames[1][name] == frames[0]["action"][name] for name in ("grant_id", "run_id", "operation_id"))
            assert instance.desktop_actions.health()["leases"] == 0
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())
