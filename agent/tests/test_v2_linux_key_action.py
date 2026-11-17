"""Approved native key business integration with application readback; all accounts, sockets and input injected."""

import asyncio
import json
import sys
import uuid
from types import SimpleNamespace

import pytest
import v2_linux_control_helpers as shared
from test_v2_desktop import begin, listed, verification
from test_v2_desktop_actions import approve, queued
from test_v2_execution import wait_state
from v2_linux_key_helpers import EDIT, Bundle, command, model, profile_factory

from omni.adapters.platform import desktop as host
from omni.adapters.platform.linux_key_action import expected_edit
from omni.adapters.workers.linux_action import run
from omni.adapters.workers.linux_desktop import perform
from omni.composition.instance import open_instance
from omni.domain.desktop import parse_request
from omni.domain.desktop_actions import POLICY as SEMANTIC_POLICY
from omni.domain.desktop_actions import action_result
from omni.domain.foreground import permit as foreground_permit


@pytest.mark.parametrize(
    "key,caret,text,expected,position",
    [
        ("Left", 3, "你好🙂", "你好🙂", 2),
        ("Right", 1, "你好🙂", "你好🙂", 2),
        ("Home", 3, "before", "before", 0),
        ("End", 2, "before", "before", 6),
        ("Backspace", 3, "你好🙂", "你好", 2),
        ("Delete", 1, "你好🙂", "你🙂", 1),
        ("Enter", 1, "你好", "你\n好", 2),
    ],
)
def test_real_binding_mapping_copy_pair_permit_and_exact_application_result(key, caret, text, expected, position):
    async def scenario():
        value = model()
        value.nodes[EDIT]["text"], value.caret = text, caret
        query = await command(value, key)
        bundle = Bundle(value, key)

        async def permit():
            assert bundle.bindings[0].focus is not None and not value.key_mutations
            assert bundle.input.xkb.full_reads == [256, 5, 256, 5]
            return True

        response = await perform(parse_request(query), permit=permit, **bundle.arguments)
        assert response["ok"] and response["result"]["status"] == "ok", response
        assert response["result"]["verification"] == "key_verified"
        action_result(response["result"], "press_key")
        assert (value.nodes[EDIT]["text"], value.caret) == (expected, position)
        assert value.key_mutations == [key] and len(bundle.input.input_requests) == 2
        assert all(item.closed for item in bundle.connections + bundle.monitors)
        assert not value.mutations

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["denied", "caret", "descriptor", "physical", "mapping", "password", "unfocused"])
def test_revocation_and_changed_preconditions_send_no_key_pair(fault):
    async def scenario():
        value = model()
        query = await command(value)
        bundle = Bundle(value)
        if fault == "password":
            value.nodes[EDIT]["role"] = 40
        if fault == "unfocused":
            value.nodes[EDIT]["flags"][0] &= ~(1 << 12)
            # Immediate model focus drift is checked before any input proof;
            # retain the real manual-wait budget in its separate injected test.
            query["action"]["node"]["value_sha256"] = "0" * 64

        async def permit():
            if fault == "caret":
                value.caret = 1
            elif fault == "descriptor":
                bundle.factory.attributes["avalonia-window-handle"] = "4194306"
            elif fault == "physical":
                bundle.input.raw_fault = "other_client"
                bundle.input.event = bytes([35, 130]) + bytes(6) + bytes([13]) + bytes(23)
            elif fault == "mapping":
                bundle.input.xkb.copy_common = 10
            return fault != "denied"

        response = await perform(parse_request(query), permit=permit, **bundle.arguments)
        assert response["ok"] and response["result"]["status"] == "denied", response
        assert not value.key_mutations and not bundle.input.input_requests
        assert all(item.closed for item in bundle.connections + bundle.monitors)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "key,effect", [("Tab", "normal"), ("Escape", "normal"), ("Left", "foreign_value"), ("Left", "unchanged")]
)
def test_transport_or_unrelated_application_changes_remain_unknown_without_replay(key, effect):
    async def scenario():
        value = model()
        query = await command(value, key)
        bundle = Bundle(value, key)
        bundle.effect = effect
        response = await perform(parse_request(query), permit=lambda: asyncio.sleep(0, result=True), **bundle.arguments)
        assert response["ok"] and response["result"]["status"] == "unknown", response
        assert value.key_mutations == [key] and len(bundle.input.input_requests) == 2
        assert all(item.closed for item in bundle.connections + bundle.monitors)

    asyncio.run(scenario())


