"""Manual focused Unicode append, per-action permit and real owner ledger with all native/device/model peers injected."""

import asyncio
import json
import sys
import uuid
from types import SimpleNamespace

import pytest
from test_v2_desktop import begin, listed, verification
from test_v2_desktop_actions import approve, queued
from test_v2_execution import wait_state
from test_v2_linux_semantic import observed
from v2_linux_control_helpers import EDIT, Guard, Model, Spawn, factory
from v2_linux_desktop_helpers import WINDOW

from omni.adapters.platform.desktop import backend as host
from omni.adapters.platform.desktop.linux.linux_atspi import AtspiDesktop
from omni.adapters.platform.desktop.linux.linux_session import SessionInspector
from omni.adapters.platform.desktop.linux.linux_text import WAIT_SECONDS, FocusedText
from omni.adapters.workers.linux_action import run as action_worker
from omni.adapters.workers.linux_desktop import perform
from omni.composition.instance import open_instance
from omni.domain.desktop import VERSION, parse_request
from omni.domain.foreground import POLICY
from omni.domain.foreground import permit as foreground_permit


def model():
    value = Model()
    value.typing = True
    value.nodes[WINDOW]["flags"][0] |= 1 << 1
    value.nodes[EDIT]["flags"][0] |= 1 << 12
    return value


async def command(value, text="你好🙂"):
    target, observation = await observed(value)
    node = next(node for node in observation["nodes"] if "type_text" in node.get("actions", []))
    return {
        "version": VERSION,
        "request_id": str(uuid.uuid4()),
        "operation": "foreground",
        "target": target,
        "limit": 1,
        "action": {
            "kind": "type_text",
            "text": text,
            "node": node["reference"],
            "policy": POLICY,
            "takeover": True,
            **{key: str(uuid.uuid4()) for key in ("grant_id", "run_id", "operation_id")},
        },
    }


@pytest.mark.parametrize("advance_caret", [False, True])
def test_unicode_append_uses_character_position_utf8_byte_length_and_full_content_readback(advance_caret):
    async def scenario():
        value = model()
        value.nodes[EDIT]["text"] = "前🙂"
        value.advance_caret = advance_caret
        query = await command(value)
        f = factory(value)

        async def permit():
            assert not value.mutations and value.monitors[-1].armed
            return True

        response = await perform(
            parse_request(query),
            ops=f.ops,
            factory=f,
            monitor_factory=lambda inspector: Guard(inspector, model=value),
            permit=permit,
        )
        assert response["ok"] and response["result"] == {
            "status": "ok",
            "effect": "applied",
            "error_code": "",
            "verification": "text_appended",
        }
        assert value.nodes[EDIT]["text"] == "前🙂你好🙂"
        assert value.mutations == [("InsertText", [2, "你好🙂", len("你好🙂".encode("utf-8"))])]
        assert all(guard.closed for guard in value.monitors)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault",
    [
        "denied",
        "caret_changed",
        "selection",
        "content",
        "focus",
        "input_before",
        "input_after",
        "lost",
        "false",
        "unchanged",
        "password",
        "oversize",
        "wayland",
    ],
)
def test_fresh_proof_and_approval_are_required_and_uncertain_insert_is_never_replayed(fault):
    async def scenario():
        value = model()
        if fault == "oversize":
            value.nodes[EDIT]["text"] = "x" * 510
        query = await command(value)
        if fault in {"lost", "false", "unchanged"}:
            value.reply = fault
        if fault == "password":
            value.nodes[EDIT]["role"] = 40
        f = factory(value, wayland=fault == "wayland")

        async def permit():
            if fault == "caret_changed":
                value.caret = 1
            elif fault == "selection":
                value.selections = 1
            elif fault == "content":
                value.nodes[EDIT]["text"] = "user content"
            elif fault == "focus":
                value.nodes[EDIT]["flags"][0] &= ~(1 << 12)
            elif fault == "input_before":
                value.monitors[-1].fault = "changed"
            elif fault == "input_after":
                value.hook = lambda _: setattr(value.monitors[-1], "fault", "changed")
            return fault != "denied"

        response = await perform(
            query, ops=f.ops, factory=f, monitor_factory=lambda inspector: Guard(inspector, model=value), permit=permit
        )
        sent = fault in {"input_after", "lost", "false", "unchanged"}
        if fault == "wayland":
            assert not response["ok"] and response["error_code"] == "desktop_input_unverified"
        else:
            assert response["result"]["status"] == ("unknown" if sent else "denied"), response
        assert len(value.mutations) == int(sent)
        assert all(guard.closed for guard in value.monitors)
        if fault == "password":
            assert not any(call[2] == EDIT and call[4] == "GetText" for call in f.calls)

    asyncio.run(scenario())


def test_manual_focus_wait_keeps_the_actual_ten_second_budget_without_dispatch_or_device_input():
    async def scenario():
        value = model()
        value.nodes[EDIT]["flags"][0] &= ~(1 << 12)
        query = await command(value)
        f = factory(value)
        desktop = AtspiDesktop(SessionInspector(f.ops, f))
        await desktop.setup()
        guard = Guard(desktop.inspector, model=value)
        await guard.open()
        now, intervals = [100.0], []

        async def sleep(seconds):
            assert 0 <= seconds <= 0.1
            intervals.append(seconds)
            now[0] += seconds

        async def permit():
            pytest.fail("No native ready/permit may precede manually focused target proof")

        try:
            result = await FocusedText(desktop, now=lambda: now[0], sleep=sleep).append(
                query["target"], query["action"], guard, permit
            )
            assert result["status"] == "denied" and now[0] >= 100 + WAIT_SECONDS
            assert intervals and not value.mutations and not guard.armed
        finally:
            assert await guard.close() and await desktop.close()

    asyncio.run(scenario())


