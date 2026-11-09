"""Real V2 owner runs/approval/database with actual host/worker protocol and injected Linux peers."""

import asyncio
import sys
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from test_v2_desktop import begin, listed, verification
from test_v2_desktop_actions import approve, queued
from test_v2_execution import wait_state
from v2_linux_control_helpers import EDIT, Model, Spawn

from omni.adapters.persistence import schema as s
from omni.adapters.platform import desktop as host
from omni.composition.instance import open_instance


@asynccontextmanager
async def prepared(tmp_path, monkeypatch, *, kind="set_text"):
    monkeypatch.setattr(host, "sys", SimpleNamespace(platform="linux", executable=sys.executable))
    model = Model()
    spawn = Spawn(model)
    backend = host.DesktopBackend(process_factory=spawn)
    instance = await open_instance(tmp_path / "home", capabilities=(), desktop_backend=backend)
    try:
        agent = (await instance.application.query("agents", ""))[0]["id"]

        def setup(u):
            session = u._record(s.sessions, {"title": "Linux owner fixture", "revision": 1, "next_seq": 0}, agent)
            configuration = u.configuration_get(agent, u.settings(agent)["value"]["model_profile_id"])
            u.configuration_put(
                agent,
                configuration["id"],
                configuration["name"],
                False,
                configuration["value"],
                configuration["revision"],
            )
            return session["id"]

        session = await instance._database.write(setup)
        view = await begin(instance.desktop, agent)
        snapshot = await listed(instance.desktop, agent, view)
        report = await instance.desktop.observe(agent, verification(view, snapshot))
        node = next(n for n in report["nodes"] if kind in n.get("actions", []))
        request = {
            **verification(view, snapshot),
            "session_id": session,
            "request_id": str(uuid.uuid4()),
            "observation_id": report["observation_id"],
            "node_id": node["node_id"],
            "kind": kind,
            "text": "approved Linux text" if kind == "set_text" else "",
        }
        assert all("reference" not in n for n in report["nodes"])
        yield instance, model, spawn, agent, request
    finally:
        assert (await instance.close())["complete"]
        assert all(owner.reclaimed for owner in spawn.owners)


@pytest.mark.parametrize("kind", ["set_text", "select", "invoke"])
def test_linux_command_reuses_existing_owner_approval_and_uncertainty_ledger(tmp_path, monkeypatch, kind):
    async def scenario():
        async with prepared(tmp_path, monkeypatch, kind=kind) as (instance, model, _spawn, agent, request):
            receipt, approval = await queued(instance, agent, request)
            assert not model.mutations
            await approve(instance, agent, approval)
            run = await wait_state(instance.application, agent, receipt["run_id"], {"succeeded", "failed"})
            if kind == "invoke":
                assert run["state"] == "failed" and run["error_code"] == "action_outcome_unknown", run
            else:
                assert run["state"] == "succeeded", run
                if kind == "set_text":
                    assert model.nodes[EDIT]["text"] == request["text"]
                else:
                    assert model.selected
            assert len(model.mutations) == 1
            assert (await instance.desktop_actions.submit(agent, request))["duplicate"]
            assert len(model.mutations) == 1
            assert await instance._database.read(lambda u: u.connection.execute(select(s.model_calls)).first()) is None
            assert instance.desktop_actions.health()["leases"] == 0
        for path in (tmp_path / "home").rglob("*.jsonl"):
            assert "approved Linux text" not in path.read_text(encoding="utf-8")

    asyncio.run(scenario())


def test_detached_view_while_awaiting_approval_cannot_dispatch_a_linux_mutation(tmp_path, monkeypatch):
    async def scenario():
        async with prepared(tmp_path, monkeypatch) as (instance, model, spawn, agent, request):
            receipt, approval = await queued(instance, agent, request)
            await instance.desktop.forget(agent, request["view_id"])
            await approve(instance, agent, approval)
            run = await wait_state(instance.application, agent, receipt["run_id"], {"succeeded", "failed"})
            assert run["state"] == "failed" and not model.mutations
            assert not any(call[0][-1] == "omni.adapters.workers.linux_action" for call in spawn.calls)

    asyncio.run(scenario())