@pytest.mark.parametrize("identity", ["request_id", "grant_id", "run_id", "operation_id"])
def test_ready_waits_for_native_proof_and_only_the_exact_parent_permit_can_release_input(identity):
    async def scenario():
        value = model()
        query = await command(value)
        bundle = Bundle(value)
        reader = asyncio.StreamReader()
        reader.feed_data(json.dumps(query).encode() + b"\n")
        frames = []

        async def send(data):
            frame = json.loads(data)
            frames.append(frame)
            if frame.get("phase") == "ready":
                assert bundle.input.xkb.full_reads and not value.key_mutations
                permission = foreground_permit(query)
                permission[identity] = "00000000-0000-0000-0000-000000000000"
                reader.feed_data(json.dumps(permission).encode() + b"\n")
                reader.feed_eof()

        assert await run(reader, send, **bundle.arguments) == 0
        assert len(frames) == 2 and frames[-1]["result"]["status"] == "denied"
        assert not bundle.input.input_requests and not value.key_mutations

    asyncio.run(scenario())


def test_vertical_text_postconditions_are_bounded_and_unchanged_edges_or_nontext_have_no_success_proof():
    assert expected_edit("ab\ncdef\ng", {"caret": 5}, "Up") == ("ab\ncdef\ng", 2)
    assert expected_edit("ab\ncdef\ng", {"caret": 1}, "Down") == ("ab\ncdef\ng", 4)
    assert expected_edit("ab", {"caret": 0}, "Left") is None
    assert expected_edit("ab", {"caret": 1}, "Tab") is None
    assert expected_edit("ab", None, "Enter") is None


def test_cancelled_business_waiter_keeps_actual_key_release_and_both_connections_until_settled():
    async def scenario():
        value = model()
        query = await command(value)
        bundle = Bundle(value)
        apply = bundle.input.after_copy

        def after_press():
            apply()
            bundle.input.block_query = True

        bundle.input.after_copy = after_press
        task = asyncio.create_task(
            perform(parse_request(query), permit=lambda: asyncio.sleep(0, result=True), **bundle.arguments)
        )
        try:
            await bundle.input.query_started.wait()
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and all(not item.closed for item in bundle.connections + bundle.monitors)
            bundle.input.query_release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert value.key_mutations == ["Left"] and len(bundle.input.input_requests) == 2
            assert all(item.closed for item in bundle.connections + bundle.monitors)
        finally:
            bundle.input.query_release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_permitted_parent_reclamation_waits_for_actual_worker_exit_without_terminating_release_owner():
    async def scenario():
        release = asyncio.Event()
        calls = []

        class Process:
            stdin = SimpleNamespace(close=lambda: calls.append("close_stdin"))

            async def wait(self):
                await release.wait()
                return 0

        class Owned:
            process = Process()

            async def terminate(self, _budget):
                assert release.is_set()
                calls.append("terminate_exited")
                return True

        backend = host.DesktopBackend()
        await backend._slots.acquire()
        owner = host._Host(owned=Owned(), native_key_permitted=True)
        backend._hosts.add(owner)
        try:
            assert not await backend._reclaim_actual(owner, 0)
            retained = owner.exited
            assert owner in backend._hosts and calls == ["close_stdin"]
            release.set()
            await retained
            assert await backend._reclaim_actual(owner, 1)
            assert owner.exited is retained and calls.count("terminate_exited") == 1
            assert owner not in backend._hosts
        finally:
            release.set()
            assert await backend.close(1)

    asyncio.run(scenario())


