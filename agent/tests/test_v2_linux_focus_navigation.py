"""Same-window navigation postconditions using actual worker/binary peers and bounded AT-SPI reads; no native input."""

import asyncio
import sys
import uuid
from types import SimpleNamespace

import pytest
import v2_linux_control_helpers as shared
from test_v2_desktop import begin, listed, verification
from test_v2_desktop_actions import approve, queued
from test_v2_execution import wait_state
from v2_linux_control_helpers import BUTTON, EDIT, ITEM, LIST
from v2_linux_desktop_helpers import APP, WINDOW, node
from v2_linux_key_helpers import Bundle, command, model, profile_factory

from omni.adapters.platform.desktop import backend as host
from omni.adapters.platform.desktop.linux.linux_atspi import MAX_TEXT, PASSWORD_ROLE, AtspiDesktop
from omni.adapters.platform.desktop.linux.linux_focus_navigation import FocusNavigation
from omni.adapters.platform.desktop.linux.linux_semantic import Semantics
from omni.adapters.platform.desktop.linux.linux_session import SessionInspector
from omni.adapters.workers.linux_action import run
from omni.adapters.workers.linux_desktop import perform
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError
from omni.domain.desktop import parse_request
from omni.domain.desktop_actions import action_result


def navigable():
    value = model()
    value.nodes[BUTTON]["flags"][0] |= 1 << 11
    return value


def move(value, destination=BUTTON):
    value.nodes[EDIT]["flags"][0] &= ~(1 << 12)
    if destination is not None:
        value.nodes[destination]["flags"][0] |= 1 << 12


class Guard:
    def __init__(self):
        self.calls = 0
        self.fault = None

    async def check(self):
        self.calls += 1
        if self.fault:
            raise self.fault


async def setup(value=None):
    value = value or navigable()
    factory = profile_factory(value)
    desktop = AtspiDesktop(SessionInspector(factory.ops, factory))
    await desktop.setup()
    target = await desktop.target((APP, WINDOW))
    guard = Guard()
    navigation = FocusNavigation(desktop, guard)
    return value, factory, desktop, target, guard, navigation