def test_linux_foreground_host_uses_uuid_bound_permit_and_denies_unbound_keys_or_x11_capture_target(monkeypatch):
    async def scenario():
        monkeypatch.setattr(host, "sys", SimpleNamespace(platform="linux", executable=sys.executable))
        value = model()
        query = await command(value)
        spawn = Spawn(value)
        backend = host.DesktopBackend(process_factory=spawn)

        async def permit():
            pass

        try:
            result = await backend.foreground(query["target"], query["action"], before_dispatch=permit)
            assert result["status"] == "ok"
            assert spawn.calls[0][0][-1] == "omni.adapters.workers.linux_action"
            frames = spawn.owners[-1].process.stdin.frames
            assert len(frames) == 2 and frames[-1]["phase"] == "permit"
            assert all(frames[-1][key] == query["action"][key] for key in ("grant_id", "run_id", "operation_id"))
            denied = await backend.foreground(
                query["target"], {**query["action"], "kind": "press_key", "text": "Enter"}, before_dispatch=permit
            )
            assert denied["status"] == "denied" and len(spawn.calls) == 2 and len(value.mutations) == 1
            denied = await backend.foreground(
                {**query["target"], "backend": "x11", "native_id": "x11:00400001:" + "a" * 64},
                query["action"],
                before_dispatch=permit,
            )
            assert denied["status"] == "denied" and len(spawn.calls) == 2
        finally:
            assert await backend.close(5)

    asyncio.run(scenario())


def test_real_owner_approval_single_use_and_cleanup_ledger_are_used_for_focused_append(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.setattr(host, "sys", SimpleNamespace(platform="linux", executable=sys.executable))
        value = model()
        spawn = Spawn(value)
        backend = host.DesktopBackend(process_factory=spawn)
        instance = await open_instance(tmp_path / "home", capabilities=(), desktop_backend=backend)
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            turn = await instance._database.write(
                lambda unit: unit.accept_turn(agent, None, "focused append fixture", "fixture", {})
            )
            view = await begin(instance.desktop, agent)
            snapshot = await listed(instance.desktop, agent, view)
            observation = await instance.desktop.observe(agent, verification(view, snapshot))
            node = next(node for node in observation["nodes"] if "type_text" in node.get("actions", []))
            request = {
                **verification(view, snapshot),
                "session_id": turn["session_id"],
                "request_id": str(uuid.uuid4()),
                "observation_id": observation["observation_id"],
                "node_id": node["node_id"],
                "kind": "type_text",
                "text": "你好🙂",
                "takeover": True,
            }
            receipt, approval = await queued(instance, agent, request)
            assert not value.mutations
            await approve(instance, agent, approval)
            run = await wait_state(instance.application, agent, receipt["run_id"], {"succeeded", "failed"})
            assert run["state"] == "succeeded", run
            assert value.nodes[EDIT]["text"] == "before你好🙂" and len(value.mutations) == 1
            assert (await instance.desktop_actions.submit(agent, request))["duplicate"]
            assert len(value.mutations) == 1 and instance.desktop_actions.health()["leases"] == 0
        finally:
            assert (await instance.close())["complete"] and all(owner.reclaimed for owner in spawn.owners)

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", [None, "missing", "cleanup"])
def test_capability_probe_reports_only_verified_x11_text_append_and_never_arms_or_writes(fault):
    async def scenario():
        value = model()
        f = factory(value)
        request = {
            "version": VERSION,
            "request_id": str(uuid.uuid4()),
            "operation": "probe",
            "target": None,
            "limit": 1,
        }
        result = await perform(
            request, ops=f.ops, factory=f, monitor_factory=lambda inspector: Guard(inspector, model=value, fault=fault)
        )
        if fault == "cleanup":
            assert not result["ok"] and result["error_code"] == "desktop_cleanup_pending"
        else:
            assert result["ok"], result
            capability = next(item for item in result["result"]["capabilities"] if item["id"] == "foreground_control")
            assert capability["installed"] and capability["available"] is (fault is None)
            assert "128" in str(capability["limitations"]) and "不使用剪贴板" in str(capability["limitations"])
        assert not value.mutations and all(guard.closed and not guard.armed for guard in value.monitors)

    asyncio.run(scenario())


@pytest.mark.parametrize("identity", ["request_id", "grant_id", "run_id", "operation_id"])
def test_native_worker_does_not_accept_a_permit_from_another_approved_operation(identity):
    async def scenario():
        value = model()
        query = await command(value)
        f = factory(value)
        reader = asyncio.StreamReader()
        reader.feed_data(json.dumps(query).encode("utf-8") + b"\n")
        frames = []

        async def send(data):
            frame = json.loads(data)
            frames.append(frame)
            if frame.get("phase") == "ready":
                assert not value.mutations
                reader.feed_data(
                    json.dumps({**foreground_permit(query), identity: str(uuid.uuid4())}).encode("utf-8") + b"\n"
                )
                reader.feed_eof()

        assert (
            await action_worker(
                reader, send, ops=f.ops, factory=f, monitor_factory=lambda inspector: Guard(inspector, model=value)
            )
            == 0
        )
        assert len(frames) == 2 and frames[-1]["result"]["status"] == "denied" and not value.mutations
        assert all(guard.closed for guard in value.monitors)

    asyncio.run(scenario())
