"""Abrupt interruption through the production engine and recovery with the same instance home."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from omni_test_support.v2.execution import configure, wait_state

from omni.adapters.persistence.repository import Repositories
from omni.application.engine import Application
from omni.application.model_calls import ModelCalls
from omni.composition.instance import open_instance
from omni.domain.contracts import Invocation, ModelReply
from omni.domain.routing import POLICY_DEFAULT

CRASH = 77


def crash_at(phase):
    if phase == "before-model":

        async def before(*args, **kwargs):
            os._exit(CRASH)

        ModelCalls._invoke_once = before
    elif phase == "after-model":
        original = Repositories.save_run_checkpoint

        def before_planning(self, agent_id, run_id, step, model_revision_id, calls=()):
            if calls:
                os._exit(CRASH)
            return original(self, agent_id, run_id, step, model_revision_id, calls)

        Repositories.save_run_checkpoint = before_planning
    elif phase == "dispatched":

        def before_result(*args, **kwargs):
            os._exit(CRASH)

        Repositories.settle_action = before_result
    elif phase == "settled":

        async def after_result(*args, **kwargs):
            os._exit(CRASH)

        Application._tool_message_content = after_result


async def interrupted_run(home, phase):
    class FirstProcess:
        async def complete(self, messages, tools, settings):
            if phase == "model-in-flight":
                os._exit(CRASH)
            return ModelReply(
                "", (Invocation("write", "files_write", {"path": "result.txt", "content": "one write"}),), cost_micro=5
            )

    crash_at(phase)
    instance = await open_instance(home, model=FirstProcess())
    try:
        app = instance.application
        agent = await configure(app, instance._database)
        work = home.parent / "workspace"
        work.mkdir()
        cfg = await app.settings(agent)
        await app.save_settings(agent, {**cfg["value"], "workspace": str(work)}, cfg["revision"])
        await instance.routing.save(
            agent,
            {**POLICY_DEFAULT, "kernel": {"safe_resume": True, "background_learning": False}},
            0,
        )
        turn = await app.submit_turn(agent, None, "one", "Write the result and finish", mode="agent")
        async with asyncio.timeout(35):
            while True:
                for approval in await app.approvals(agent):
                    if phase == "approval":
                        os._exit(CRASH)
                    await app.approve(agent, approval["id"], True, approval["policy_revision"])
                state = await app.get_run(agent, turn["run_id"])
                if state["state"] == "failed":
                    raise AssertionError(state)
                await asyncio.sleep(0.02)
    finally:
        await instance.close()


@pytest.mark.parametrize(
    ("phase", "allowed", "effects"),
    [
        ("before-model", True, []),
        ("model-in-flight", False, []),
        ("after-model", False, []),
        ("approval", False, ["none"]),
        ("dispatched", False, ["unknown"]),
        ("settled", True, ["applied"]),
        ("two-restarts", True, ["applied"]),
    ],
)
def test_reboot_resumes_only_at_a_proven_boundary(tmp_path, phase, allowed, effects):
    home = tmp_path / "home"
    launcher = (
        "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_path(sys.argv.pop(1),run_name='__main__')"
    )
    command = [sys.executable, "-c", launcher, str(Path(__file__).resolve().parents[2]), __file__, str(home)]
    phases = ("settled", "reopen-before-model") if phase == "two-restarts" else (phase,)
    for stop in phases:
        child = subprocess.run(
            [*command, stop],
            capture_output=True,
            timeout=60,
            check=False,
            env={**os.environ, "OMNI_TEST_OFFLINE": "1"},
        )
        assert child.returncode == CRASH, child.stderr.decode(errors="replace")

    class RecoveryModel:
        def __init__(self):
            self.calls = 0

        async def complete(self, messages, tools, settings):
            self.calls += 1
            assert "previous process" in str(messages)
            if phase in {"settled", "two-restarts"}:
                assert "files_write" in str(messages) and "result.txt" in str(messages)
            return ModelReply("Continued from verified execution boundaries.", cost_micro=7)

    async def scenario():
        model = RecoveryModel()
        instance = await open_instance(home, model=model)
        try:
            app = instance.application
            [agent] = await app.query("agents", "")
            [run] = await app.query("runs", agent["id"])
            row = await wait_state(app, agent["id"], run["id"], {"interrupted", "succeeded", "failed"})
            assert row["state"] == ("succeeded" if allowed else "interrupted"), row
            assert model.calls == int(allowed) and not await app.approvals(agent["id"])
            actions = await app.query("actions", agent["id"], run_id=run["id"])
            assert [item["effect"] for item in actions] == effects
            assert row["continuation"]["decision"] == ("resumed" if allowed else "review")
            calls = await app.query("calls", agent["id"], run_id=run["id"])
            if phase == "model-in-flight":
                assert [call["state"] for call in calls] == ["unknown"]
            events = await instance._database.read(lambda u: u.query("events", agent["id"], run_id=run["id"]))
            assert sum(event["kind"] == "run.resumed" for event in events) == int(allowed) + int(
                phase == "two-restarts"
            )
            if phase in {"settled", "two-restarts"}:
                assert (tmp_path / "workspace" / "result.txt").read_text(encoding="utf-8") == "one write"
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


if __name__ == "__main__":

    async def reopen(home):
        crash_at("before-model")
        instance = await open_instance(home, model=object())
        try:
            await asyncio.sleep(15)
        finally:
            await instance.close()

    asyncio.run(
        reopen(Path(sys.argv[1]))
        if sys.argv[2] == "reopen-before-model"
        else interrupted_run(Path(sys.argv[1]), sys.argv[2])
    )
