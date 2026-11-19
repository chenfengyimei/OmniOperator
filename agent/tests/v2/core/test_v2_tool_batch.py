"""K05 batch tool parallelism: independent reads concurrent, interactive/conflicting serial."""

from omni_test_support.v2.execution import (
    ActionResult as ActionResult,
)
from omni_test_support.v2.execution import (
    Capability as Capability,
)
from omni_test_support.v2.execution import (
    Effect as Effect,
)
from omni_test_support.v2.execution import (
    Invocation as Invocation,
)
from omni_test_support.v2.execution import (
    ModelReply as ModelReply,
)
from omni_test_support.v2.execution import (
    Outcome as Outcome,
)
from omni_test_support.v2.execution import (
    ToolSpec as ToolSpec,
)
from omni_test_support.v2.execution import (
    asyncio as asyncio,
)
from omni_test_support.v2.execution import (
    configure as configure,
)
from omni_test_support.v2.execution import (
    open_instance as open_instance,
)
from omni_test_support.v2.execution import (
    uuid as uuid,
)
from omni_test_support.v2.execution import (
    wait_state as wait_state,
)

from omni.domain.tool_batch import BatchEntry, plan_tool_batch


def read(name, resource):
    return BatchEntry(name, resource=resource, parallel=True)


def barrier(name):
    return BatchEntry(name)


def test_independent_reads_form_one_parallel_segment():
    plan = plan_tool_batch([read("a", "alpha"), read("b", "beta"), read("c", "gamma")])
    assert plan == (("parallel", (0, 1, 2)),)


def test_same_resource_reads_stay_serial():
    plan = plan_tool_batch([read("a", "shared"), read("b", "shared")])
    assert plan == (("serial", (0, 1)),)


def test_resource_conflict_closes_run_and_preserves_order():
    plan = plan_tool_batch([read("a", "alpha"), read("b", "beta"), read("c", "alpha")])
    assert plan == (("parallel", (0, 1)), ("serial", (2,)))


def test_writes_and_controls_are_serial_barriers():
    plan = plan_tool_batch(
        [read("a", "alpha"), read("b", "beta"), barrier("write"), read("c", "gamma"), read("d", "delta")]
    )
    assert plan == (("parallel", (0, 1)), ("serial", (2,)), ("parallel", (3, 4)))


def test_single_call_demotes_and_adjacent_serial_merges():
    plan = plan_tool_batch([read("a", "alpha"), barrier("control"), barrier("write")])
    assert plan == (("serial", (0, 1, 2)),)


def test_empty_batch_has_no_segments():
    assert plan_tool_batch([]) == ()


def test_empty_resource_falls_back_to_name():
    assert plan_tool_batch([read("a", ""), read("a", "")]) == (("serial", (0, 1)),)
    assert plan_tool_batch([read("a", ""), read("b", "")]) == (("parallel", (0, 1)),)


def test_indices_map_to_original_call_positions():
    plan = plan_tool_batch(
        [barrier("control"), read("a", "alpha"), read("b", "beta"), barrier("write"), read("c", "gamma")]
    )
    assert plan == (("serial", (0,)), ("parallel", (1, 2)), ("serial", (3, 4)))


def call(name):
    return Invocation(str(uuid.uuid4()), name, {})


def batch_model(calls):
    class Model:
        def __init__(self):
            self.rounds = 0
            self.inputs = []

        async def complete(self, messages, tools, settings):
            self.rounds += 1
            self.inputs.append([dict(m) for m in messages])
            if self.rounds == 1:
                return ModelReply("", tuple(calls), cost_micro=0)
            return ModelReply("done", cost_micro=0)

    return Model()


def spec(name, writes=False, resource=""):
    return ToolSpec(name, name, {"type": "object"}, writes, resource=resource)


def tool_messages(messages):
    return [m["content"] for m in messages if m["role"] == "tool"]


