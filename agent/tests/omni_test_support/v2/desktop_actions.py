"""Reusable desktop_actions fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select, update

from omni.adapters.persistence import schema as s
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError
from omni.domain.desktop_actions import POLICY, action_result, owner_action
from omni_test_support.v2.desktop import begin, listed, verification
from omni_test_support.v2.execution import wait_state
from omni_test_support.v2.v2_desktop_helpers import FixtureDesktopBackend


class ActionsBackend(FixtureDesktopBackend):
    def __init__(self):
        super().__init__()
        self.action_calls = []
        self.action_entered, self.action_gate, self.after_gate = asyncio.Event(), asyncio.Event(), asyncio.Event()
        self.action_gate.set()
        self.after_gate.set()
        self.outcome = {"status": "ok", "effect": "applied", "error_code": "", "verification": "value_matches"}

    async def observe(self, target, *, before_dispatch=None):
        result = await super().observe(target, before_dispatch=before_dispatch)
        node = result["nodes"][0]
        node.update(
            actions=["set_text", "invoke", "select"],
            reference={
                "root": [1],
                "runtime": [1],
                "role": node["role"],
                "name_sha256": hashlib.sha256(node["name"].encode()).hexdigest(),
            },
        )
        return result

    async def act(self, target, action, *, before_dispatch):
        self.action_entered.set()
        await self.action_gate.wait()
        try:
            await before_dispatch()
        except BusinessError as error:
            return {"status": "denied", "effect": "none", "error_code": error.code, "verification": "not_dispatched"}
        self.action_calls.append(action)
        await self.after_gate.wait()
        return self.outcome

    def quiescent(self):
        return not self.close_blocked and self.after_gate.is_set()

    async def reclaim(self, timeout_s):
        return self.quiescent()


@asynccontextmanager
async def prepared(tmp_path):
    backend = ActionsBackend()
    instance = await open_instance(tmp_path / "home", capabilities=(), desktop_backend=backend)
    agent = (await instance.application.query("agents", ""))[0]["id"]
    try:

        def setup(u):
            session = u._record(s.sessions, {"title": "Owner command fixture", "revision": 1, "next_seq": 0}, agent)
            # Native owner commands must work even with no authorized model.
            model = u.configuration_get(agent, u.settings(agent)["value"]["model_profile_id"])
            u.configuration_put(agent, model["id"], model["name"], False, model["value"], model["revision"])
            return session["id"]

        session = await instance._database.write(setup)
        view = await begin(instance.desktop, agent)
        snapshot = await listed(instance.desktop, agent, view)
        observed = await instance.desktop.observe(agent, verification(view, snapshot))
        assert "reference" not in observed["nodes"][0]
        request = {
            **verification(view, snapshot),
            "observation_id": observed["observation_id"],
            "node_id": observed["nodes"][0]["node_id"],
            "session_id": session,
            "request_id": str(uuid.uuid4()),
            "kind": "set_text",
            "text": "private-approved-content",
        }
        yield instance, backend, agent, request
    finally:
        backend.action_gate.set()
        backend.after_gate.set()
        backend.close_blocked = False
        assert (await instance.close())["complete"]


async def queued(instance, agent, request):
    receipt = await instance.desktop_actions.submit(agent, request)
    instance.application.notify()
    run = await wait_state(instance.application, agent, receipt["run_id"], {"waiting_approval", "failed"})
    assert run["state"] == "waiting_approval", run
    approval = next(a for a in await instance.application.approvals(agent) if a["run_id"] == receipt["run_id"])
    return receipt, approval


async def approve(instance, agent, approval):
    await instance.application.approve(agent, approval["id"], True, approval["policy_revision"])


__all__ = [
    "ActionsBackend",
    "BusinessError",
    "FixtureDesktopBackend",
    "POLICY",
    "action_result",
    "approve",
    "asynccontextmanager",
    "asyncio",
    "begin",
    "hashlib",
    "listed",
    "open_instance",
    "owner_action",
    "prepared",
    "pytest",
    "queued",
    "s",
    "select",
    "update",
    "uuid",
    "verification",
    "wait_state",
]
