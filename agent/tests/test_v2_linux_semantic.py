"""Semantic proof, permission and effect certainty on fake AT-SPI/input peers; no real native action."""

import asyncio
import json
import sys
import uuid
from types import SimpleNamespace

import pytest
from v2_linux_control_helpers import EDIT, Guard, Model, Spawn, factory

from omni.adapters.platform.desktop import backend as host
from omni.adapters.platform.desktop.linux.linux_atspi import AtspiDesktop
from omni.adapters.platform.desktop.linux.linux_session import SessionInspector
from omni.adapters.workers.linux_action import run
from omni.adapters.workers.linux_desktop import perform
from omni.domain.contracts import BusinessError
from omni.domain.desktop import VERSION, parse_request
from omni.domain.desktop_actions import POLICY, action_result, linux_ready


async def observed(model, *, wayland=False):
    f = factory(model, wayland=wayland)
    adapter = AtspiDesktop(SessionInspector(f.ops, f))
    await adapter.setup()
    try:
        target = (await adapter.targets(10))["items"][0]
        observation = await adapter.observe(target, actions=True)
        return target, observation
    finally:
        await adapter.close()


def command(target, proof, *, kind="set_text"):
    return {
        "version": VERSION,
        "request_id": str(uuid.uuid4()),
        "operation": "act",
        "target": target,
        "limit": 1,
        "action": {"kind": kind, "text": "after你好" if kind == "set_text" else "", "node": proof, "policy": POLICY},
    }


@pytest.mark.parametrize(
    "kind,path,expected", [("set_text", [0], "ok"), ("select", [2, 0], "ok"), ("invoke", [1], "unknown")]
)
def test_proof_and_single_native_mutation_are_checked_before_and_after(kind, path, expected):
    async def scenario():
        model = Model()
        target, tree = await observed(model)
        proof = next(n["reference"] for n in tree["nodes"] if (n.get("reference") or {}).get("path") == path)
        query = command(target, proof, kind=kind)
        f = factory(model)

        async def permit():
            assert not model.mutations and model.monitors[-1].armed
            return True

        response = await perform(
            parse_request(query), ops=f.ops, factory=f, monitor_factory=lambda i: Guard(i, model=model), permit=permit
        )
        assert response["ok"], response
        result = response["result"]
        assert result["status"] == expected and len(model.mutations) == 1
        assert action_result(result, kind).status.value == expected
        assert all(g.closed for g in model.monitors)
        assert "private-native-canary" not in json.dumps(response)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault",
    [
        "denied",
        "text_changed",
        "actions_changed",
        "input_before",
        "input_after",
        "lost",
        "unchanged",
        "password",
        "wayland",
    ],
)
def test_revocation_drift_and_uncertain_rpc_never_false_succeed_or_replay(fault):
    async def scenario():
        model = Model()
        target, tree = await observed(model, wayland=fault == "wayland")
        proof = next(n["reference"] for n in tree["nodes"] if (n.get("reference") or {}).get("path") == [0])
        query = command(target, proof)
        if fault in {"lost", "unchanged"}:
            model.reply = fault
        if fault == "password":
            model.nodes[EDIT]["role"] = 40
        f = factory(model, wayland=fault == "wayland")

        async def permit():
            if fault == "text_changed":
                model.nodes[EDIT]["text"] = "user edited after observation"
            if fault == "actions_changed":
                model.nodes[EDIT]["interfaces"].clear()
            if fault == "input_before":
                model.monitors[-1].fault = "changed"
            if fault == "input_after":
                model.hook = lambda _: setattr(model.monitors[-1], "fault", "changed")
            return fault != "denied"

        response = await perform(
            query, ops=f.ops, factory=f, monitor_factory=lambda i: Guard(i, model=model), permit=permit
        )
        sent = fault in {"lost", "unchanged", "input_after"}
        if fault == "wayland":
            assert response["error_code"] == "desktop_input_unverified" and not response["ok"]
        else:
            assert response["result"]["status"] == ("unknown" if sent else "denied"), response
        assert len(model.mutations) == int(sent)
        if fault == "password":
            assert not any(c[2] == EDIT and c[4] == "GetText" for c in f.calls)

    asyncio.run(scenario())


