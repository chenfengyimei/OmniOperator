"""Reusable android_model fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import update

from omni.adapters.persistence import schema as s
from omni.composition.instance import open_instance
from omni.domain.android import ACCESS_SPEC, MODEL_SPEC, OBSERVE_SPEC, access_request, input_params, scroll_verified
from omni.domain.contracts import BusinessError, Invocation, InvocationContext, ModelReply, Outcome
from omni_test_support.v2.android import SERIAL, approve, fake_server, prepared, request_for
from omni_test_support.v2.execution import wait_state


class AndroidModel:
    def __init__(self, *, read_only=False, packages=None):
        self.step = 0
        self.request = None
        self.access = self.view = None
        self.read_only = read_only
        self.packages = packages or ["org.fixture"]
        self.catalogs, self.tool_bodies = [], []
        self.hold, self.entered = asyncio.Event(), asyncio.Event()
        self.hold.set()

    async def complete(self, messages, tools, settings):
        self.catalogs.append([t.name for t in tools])
        self.tool_bodies = [m["content"] for m in messages if m["role"] == "tool"]
        self.step += 1
        if self.step == 1:
            return ModelReply("", (Invocation("request-device-access", ACCESS_SPEC.name, self.request),), cost_micro=0)
        if self.step == 2:
            self.access = json.loads(self.tool_bodies[-1])
            return ModelReply(
                "", (Invocation("observe-device", OBSERVE_SPEC.name, {"access_id": self.access["id"]}),), cost_micro=0
            )
        if self.step == 3:
            self.entered.set()
            await self.hold.wait()
            if self.read_only:
                return ModelReply("Observation handled", cost_micro=0)
            self.view = json.loads(self.tool_bodies[-1])
            node = next(n for n in self.view["nodes"] if "append_ascii" in n["actions"])
            args = {
                "access_id": self.access["id"],
                "observation_id": self.view["observation_id"],
                "node_id": node["node_id"],
                "kind": "append_ascii",
                "text": " model",
                "params": {},
                "target_serial": self.view["target_serial"],
                "target_user": self.view["target_user"],
                "target_application": self.view["window"]["package"],
                "target_resource": node["resource_id"],
                "observation_sha256": self.view["observation_sha256"],
            }
            return ModelReply("", (Invocation("input-device", MODEL_SPEC.name, args),), cost_micro=0)
        if self.step == 4:
            return ModelReply(
                "",
                (
                    Invocation(
                        "list-scoped-apps", OBSERVE_SPEC.name, {"access_id": self.access["id"], "operation": "apps"}
                    ),
                ),
                cost_micro=0,
            )
        return ModelReply("Android operation handled", cost_micro=0)


@asynccontextmanager
async def model_instance(tmp_path, model):
    async with fake_server() as (fake, port):
        instance = await open_instance(tmp_path / "home", model=model)
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            cfg = await instance.configurations.create(
                agent,
                "channel",
                "Model device fixture",
                True,
                {"driver": "android", "endpoint": f"http://127.0.0.1:{port}", "account": SERIAL},
            )
            profile = await instance.configurations.get(
                agent, (await instance.application.settings(agent))["value"]["model_profile_id"]
            )
            await instance.configurations.save(
                agent,
                profile["id"],
                profile["name"],
                True,
                {**profile["value"], "model": "fixture"},
                profile["revision"],
            )
            probe = await instance.android.probe(agent, cfg["id"], True)
            grant = await instance.android.grant(agent, probe["probe_id"], True)
            model.request = {
                "grant_id": grant["id"],
                "target_serial": SERIAL,
                "target_user": 0,
                "packages": model.packages,
                "kinds": [] if model.read_only else ["append_ascii"],
                "ttl_seconds": 600,
            }
            yield instance, fake, agent, cfg, grant
        finally:
            model.hold.set()
            fake.allow_dump.set()
            assert (await instance.close())["complete"]


async def approve_tool(instance, agent, run_id, tool):
    await wait_state(instance.application, agent, run_id, {"waiting_approval", "failed"})
    approval = next(a for a in await instance.application.approvals(agent) if a["run_id"] == run_id)
    assert approval["tool"] == tool, approval
    await instance.application.approve(agent, approval["id"], True, approval["policy_revision"])
    return approval


__all__ = [
    "ACCESS_SPEC",
    "AndroidModel",
    "BusinessError",
    "Invocation",
    "InvocationContext",
    "MODEL_SPEC",
    "ModelReply",
    "OBSERVE_SPEC",
    "Outcome",
    "SERIAL",
    "access_request",
    "approve",
    "approve_tool",
    "asynccontextmanager",
    "asyncio",
    "dt",
    "fake_server",
    "input_params",
    "json",
    "model_instance",
    "open_instance",
    "prepared",
    "pytest",
    "request_for",
    "s",
    "scroll_verified",
    "update",
    "uuid",
    "wait_state",
]