@pytest.mark.parametrize("key", ["Tab", "Escape"])
@pytest.mark.parametrize("source", ["text", "button"])
def test_exact_native_pair_and_real_unique_same_window_focus_readback(key, source):
    async def scenario():
        value = navigable()
        query = await command(value, key)
        original, destination = EDIT, BUTTON
        if source == "button":
            move(value)
            original, destination = BUTTON, EDIT
            factory = profile_factory(value)
            desktop = AtspiDesktop(SessionInspector(factory.ops, factory))
            await desktop.setup()
            try:
                query["action"]["node"] = (await Semantics(desktop).resolve(query["target"], [1]))[-1]
            finally:
                assert await desktop.close()
        bundle = Bundle(value, key)

        def apply():
            value.key_mutations.append(key)
            value.nodes[original]["flags"][0] &= ~(1 << 12)
            value.nodes[destination]["flags"][0] |= 1 << 12

        bundle.input.after_copy = apply

        async def permit():
            assert not value.key_mutations and bundle.input.xkb.full_reads == [256, 5, 256, 5]
            return True

        response = await perform(parse_request(query), permit=permit, **bundle.arguments)
        assert response["ok"] and response["result"]["status"] == "ok", response
        assert response["result"]["verification"] == "key_verified"
        action_result(response["result"], "press_key")
        assert value.key_mutations == [key] and len(bundle.input.input_requests) == 2
        assert all(item.closed for item in bundle.connections + bundle.monitors)
        assert not value.mutations

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["focus", "text", "name", "role", "child", "flags", "actions", "denied"])
def test_approval_wait_rechecks_complete_tree_and_original_semantics_before_any_pair(fault):
    async def scenario():
        value = navigable()
        query = await command(value, "Tab")
        bundle = Bundle(value, "Tab")

        async def permit():
            if fault == "focus":
                move(value)
            elif fault == "text":
                value.nodes[BUTTON]["text"] = "changed"
            elif fault in {"name", "role"}:
                value.nodes[BUTTON][fault] = "changed" if fault == "name" else 44
            elif fault == "child":
                value.nodes[WINDOW]["children"].reverse()
            elif fault == "flags":
                value.nodes[BUTTON]["flags"][0] |= 1 << 23
            elif fault == "actions":
                value.nodes[EDIT]["interfaces"].append("org.a11y.atspi.Action")
            return fault != "denied"

        response = await perform(parse_request(query), permit=permit, **bundle.arguments)
        assert response["ok"] and response["result"]["status"] == "denied", response
        assert not value.key_mutations and not bundle.input.input_requests
        assert all(item.closed for item in bundle.connections + bundle.monitors)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault",
    [
        "two",
        "none",
        "unfocusable",
        "hidden",
        "disabled",
        "text",
        "role",
        "name",
        "parent",
        "order",
        "removed",
        "other_state",
        "native",
        "physical",
    ],
)
def test_unproven_or_unrelated_postconditions_remain_unknown_and_never_replay(fault):
    async def scenario():
        value = navigable()
        query = await command(value, "Tab")
        bundle = Bundle(value, "Tab")

        def apply():
            value.key_mutations.append("Tab")
            move(value, None if fault == "none" else BUTTON)
            if fault == "two":
                value.nodes[EDIT]["flags"][0] |= 1 << 12
            elif fault in {"unfocusable", "hidden", "disabled"}:
                bit = {"unfocusable": 11, "hidden": 25, "disabled": 8}[fault]
                value.nodes[BUTTON]["flags"][0] &= ~(1 << bit)
            elif fault in {"text", "name", "role"}:
                value.nodes[BUTTON][fault] = 44 if fault == "role" else "unrelated"
            elif fault == "parent":
                value.parents[BUTTON] = LIST
            elif fault == "order":
                value.nodes[WINDOW]["children"].reverse()
            elif fault == "removed":
                value.nodes[WINDOW]["children"].pop(1)
            elif fault == "other_state":
                value.nodes[ITEM]["flags"][0] |= 1 << 23
            elif fault == "native":
                bundle.input.focus = 999
            elif fault == "physical":
                bundle.input.raw_fault = "other_client"
                bundle.input.event = bytes([35, 130]) + bytes(6) + bytes([13]) + bytes(23)

        bundle.input.after_copy = apply
        response = await perform(parse_request(query), permit=lambda: asyncio.sleep(0, result=True), **bundle.arguments)
        assert response["ok"] and response["result"]["status"] == "unknown", response
        assert value.key_mutations == ["Tab"] and len(bundle.input.input_requests) == 2
        assert all(item.closed for item in bundle.connections + bundle.monitors)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault", ["cycle", "foreign", "parent", "password", "duplicate", "depth", "nodes", "bytes", "two_focus"]
)
def test_incomplete_or_ambiguous_tree_cannot_create_navigation_proof(fault):
    async def scenario():
        value, factory, desktop, target, _guard, navigation = await setup()
        try:
            if fault == "cycle":
                value.nodes[LIST]["children"] = [[APP, WINDOW]]
            elif fault == "foreign":
                value.nodes[WINDOW]["children"][1][0] = ":1.999"
            elif fault == "parent":
                value.parents[BUTTON] = LIST
            elif fault == "password":
                value.nodes[BUTTON]["role"] = PASSWORD_ROLE
            elif fault == "duplicate":
                value.nodes[WINDOW]["children"].append([APP, BUTTON])
            elif fault == "depth":
                parent = ITEM
                for index in range(8):
                    child = "/nested" + str(index)
                    value.nodes[parent]["children"] = [[APP, child]]
                    value.nodes[child], value.parents[child] = node(31), parent
                    parent = child
            elif fault == "nodes":
                value.nodes[LIST]["children"] = [[APP, ITEM]] * 129
            elif fault == "bytes":
                value.nodes[BUTTON]["interfaces"] = ["org.a11y." + str(index) + "x" * 240 for index in range(64)]
                value.nodes[ITEM]["text"] = "🙂" * 512
            elif fault == "two_focus":
                value.nodes[BUTTON]["flags"][0] |= 1 << 12
            with pytest.raises(BusinessError):
                await navigation.prepare(target, [0], (APP, EDIT))
            if fault == "password":
                calls = factory.calls
                assert not any(
                    call[2] == BUTTON and call[4] in {"GetText", "GetChildAtIndex", "GetInterfaces"} for call in calls
                )
                assert not any(call[2] == BUTTON and call[6] == ["org.a11y.atspi.Accessible", "Name"] for call in calls)
            with pytest.raises(BusinessError):
                await navigation.check()
        finally:
            assert await desktop.close()

    asyncio.run(scenario())


def test_zero_focus_is_pending_then_unique_focus_can_be_read_without_adopting_a_new_baseline():
    async def scenario():
        value, _factory, desktop, target, _guard, navigation = await setup()
        try:
            await navigation.prepare(target, [0], (APP, EDIT))
            baseline = navigation._before
            move(value, None)
            assert not await navigation.changed()
            value.nodes[BUTTON]["flags"][0] |= 1 << 12
            assert await navigation.changed()
            assert navigation._before is baseline
            with pytest.raises(BusinessError):
                await navigation.check()
            # A rejected read cannot be revived by restoring the initial state.
            value.nodes[BUTTON]["flags"][0] &= ~(1 << 12)
            value.nodes[EDIT]["flags"][0] |= 1 << 12
            with pytest.raises(BusinessError):
                await navigation.check()
        finally:
            assert await desktop.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["between_reads", "last_guard", "cancel"])