def test_real_owner_approval_pipe_permit_sqlite_result_and_single_use_use_the_native_key_path(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.setattr(host, "sys", SimpleNamespace(platform="linux", executable=sys.executable))
        value = model()
        bundles = []

        async def native_run(reader, send, **injected):
            bundle = Bundle(value, selected_factory=injected["factory"])
            bundles.append(bundle)
            return await run(reader, send, **bundle.arguments)

        monkeypatch.setattr(shared, "factory", profile_factory)
        monkeypatch.setattr(shared, "run", native_run)
        spawn = shared.Spawn(value)
        backend = host.DesktopBackend(process_factory=spawn)
        instance = await open_instance(tmp_path / "home", capabilities=(), desktop_backend=backend)
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            turn = await instance._database.write(
                lambda unit: unit.accept_turn(agent, None, "native key fixture", "fixture", {})
            )
            view = await begin(instance.desktop, agent)
            snapshot = await listed(instance.desktop, agent, view)
            observation = await instance.desktop.observe(agent, verification(view, snapshot))
            node = next(node for node in observation["nodes"] if "press_key" in node.get("actions", []))
            request = {
                **verification(view, snapshot),
                "session_id": turn["session_id"],
                "request_id": str(uuid.uuid4()),
                "observation_id": observation["observation_id"],
                "node_id": node["node_id"],
                "kind": "press_key",
                "text": "Left",
                "takeover": True,
            }
            receipt, approval = await queued(instance, agent, request)
            assert not value.key_mutations
            await approve(instance, agent, approval)
            outcome = await wait_state(instance.application, agent, receipt["run_id"], {"succeeded", "failed"})
            assert outcome["state"] == "succeeded", outcome
            assert value.caret == 5 and value.key_mutations == ["Left"]
            assert (await instance.desktop_actions.submit(agent, request))["duplicate"]
            assert len(bundles) == 1 and len(bundles[0].input.input_requests) == 2
            frames = spawn.owners[-1].process.stdin.frames
            assert len(frames) == 2 and frames[1]["phase"] == "permit"
            assert all(frames[1][key] == frames[0]["action"][key] for key in ("grant_id", "run_id", "operation_id"))
            assert instance.desktop_actions.health()["leases"] == 0
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["set_text", "type_text"])
def test_original_semantic_and_append_actions_remain_verified_when_the_node_also_has_a_key_candidate(kind):
    async def scenario():
        value = model()
        query = await command(value)
        if kind == "set_text":
            query["operation"] = "act"
            query["action"] = {"kind": kind, "text": "你好", "node": query["action"]["node"], "policy": SEMANTIC_POLICY}
        else:
            query["action"].update(kind=kind, text="你好")
        bundle = Bundle(value)
        response = await perform(parse_request(query), permit=lambda: asyncio.sleep(0, result=True), **bundle.arguments)
        assert response["ok"] and response["result"]["status"] == "ok", response
        assert value.nodes[EDIT]["text"] == ("before" if kind == "type_text" else "") + "你好"
        assert not value.key_mutations and not bundle.input.input_requests
        assert not bundle.bindings

    asyncio.run(scenario())


def test_parent_close_after_real_permit_retains_the_live_release_worker_and_shared_slot(monkeypatch):
    async def scenario():
        monkeypatch.setattr(host, "sys", SimpleNamespace(platform="linux", executable=sys.executable))
        value = model()
        query = await command(value)
        prepared = asyncio.Event()
        bundles, terminations = [], []
        original_terminate = shared.Owned.terminate

        async def native_run(reader, send, **injected):
            bundle = Bundle(value, selected_factory=injected["factory"])
            bundles.append(bundle)
            apply = bundle.input.after_copy

            def after_press():
                apply()
                bundle.input.block_query = True

            bundle.input.after_copy = after_press
            prepared.set()
            return await run(reader, send, **bundle.arguments)

        async def terminated(owner, budget):
            assert owner.process.worker.done(), "A permitted native release owner must not be forcibly terminated"
            terminations.append(owner)
            return await original_terminate(owner, budget)

        monkeypatch.setattr(shared, "factory", profile_factory)
        monkeypatch.setattr(shared, "run", native_run)
        monkeypatch.setattr(shared.Owned, "terminate", terminated)
        spawn = shared.Spawn(value)
        backend = host.DesktopBackend(process_factory=spawn)
        operation = asyncio.create_task(
            backend.foreground(query["target"], query["action"], before_dispatch=lambda: asyncio.sleep(0))
        )
        try:
            await prepared.wait()
            bundle = bundles[0]
            await bundle.input.query_started.wait()
            assert not await backend.close(0)
            await asyncio.sleep(0)
            assert len(backend._hosts) == 1 and next(iter(backend._hosts)).native_key_permitted
            assert not terminations and not operation.done()
            assert all(not item.closed for item in bundle.connections + bundle.monitors)
            bundle.input.query_release.set()
            outcome = await operation
            assert outcome["status"] == "ok" and value.key_mutations == ["Left"]
            assert await backend.close(2) and len(terminations) == 1
        finally:
            if bundles:
                bundles[0].input.query_release.set()
            await asyncio.gather(operation, return_exceptions=True)
            assert await backend.close(2)

    asyncio.run(scenario())