def test_independent_reads_run_concurrently_and_results_keep_call_order(tmp_path):
    class Readers:
        def __init__(self):
            self.started = {"read_a": asyncio.Event(), "read_b": asyncio.Event()}
            self.counts = {"read_a": 0, "read_b": 0}
            self.finish = []

        async def invoke(self, name, arguments, *, operation_id, context):
            self.counts[name] += 1
            self.started[name].set()
            other = "read_b" if name == "read_a" else "read_a"
            # A sequential dispatcher could never start the sibling: this wait would time out.
            async with asyncio.timeout(5):
                await self.started[other].wait()
            if name == "read_a":
                await asyncio.sleep(0.05)
            self.finish.append(name)
            return ActionResult(Outcome.OK, "result " + name)

        async def close(self, timeout_s):
            return True

    async def scenario():
        readers = Readers()
        model = batch_model([call("read_a"), call("read_b")])
        instance = await open_instance(
            tmp_path,
            model=model,
            capabilities=(
                Capability(spec("read_a", resource="alpha"), readers),
                Capability(spec("read_b", resource="beta"), readers),
            ),
        )
        try:
            app = instance.application
            agent = await configure(app, instance._database)
            receipt = await app.submit_turn(agent, None, "go", "go")
            await wait_state(app, agent, receipt["run_id"], {"succeeded"})
            assert readers.counts == {"read_a": 1, "read_b": 1}
            assert readers.finish == ["read_b", "read_a"]
            # The next model call must see results in original call order, not finish order.
            assert tool_messages(model.inputs[1]) == ["result read_a", "result read_b"]
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_same_resource_reads_execute_serially(tmp_path):
    class SerialReader:
        def __init__(self):
            self.events = []

        async def invoke(self, name, arguments, *, operation_id, context):
            self.events.append("start " + name)
            await asyncio.sleep(0.02)
            self.events.append("finish " + name)
            return ActionResult(Outcome.OK, name)

        async def close(self, timeout_s):
            return True

    async def scenario():
        reader = SerialReader()
        instance = await open_instance(
            tmp_path,
            model=batch_model([call("read_a"), call("read_b")]),
            capabilities=(
                Capability(spec("read_a", resource="shared"), reader),
                Capability(spec("read_b", resource="shared"), reader),
            ),
        )
        try:
            app = instance.application
            agent = await configure(app, instance._database)
            receipt = await app.submit_turn(agent, None, "go", "go")
            await wait_state(app, agent, receipt["run_id"], {"succeeded"})
            assert reader.events == ["start read_a", "finish read_a", "start read_b", "finish read_b"]
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_write_between_reads_is_a_serial_barrier_with_approval(tmp_path):
    class Mixed:
        def __init__(self):
            self.order = []

        async def invoke(self, name, arguments, *, operation_id, context):
            self.order.append(name)
            if name == "write_w":
                return ActionResult(Outcome.OK, "written", Effect.APPLIED)
            return ActionResult(Outcome.OK, name)

        async def close(self, timeout_s):
            return True

    async def scenario():
        mixed = Mixed()
        instance = await open_instance(
            tmp_path,
            model=batch_model([call("read_a"), call("write_w"), call("read_b")]),
            capabilities=(
                Capability(spec("read_a", resource="alpha"), mixed),
                Capability(spec("write_w", writes=True, resource="beta"), mixed),
                Capability(spec("read_b", resource="gamma"), mixed),
            ),
        )
        try:
            app = instance.application
            agent = await configure(app, instance._database)
            receipt = await app.submit_turn(agent, None, "go", "go")
            await wait_state(app, agent, receipt["run_id"], {"waiting_approval"})
            assert mixed.order == ["read_a"]
            approvals = await app.approvals(agent)
            assert len(approvals) == 1 and approvals[0]["tool"] == "write_w"
            await app.approve(agent, approvals[0]["id"], True, approvals[0]["policy_revision"])
            await wait_state(app, agent, receipt["run_id"], {"succeeded"})
            assert mixed.order == ["read_a", "write_w", "read_b"]
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_two_writes_never_share_an_approval_window(tmp_path):
    class Writers:
        def __init__(self):
            self.order = []

        async def invoke(self, name, arguments, *, operation_id, context):
            self.order.append(name)
            return ActionResult(Outcome.OK, name, Effect.APPLIED)

        async def close(self, timeout_s):
            return True

    async def scenario():
        writers = Writers()
        instance = await open_instance(
            tmp_path,
            model=batch_model([call("write_a"), call("write_b")]),
            capabilities=(
                Capability(spec("write_a", writes=True, resource="alpha"), writers),
                Capability(spec("write_b", writes=True, resource="beta"), writers),
            ),
        )
        try:
            app = instance.application
            agent = await configure(app, instance._database)
            receipt = await app.submit_turn(agent, None, "go", "go")
            await wait_state(app, agent, receipt["run_id"], {"waiting_approval"})
            approvals = await app.approvals(agent)
            assert len(approvals) == 1 and approvals[0]["tool"] == "write_a"
            assert writers.order == []
            await app.approve(agent, approvals[0]["id"], True, approvals[0]["policy_revision"])
            await wait_state(app, agent, receipt["run_id"], {"waiting_approval"})
            assert writers.order == ["write_a"]
            approvals = await app.approvals(agent)
            assert len(approvals) == 1 and approvals[0]["tool"] == "write_b"
            await app.approve(agent, approvals[0]["id"], True, approvals[0]["policy_revision"])
            await wait_state(app, agent, receipt["run_id"], {"succeeded"})
            assert writers.order == ["write_a", "write_b"]
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_cancel_during_parallel_batch_settles_every_started_action(tmp_path):
    class Blockers:
        def __init__(self):
            self.started = {"read_a": asyncio.Event(), "read_b": asyncio.Event()}
            self.counts = {"read_a": 0, "read_b": 0}
            self.release = asyncio.Event()

        async def invoke(self, name, arguments, *, operation_id, context):
            self.counts[name] += 1
            self.started[name].set()
            await self.release.wait()
            return ActionResult(Outcome.OK, name)

        async def close(self, timeout_s):
            return True

    async def scenario():
        blockers = Blockers()
        instance = await open_instance(
            tmp_path,
            model=batch_model([call("read_a"), call("read_b")]),
            capabilities=(
                Capability(spec("read_a", resource="alpha"), blockers),
                Capability(spec("read_b", resource="beta"), blockers),
            ),
        )
        try:
            app = instance.application
            agent = await configure(app, instance._database)
            receipt = await app.submit_turn(agent, None, "go", "go")
            async with asyncio.timeout(10):
                await blockers.started["read_a"].wait()
                await blockers.started["read_b"].wait()
            await app.cancel(agent, receipt["run_id"])
            await wait_state(app, agent, receipt["run_id"], {"cancelled"})
            assert blockers.counts == {"read_a": 1, "read_b": 1}
            actions = await app.query("actions", agent)
            assert len(actions) == 2
            assert all(a["state"] != "executing" for a in actions)
            assert all(a["state"] == "error" and a["effect"] == "none" for a in actions)
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_unknown_effect_in_parallel_batch_fails_run_after_sibling_settles(tmp_path):
    class Pair:
        def __init__(self):
            self.started = {"read_a": asyncio.Event(), "read_b": asyncio.Event()}
            self.counts = {"read_a": 0, "read_b": 0}

        async def invoke(self, name, arguments, *, operation_id, context):
            self.counts[name] += 1
            self.started[name].set()
            other = "read_b" if name == "read_a" else "read_a"
            async with asyncio.timeout(5):
                await self.started[other].wait()
            if name == "read_a":
                return ActionResult(Outcome.UNKNOWN, "unknown effect", Effect.UNKNOWN)
            return ActionResult(Outcome.OK, "result read_b")

        async def close(self, timeout_s):
            return True

    async def scenario():
        pair = Pair()
        instance = await open_instance(
            tmp_path,
            model=batch_model([call("read_a"), call("read_b")]),
            capabilities=(
                Capability(spec("read_a", resource="alpha"), pair),
                Capability(spec("read_b", resource="beta"), pair),
            ),
        )
        try:
            app = instance.application
            agent = await configure(app, instance._database)
            receipt = await app.submit_turn(agent, None, "go", "go")
            await wait_state(app, agent, receipt["run_id"], {"failed"})
            assert pair.counts == {"read_a": 1, "read_b": 1}
            run = await app.get_run(agent, receipt["run_id"])
            assert run["state"] == "failed" and run["error_code"] == "action_outcome_unknown"
            actions = {a["tool"]: a for a in await app.query("actions", agent)}
            assert actions["read_a"]["state"] == "unknown" and actions["read_a"]["effect"] == "unknown"
            # The independent sibling settled with its real result; it was not abandoned or replayed.
            assert actions["read_b"]["state"] == "ok"
            assert actions["read_b"]["result"]["text"] == "result read_b"
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_parallel_batch_preserves_per_action_records(tmp_path):
    class Reader:
        async def invoke(self, name, arguments, *, operation_id, context):
            return ActionResult(Outcome.OK, name)

        async def close(self, timeout_s):
            return True

    async def scenario():
        reader = Reader()
        instance = await open_instance(
            tmp_path,
            model=batch_model([call("read_a"), call("read_b")]),
            capabilities=(
                Capability(spec("read_a", resource="alpha"), reader),
                Capability(spec("read_b", resource="beta"), reader),
            ),
        )
        try:
            app = instance.application
            agent = await configure(app, instance._database)
            receipt = await app.submit_turn(agent, None, "go", "go")
            await wait_state(app, agent, receipt["run_id"], {"succeeded"})
            actions = await app.query("actions", agent)
            assert {a["tool"] for a in actions} == {"read_a", "read_b"}
            assert all(a["state"] == "ok" and a["effect"] == "none" for a in actions)
            assert len({a["id"] for a in actions}) == 2
            assert not await app.approvals(agent)
        finally:
            await instance.close()

    asyncio.run(scenario())