def test_double_read_or_guard_failure_latches_original_proof_invalid(fault):
    async def scenario():
        value, _factory, desktop, target, guard, navigation = await setup()
        try:
            await navigation.prepare(target, [0], (APP, EDIT))
            original = navigation._read

            async def read():
                outcome = await original()
                if fault == "between_reads":
                    move(value)
                elif fault == "last_guard":
                    guard.fault = BusinessError("desktop_user_intervened", "changed")
                else:
                    raise asyncio.CancelledError
                return outcome

            navigation._read = read
            with pytest.raises(asyncio.CancelledError if fault == "cancel" else BusinessError):
                await navigation.check()
            navigation._read = original
            guard.fault = None
            with pytest.raises(BusinessError):
                await navigation.changed()
        finally:
            assert await desktop.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("boundary", ["128_nodes", "depth_8", "exact_bytes"])
def test_exact_tree_budgets_remain_usable_without_truncation(boundary):
    async def scenario():
        value, _factory, desktop, target, _guard, navigation = await setup()
        try:
            if boundary == "128_nodes":
                for index in range(123):
                    child = "/leaf" + str(index)
                    value.nodes[WINDOW]["children"].append([APP, child])
                    value.nodes[child], value.parents[child] = node(31), WINDOW
            elif boundary == "depth_8":
                parent = ITEM
                for index in range(5):
                    child = "/nested" + str(index)
                    value.nodes[parent]["children"] = [[APP, child]]
                    value.nodes[child], value.parents[child] = node(31), parent
                    parent = child
            else:
                other = 0
                for path in value.nodes:
                    facts = await Semantics(desktop).facts((APP, path))
                    strings = [facts["name"], facts["text"]]
                    if path != BUTTON:
                        strings.extend(facts["interfaces"])
                    other += sum(len(item.encode("utf-8")) for item in strings)
                capacity = MAX_TEXT - other
                length = (capacity + 63) // 64
                interfaces = []
                for index in range(64):
                    size = length if index < 63 else capacity - 63 * length
                    prefix = "org.test." + str(index)
                    assert len(prefix) <= size <= 255
                    interfaces.append(prefix + "x" * (size - len(prefix)))
                value.nodes[BUTTON]["interfaces"] = interfaces
            await navigation.prepare(target, [0], (APP, EDIT))
            await navigation.check()
            move(value)
            assert await navigation.changed()
        finally:
            assert await desktop.close()

    asyncio.run(scenario())


def test_navigation_cancellation_retains_real_key_release_and_both_connections_until_settled():
    async def scenario():
        value = navigable()
        query = await command(value, "Tab")
        bundle = Bundle(value, "Tab")

        def apply():
            value.key_mutations.append("Tab")
            move(value)
            bundle.input.block_query = True

        bundle.input.after_copy = apply
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
            assert value.key_mutations == ["Tab"] and len(bundle.input.input_requests) == 2
            assert all(item.closed for item in bundle.connections + bundle.monitors)
        finally:
            bundle.input.query_release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("key", ["Tab", "Escape"])
def test_owner_approval_exact_permit_sqlite_receipt_and_single_use_keep_navigation_path(key, tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.setattr(host, "sys", SimpleNamespace(platform="linux", executable=sys.executable))
        value, bundles = navigable(), []

        async def native_run(reader, send, **injected):
            bundle = Bundle(value, key, selected_factory=injected["factory"])
            bundles.append(bundle)

            def apply():
                value.key_mutations.append(key)
                move(value)

            bundle.input.after_copy = apply
            return await run(reader, send, **bundle.arguments)

        monkeypatch.setattr(shared, "factory", profile_factory)
        monkeypatch.setattr(shared, "run", native_run)
        spawn = shared.Spawn(value)
        backend = host.DesktopBackend(process_factory=spawn)
        instance = await open_instance(tmp_path / "home", capabilities=(), desktop_backend=backend)
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            turn = await instance._database.write(lambda unit: unit.accept_turn(agent, None, "navigate", "fixture", {}))
            view = await begin(instance.desktop, agent)
            snapshot = await listed(instance.desktop, agent, view)
            observation = await instance.desktop.observe(agent, verification(view, snapshot))
            observed = next(node for node in observation["nodes"] if node["name"] == "fixture-edit")
            request = {
                **verification(view, snapshot),
                "session_id": turn["session_id"],
                "request_id": str(uuid.uuid4()),
                "observation_id": observation["observation_id"],
                "node_id": observed["node_id"],
                "kind": "press_key",
                "text": key,
                "takeover": True,
            }
            receipt, approval = await queued(instance, agent, request)
            assert not value.key_mutations
            await approve(instance, agent, approval)
            outcome = await wait_state(instance.application, agent, receipt["run_id"], {"succeeded", "failed"})
            assert outcome["state"] == "succeeded", outcome
            assert value.key_mutations == [key] and value.nodes[BUTTON]["flags"][0] & (1 << 12)
            assert (await instance.desktop_actions.submit(agent, request))["duplicate"]
            assert len(bundles) == 1 and len(bundles[0].input.input_requests) == 2
            frames = spawn.owners[-1].process.stdin.frames
            assert len(frames) == 2 and frames[1]["phase"] == "permit"
            assert all(frames[1][name] == frames[0]["action"][name] for name in ("grant_id", "run_id", "operation_id"))
            assert instance.desktop_actions.health()["leases"] == 0
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())
