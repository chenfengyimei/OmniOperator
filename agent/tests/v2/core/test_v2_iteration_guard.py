"""K07 repetition degeneration exit and bounded budget grace convergence."""

from omni_test_support.v2.execution import (
    ActionResult as ActionResult,
)
from omni_test_support.v2.execution import (
    Capability as Capability,
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
    model_uow as model_uow,
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

from omni.domain.repetition import (
    is_degenerate_reply,
    is_repetition_dominated,
    is_runaway_repetition,
)

LOOP = ("ABCDEFGHIJ" * 6) * 300
NORMAL = " ".join(f"Sentence number {i} carries distinct content." for i in range(40))
ROWS = "\n".join(f"INSERT INTO t VALUES ({i}, 'data');" for i in range(60))
LARGE_ROWS = "\n".join(f"INSERT INTO t VALUES ({i}, 'data');" for i in range(600))


def test_short_text_not_flagged():
    assert is_repetition_dominated("abc" * 50) is False


def test_normal_answer_not_flagged():
    assert is_repetition_dominated(NORMAL) is False


def test_legitimate_distinct_rows_not_flagged():
    assert is_repetition_dominated(ROWS) is False


def test_single_line_echo_loop_flagged():
    assert is_repetition_dominated("echo line\n" * 100) is True


def test_window_repeat_loop_flagged():
    assert is_repetition_dominated(LOOP) is True


def test_bounded_uniform_payload_not_degenerate():
    assert is_repetition_dominated("x" * 5000) is True
    assert is_degenerate_reply("x" * 5000) is False


def test_runaway_loop_is_degenerate_reply():
    assert is_runaway_repetition(LOOP) is True
    assert is_degenerate_reply(LOOP) is True


def test_large_distinct_rows_not_degenerate():
    assert is_degenerate_reply(LARGE_ROWS) is False


def test_short_loop_below_stop_path_scale_not_degenerate():
    assert is_degenerate_reply(("ABCDEFGHIJ" * 6) * 20) is False


def call(name):
    return Invocation(str(uuid.uuid4()), name, {})


def read_spec():
    return ToolSpec("read", "read", {"type": "object"}, False, resource="alpha")


class Reader:
    def __init__(self):
        self.count = 0

    async def invoke(self, name, arguments, *, operation_id, context):
        self.count += 1
        return ActionResult(Outcome.OK, "value")

    async def close(self, timeout_s):
        return True


async def configure_steps(app, database, steps):
    agent = await configure(app, database)
    current = await app.settings(agent)
    await app.save_settings(agent, {**current["value"], "max_steps": steps}, current["revision"])
    return agent


def test_degenerate_reply_fails_without_dispatch_or_replay(tmp_path):
    class Model:
        def __init__(self):
            self.calls = 0

        async def complete(self, messages, tools, settings):
            self.calls += 1
            return ModelReply(LOOP, (call("read"),), cost_micro=7)

    async def scenario():
        model = Model()
        reader = Reader()
        instance = await open_instance(tmp_path, model=model, capabilities=(Capability(read_spec(), reader),))
        try:
            app = instance.application
            agent = await configure(app, instance._database)
            receipt = await app.submit_turn(agent, None, "go", "go")
            run = await wait_state(app, agent, receipt["run_id"], {"failed"})
            assert run["error_code"] == "model_repetition_degenerated"
            assert model.calls == 1
            assert reader.count == 0
            assert await app.query("actions", agent) == []
            calls = await app.query("calls", agent)
            assert len(calls) == 1 and calls[0]["state"] == "settled" and calls[0]["cost_micro"] == 7
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_grace_converges_at_step_budget_for_non_delegated_run(tmp_path):
    class Model:
        def __init__(self):
            self.main = 0
            self.grace = 0

        async def complete(self, messages, tools, settings):
            if not tools:
                self.grace += 1
                return ModelReply("final answer", cost_micro=0)
            self.main += 1
            return ModelReply("", (call("read"),), cost_micro=0)

    async def scenario():
        model = Model()
        reader = Reader()
        instance = await open_instance(tmp_path, model=model, capabilities=(Capability(read_spec(), reader),))
        try:
            app = instance.application
            agent = await configure_steps(app, instance._database, 1)
            receipt = await app.submit_turn(agent, None, "go", "go")
            await wait_state(app, agent, receipt["run_id"], {"succeeded"})
            assert model.main == 1 and model.grace == 1
            assert reader.count == 1
            messages = await app.messages(agent, receipt["session_id"])
            assert messages[-1]["content"] == "final answer"
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_grace_that_still_calls_tools_keeps_step_budget_exceeded(tmp_path):
    class Model:
        async def complete(self, messages, tools, settings):
            return ModelReply("", (call("read"),), cost_micro=0)

    async def scenario():
        model = Model()
        reader = Reader()
        instance = await open_instance(tmp_path, model=model, capabilities=(Capability(read_spec(), reader),))
        try:
            app = instance.application
            agent = await configure_steps(app, instance._database, 1)
            receipt = await app.submit_turn(agent, None, "go", "go")
            run = await wait_state(app, agent, receipt["run_id"], {"failed"})
            assert run["error_code"] == "step_budget_exceeded"
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_normal_short_run_succeeds_without_grace(tmp_path):
    class Model:
        async def complete(self, messages, tools, settings):
            return ModelReply("all done", cost_micro=0)

    async def scenario():
        instance = await open_instance(tmp_path, model=Model(), capabilities=())
        try:
            app = instance.application
            agent = await configure(app, instance._database)
            receipt = await app.submit_turn(agent, None, "go", "go")
            await wait_state(app, agent, receipt["run_id"], {"succeeded"})
        finally:
            await instance.close()

    asyncio.run(scenario())
