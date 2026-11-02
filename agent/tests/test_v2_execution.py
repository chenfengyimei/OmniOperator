"""Session ordering, independent concurrency, approvals and cancellation certainty."""

import asyncio
import uuid

from omni.application.engine import Capability
from omni.composition.instance import open_instance
from omni.domain.contracts import Invocation, ModelReply, ToolSpec


async def wait_state(app, agent, run_id, states):
    async with asyncio.timeout(10):
        while True:
            run = await app.get_run(agent, run_id)
            if run["state"] in states:
                return run
            await asyncio.sleep(0.01)


async def configure(app):
    agent = (await app.query("agents", ""))[0]["id"]
    settings = await app.settings(agent)
    await app.save_settings(agent, {**settings["value"], "model": "fake"}, settings["revision"])
    return agent


def test_same_session_fifo_and_other_session_progress(tmp_path):
    class Model:
        def __init__(self):
            self.release = asyncio.Event()
            self.contexts = []

        async def complete(self, messages, tools, settings):
            content = messages[-1]["content"]
            self.contexts.append([m["content"] for m in messages if m["role"] != "system"])
            if content == "first":
                await self.release.wait()
            return ModelReply("reply " + content, cost_micro=0)

    async def scenario():
        model = Model()
        instance = await open_instance(tmp_path, model=model, capabilities=())
        try:
            app = instance.application
            agent = await configure(app)
            one = await app.submit_turn(agent, None, "first", "first")
            await wait_state(app, agent, one["run_id"], {"running"})
            two = await app.submit_turn(agent, one["session_id"], "second", "second")
            other = await app.submit_turn(agent, None, "independent", "independent")
            await wait_state(app, agent, other["run_id"], {"succeeded"})
            assert (await app.get_run(agent, two["run_id"]))["state"] == "queued"
            model.release.set()
            await wait_state(app, agent, two["run_id"], {"succeeded"})
            assert model.contexts[-1] == ["first", "reply first", "second"]
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_write_is_not_dispatched_before_approval_and_cancel_preserves_unknown(tmp_path):
    class Writer:
        def __init__(self):
            self.started = asyncio.Event()
            self.calls = 0

        async def invoke(self, name, arguments, *, operation_id, context):
            self.calls += 1
            self.started.set()
            await asyncio.Event().wait()

        async def close(self, timeout_s):
            return True

    class Model:
        async def complete(self, messages, tools, settings):
            return ModelReply("", (Invocation(str(uuid.uuid4()), "write", {}),), cost_micro=0)

    async def scenario():
        writer = Writer()
        instance = await open_instance(
            tmp_path,
            model=Model(),
            capabilities=(Capability(ToolSpec("write", "write", {"type": "object"}, True), writer),),
        )
        try:
            app = instance.application
            agent = await configure(app)
            receipt = await app.submit_turn(agent, None, "write", "write")
            await wait_state(app, agent, receipt["run_id"], {"waiting_approval"})
            assert writer.calls == 0
            approval = (await app.approvals(agent))[0]
            await app.approve(agent, approval["id"], True, 2)
            async with asyncio.timeout(10):
                await writer.started.wait()
            await app.cancel(agent, receipt["run_id"])
            await wait_state(app, agent, receipt["run_id"], {"cancelled"})
            actions = await app.query("actions", agent)
            assert writer.calls == 1
            assert actions[0]["state"] == actions[0]["effect"] == "unknown"
            assert not await app.approvals(agent)
        finally:
            await instance.close()

    asyncio.run(scenario())
