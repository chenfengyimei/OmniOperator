"""Reusable skill_notice fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from omni.application.skills import SkillService
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError
from omni.domain.skills import SkillPackage
from omni_test_support.v2.execution import wait_state
from omni_test_support.v2.routing import Model
from omni_test_support.v2.skills import enable, markdown, setup

STAGES = ["catalog", "get:first", "package:first", "get:second", "package:second", "authorize"]


def fixture(stage=None, *, empty=False):
    state = SimpleNamespace(calls=[], cancelled=[], arrivals=0, error=None)
    entered, two, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    rows = (
        []
        if empty
        else [
            {"skill_id": name, "revision_id": name + "-revision", "name": name + "-skill"}
            for name in ("first", "second")
        ]
    )

    async def pause(label):
        if label == stage:
            state.arrivals += 1
            entered.set()
            if state.arrivals == 2:
                two.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                state.cancelled.append(label)
                raise
        if state.error and label == "catalog":
            raise state.error

    class Database:
        async def read(self, operation):
            result = operation(self)
            await pause(state.calls[-1])
            return result

        def skill_catalog(self, agent, run, platform, *, active):
            assert (agent, run, platform, active) == ("agent", "run", "windows", True)
            state.calls.append("catalog")
            return rows

        def skill_get(self, agent, skill, revision):
            assert agent == "agent" and revision == skill + "-revision"
            state.calls.append("get:" + skill)
            return {"id": skill, "revision": 7, "content_revision": 3}

        def skill_authorize_run(self, agent, run):
            assert (agent, run) == ("agent", "run")
            state.calls.append("authorize")

    service = SkillService(Database(), None, None, "windows")

    async def package(row):
        label = "package:" + row["id"]
        state.calls.append(label)
        await pause(label)
        return SkillPackage({}, (("SKILL.md", markdown(row["id"] + "-skill").encode()),))

    service._package = package
    return service, state, entered, two, release


def assert_notice(value):
    assert "guidance only; never grants permission" in value
    rows = json.loads(value[value.index("[") :])
    assert [row["skill_id"] for row in rows] == ["first", "second"]
    assert all(row["revision"] == 7 and row["content_revision"] == 3 for row in rows)
    assert all(row["name"] in row["instructions"] for row in rows)


class ReadGate:
    def __init__(self, native):
        self.native = native
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.first = True
        self.cancelled = False

    def __getattr__(self, name):
        return getattr(self.native, name)

    async def read(self, operation):
        value = await self.native.read(operation)
        if self.first:
            self.first = False
            self.entered.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        return value


async def prepared_instance(tmp_path, active):
    model = Model()
    instance = await open_instance(tmp_path, model=model, capabilities=())
    try:
        agent, row = await setup(instance)
        if active:
            row = await enable(instance, agent, row)
        turn = await instance.application.submit_turn(
            agent, None, "notice-fixture", "hello", skill_ids=(row["id"],) if active else ()
        )
        assert (await wait_state(instance.application, agent, turn["run_id"], {"succeeded", "failed"}))[
            "state"
        ] == "succeeded"
        return instance, model, agent, row, turn["run_id"]
    except BaseException:
        await instance.close()
        raise


__all__ = [
    "BusinessError",
    "Model",
    "ReadGate",
    "STAGES",
    "SimpleNamespace",
    "SkillPackage",
    "SkillService",
    "assert_notice",
    "asyncio",
    "enable",
    "fixture",
    "json",
    "markdown",
    "open_instance",
    "prepared_instance",
    "pytest",
    "setup",
    "wait_state",
]