def test_host_rechecks_approval_after_ready_before_sending_real_permit(monkeypatch):
    async def scenario():
        monkeypatch.setattr(host, "sys", SimpleNamespace(platform="linux", executable=sys.executable))
        model, spawn = Model(), None
        target, tree = await observed(model)
        proof = next(n["reference"] for n in tree["nodes"] if (n.get("reference") or {}).get("path") == [0])
        spawn = Spawn(model)
        backend = host.DesktopBackend(process_factory=spawn)

        async def guard():
            if any(m.armed for m in model.monitors):
                raise BusinessError("desktop_action_revoked", "private-policy-canary", status=409)

        try:
            result = await backend.act(target, command(target, proof)["action"], before_dispatch=guard)
            assert result["status"] == "denied" and result["effect"] == "none"
            assert result["error_code"] == "desktop_action_revoked" and not model.mutations
            assert spawn.calls[0][0][-1] == "omni.adapters.workers.linux_action"
            assert len(spawn.owners[0].process.stdin.frames) == 1
        finally:
            assert await backend.close(6)
            assert all(o.reclaimed for o in spawn.owners)

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", [None, "held", "cleanup"])
def test_owned_helper_transport_uses_two_phase_dispatch_and_native_cleanup(monkeypatch, fault):
    async def scenario():
        monkeypatch.setattr(host, "sys", SimpleNamespace(platform="linux", executable=sys.executable))
        model = Model()
        target, tree = await observed(model)
        proof = next(n["reference"] for n in tree["nodes"] if (n.get("reference") or {}).get("path") == [0])
        spawn = Spawn(model, fault=fault)
        backend = host.DesktopBackend(process_factory=spawn)

        async def guard():
            return None

        try:
            result = await backend.act(target, command(target, proof)["action"], before_dispatch=guard)
            assert result["status"] == ("ok" if fault is None else "denied" if fault == "held" else "unknown"), result
            assert len(model.mutations) == (0 if fault == "held" else 1)
            frames = spawn.owners[0].process.stdin.frames
            if fault != "held":
                assert frames[1] == linux_ready(parse_request(frames[0]), permitted=True)
        finally:
            assert await backend.close(6)

    asyncio.run(scenario())


def test_wrong_permit_uuid_never_authorizes_native_write():
    async def scenario():
        model = Model()
        target, tree = await observed(model)
        proof = next(n["reference"] for n in tree["nodes"] if (n.get("reference") or {}).get("path") == [0])
        query = command(target, proof)
        reader, emitted = asyncio.StreamReader(), []
        reader.feed_data(json.dumps(query).encode() + b"\n")
        f = factory(model)

        async def send(data):
            value = json.loads(data)
            emitted.append(value)
            if value.get("phase") == "ready":
                reader.feed_data(
                    json.dumps({**linux_ready(query, permitted=True), "request_id": str(uuid.uuid4())}).encode() + b"\n"
                )

        assert await run(reader, send, ops=f.ops, factory=f, monitor_factory=lambda i: Guard(i, model=model)) == 0
        assert emitted[-1]["result"]["status"] == "denied" and not model.mutations

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["missing", "wayland", "long_text", "multi_select"])
def test_observation_omits_unproven_action_candidates_without_losing_readonly_tree(fault):
    async def scenario():
        model = Model()
        if fault == "long_text":
            model.nodes[EDIT]["text"] = "x" * 513
        if fault == "multi_select":
            model.nodes["/list"]["flags"][0] |= 1 << 18
        f = factory(model, wayland=fault == "wayland")
        adapter = AtspiDesktop(SessionInspector(f.ops, f))
        await adapter.setup()
        try:
            target = (await adapter.targets(10))["items"][0]
        finally:
            assert await adapter.close()
        f = factory(model, wayland=fault == "wayland")
        response = await perform(
            {
                "version": VERSION,
                "request_id": str(uuid.uuid4()),
                "operation": "observe",
                "target": target,
                "limit": 128,
            },
            ops=f.ops,
            factory=f,
            monitor_factory=lambda inspector: Guard(
                inspector, model=model, fault="missing" if fault == "missing" else None
            ),
        )
        assert response["ok"] and response["result"]["nodes"], response
        nodes = response["result"]["nodes"]
        if fault in {"missing", "wayland"}:
            assert all(not n.get("actions") for n in nodes)
        else:
            name = "fixture-edit" if fault == "long_text" else "fixture-item"
            assert not next(n for n in nodes if n["name"] == name)["actions"]
        assert not model.mutations and all(g.closed for g in model.monitors)

    asyncio.run(scenario())
