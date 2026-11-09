"""Session ordering, independent concurrency, approvals and cancellation certainty."""

import asyncio
import uuid
from contextlib import nullcontext

import pytest
from v2_configuration_helpers import model_uow

from omni.adapters.persistence.database import Database
from omni.adapters.primitives import SystemClock, UUIDs
from omni.adapters.token_counter import ConservativeTokenCounter
from omni.application.context import ContextService
from omni.application.engine import Application, Capability
from omni.application.model_calls import ModelCalls
from omni.composition.instance import open_instance
from omni.domain.contracts import ActionResult, Effect, Invocation, ModelReply, Outcome, ToolSpec


async def wait_state(app, agent, run_id, states):
    async with asyncio.timeout(10):
        while True:
            run = await app.get_run(agent, run_id)
            if run["state"] in states:
                return run
            await asyncio.sleep(0.01)


async def configure(app, database):
    agent = (await app.query("agents", ""))[0]["id"]
    await database.write(lambda u: model_uow(u, agent))
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
            agent = await configure(app, instance._database)
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
            agent = await configure(app, instance._database)
            receipt = await app.submit_turn(agent, None, "write", "write")
            await wait_state(app, agent, receipt["run_id"], {"waiting_approval"})
            assert writer.calls == 0
            approval = (await app.approvals(agent))[0]
            await app.approve(agent, approval["id"], True, approval["policy_revision"])
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


@pytest.mark.parametrize("interrupt_after_grant", [False, True])
def test_owner_can_approve_after_the_waiter_reads_a_pending_snapshot(tmp_path, interrupt_after_grant):
    class Observer:
        def context(self, **fields):
            return nullcontext()

        def span(self, name):
            return nullcontext()

    class Writer:
        calls = 0

        async def invoke(self, name, arguments, *, operation_id, context):
            self.calls += 1
            return ActionResult(Outcome.OK, "written", Effect.APPLIED)

        async def close(self, timeout_s):
            return True

    class Model:
        async def complete(self, messages, tools, settings):
            if messages[-1]["role"] == "tool":
                return ModelReply("done", cost_micro=0)
            return ModelReply("", (Invocation(str(uuid.uuid4()), "write", {}),), cost_micro=0)

    class Interleaved:
        def __init__(self, database):
            self.database, self.injected = database, False

        async def read(self, operation):
            value = await self.database.read(operation)
            if (
                isinstance(value, dict)
                and value.get("tool") == "write"
                and value["state"] == "pending"
                and not self.injected
            ):
                self.injected = True
                agent = value["agent_id"]
                approval = (await self.database.read(lambda u: u.pending_approvals(agent)))[0]
                await self.database.write(
                    lambda u: u.resolve_approval(agent, approval["id"], True, approval["policy_revision"])
                )
            return value

        async def write(self, operation):
            value = await self.database.write(operation)
            if (
                interrupt_after_grant
                and isinstance(value, dict)
                and value.get("tool") == "write"
                and value["state"] == "approved"
            ):
                raise asyncio.CancelledError
            return value

        async def close(self):
            await self.database.close()

    async def scenario():
        clock = SystemClock()
        database = Interleaved(Database(tmp_path / "omni.db", clock, UUIDs()))
        writer = Writer()
        observer = Observer()
        calls = ModelCalls(database, Model(), observer, ConservativeTokenCounter())
        app = Application(
            database,
            calls,
            ContextService(database, calls),
            (Capability(ToolSpec("write", "test", {"type": "object"}, True), writer),),
            clock,
            Observer(),
        )
        await app.start()
        try:
            agent = await configure(app, database)
            receipt = await app.submit_turn(agent, None, "interleaving", "write")
            result = await wait_state(
                app, agent, receipt["run_id"], {"succeeded", "failed", "interrupted", "cancelled"}
            )
            assert database.injected
            assert result["state"] == ("cancelled" if interrupt_after_grant else "succeeded"), result
            assert writer.calls == (0 if interrupt_after_grant else 1)
            assert (await app.query("actions", agent))[0]["state"] == ("unknown" if interrupt_after_grant else "ok")
        finally:
            await app.close()

    asyncio.run(scenario())
