"""Reusable skill_reads fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from omni.application.skills import SkillService
from omni.domain.contracts import BusinessError, Effect, InvocationContext, Outcome
from omni.domain.skills import SkillPackage
from omni_test_support.v2.skill_notice import ReadGate, prepared_instance
from omni_test_support.v2.skills import markdown

TOOLS = ("skills_list", "skill_history", "skill_activate", "skill_view")

CONTEXT = InvocationContext("agent", "run", "session", 1)

REFERENCE = "Declared reference text, with 中文."

STAGES = [("skills_list", "catalog"), ("skill_history", "catalog"), ("skill_history", "history")]

STAGES += [
    (name, stage) for name in ("skill_activate", "skill_view") for stage in ("catalog", "row", "package", "activate")
]


def arguments(name):
    if name == "skills_list":
        return {}
    result = {"skill_id": "skill"}
    if name != "skill_history":
        result["revision_id"] = "revision"
    if name == "skill_view":
        result["path"] = "references/guide.md"
    return result


def fixture(stage=None):
    state = SimpleNamespace(calls=[], cancelled=[], activations=[], arrivals=0, captured=True, revoked=False)
    entered, two, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    main = markdown().encode()
    files = [
        {"path": path, "sha256": "a" * 64, "bytes": len(raw)}
        for path, raw in (("SKILL.md", main), ("references/guide.md", REFERENCE.encode()))
    ]
    row = {"id": "skill", "agent_id": "agent", "revision": 7, "content_revision": 3, "files": files}

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

    class Database:
        async def read(self, operation):
            value = operation(self)
            await pause(state.calls[-1])
            return value

        async def write(self, operation):
            value = operation(self)
            await pause(state.calls[-1])
            return value

        def skill_catalog(self, agent, run, platform):
            assert (agent, run, platform) == ("agent", "run", "windows")
            state.calls.append("catalog")
            return [{"skill_id": "skill", "revision_id": "revision"}] if state.captured else []

        def skill_get(self, agent, skill, revision):
            assert (agent, skill, revision) == ("agent", "skill", "revision")
            state.calls.append("row")
            return row

        def skill_history(self, agent, skill, after, limit):
            assert (agent, skill, after, limit) == ("agent", "skill", 0, 50)
            state.calls.append("history")
            return [{"revision": 7, "revision_id": "revision"}]

        def skill_activate_run(self, agent, run, skill, platform, operation):
            assert (agent, run, skill, platform, operation) == ("agent", "run", "skill", "windows", "operation")
            if state.revoked:
                raise BusinessError("skill_authority_revoked", "Fixture revocation before activation")
            state.calls.append("activate")
            state.activations.append(operation)
            return row

    service = SkillService(Database(), None, None, "windows")

    async def package(value):
        assert value is row
        state.calls.append("package")
        await pause("package")
        return SkillPackage({}, (("SKILL.md", main), ("references/guide.md", REFERENCE.encode())))

    service._package = package
    return service, state, entered, two, release


async def invoke(service, name, params=None):
    return await service.invoke(
        name, arguments(name) if params is None else params, operation_id="operation", context=CONTEXT
    )


def assert_result(result, name):
    assert result.status == Outcome.OK and result.effect == Effect.NONE
    value = json.loads(result.text)
    if name in {"skills_list", "skill_history"}:
        assert len(value) == 1 and result.data == {"items": value}
    else:
        assert value["revision"] == 7 and value["content_revision"] == 3
        assert value["skill_id"] == "skill" and value["revision_id"] == "revision"
        assert value["content"] == (REFERENCE if name == "skill_view" else markdown())
        assert result.data == {key: cell for key, cell in value.items() if key != "content"}


__all__ = [
    "BusinessError",
    "CONTEXT",
    "Effect",
    "InvocationContext",
    "Outcome",
    "REFERENCE",
    "ReadGate",
    "STAGES",
    "SimpleNamespace",
    "SkillPackage",
    "SkillService",
    "TOOLS",
    "arguments",
    "assert_result",
    "asyncio",
    "fixture",
    "invoke",
    "json",
    "markdown",
    "prepared_instance",
    "pytest",
]
