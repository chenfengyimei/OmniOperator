"""K12 bounded model empty-reply recovery: same authorized model, budget-gated, unknown never replayed.

Recovery is opt-in (default OFF). Each retry is a fresh reserved call that re-bills the input;
deterministic empties short-circuit, an expensive attempt drops the budget to 1, and an unknown-cost
call settled ``unknown`` is never replayed. No account/model fallback exists.
"""

from omni_test_support.v2.execution import (
    Invocation as Invocation,
)
from omni_test_support.v2.execution import (
    ModelReply as ModelReply,
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
    wait_state as wait_state,
)
from sqlalchemy import select

from omni.adapters.persistence import schema as s
from omni.domain.model_recovery import (
    DEFAULT_COST_THRESHOLD_MICRO,
    EmptyAttempt,
    deterministic_empty,
    empty_attempt,
    is_empty,
    should_retry_empty,
)


def attempt(cost=7, output=0, model="m", provider="p", finish="stop"):
    return EmptyAttempt(
        model=model,
        provider=provider,
        finish_reason=finish,
        usage_present=output is not None,
        zero_output=output == 0,
        cost_micro=cost,
    )


def test_is_empty_matches_the_reply_contract():
    assert is_empty(ModelReply(" \n", ()))
    assert is_empty(ModelReply("", ()))
    assert not is_empty(ModelReply("done", ()))
    assert not is_empty(ModelReply("", (Invocation("one", "read", {}),)))


def test_empty_attempt_extracts_usage_and_cost():
    a = empty_attempt(ModelReply("", (), input_tokens=3, output_tokens=0, cost_micro=7), model="m", provider="p")
    assert a.usage_present and a.zero_output and a.cost_micro == 7
    b = empty_attempt(ModelReply("", (), input_tokens=3, output_tokens=2, cost_micro=None), model="m", provider="p")
    assert b.usage_present and not b.zero_output and b.cost_micro is None


def test_deterministic_empty_requires_two_same_signature_zero_output():
    assert not deterministic_empty([attempt()])
    assert deterministic_empty([attempt(), attempt()])
    assert not deterministic_empty([attempt(), attempt(model="other")])
    assert not deterministic_empty([attempt(), attempt(output=2)])
    assert not deterministic_empty([attempt(output=2), attempt(output=2)])


def test_should_retry_unknown_cost_never_replays():
    assert not should_retry_empty([attempt(cost=None)])
    assert not should_retry_empty([attempt(), attempt(cost=None)])


def test_should_retry_budget_and_cost_aware_reduction():
    assert should_retry_empty([attempt(output=2)])
    assert should_retry_empty([attempt(output=2), attempt(output=2)])
    assert not should_retry_empty([attempt(output=2), attempt(output=2), attempt(output=2)])
    assert not should_retry_empty([attempt(cost=DEFAULT_COST_THRESHOLD_MICRO, output=2)])
    assert not should_retry_empty([attempt(), attempt()])


class Script:
    def __init__(self, replies, always_empty=False):
        self.replies = list(replies)
        self.always_empty = always_empty
        self.requests = 0

    async def complete(self, messages, tools, settings):
        self.requests += 1
        if self.always_empty:
            return ModelReply(" \n", (), input_tokens=3, output_tokens=2, cost_micro=7)
        return self.replies.pop(0) if self.replies else ModelReply("done", cost_micro=7)


def empty(output=2, cost=7):
    return ModelReply(" \n", (), input_tokens=3, output_tokens=output, cost_micro=cost)


async def run_recovery(tmp_path, replies, *, model_recovery=True, always_empty=False):
    model = Script(replies, always_empty)
    instance = await open_instance(tmp_path, model=model, capabilities=(), model_recovery=model_recovery)
    try:
        app = instance.application
        instance.maintenance.stop_accepting()
        agent = await configure(app, instance._database)
        receipt = await app.submit_turn(agent, None, "k", "go")
        run = await wait_state(app, agent, receipt["run_id"], {"succeeded", "failed"})
        calls = await instance._database.read(
            lambda u: [
                dict(r)
                for r in u.connection.execute(
                    select(s.model_calls).where(s.model_calls.c.run_id == receipt["run_id"])
                ).mappings()
            ]
        )
        return {"run": run, "model": model, "calls": calls}
    finally:
        await instance.close()


def test_recovery_retries_empty_then_succeeds_on_same_model(tmp_path):
    async def scenario():
        out = await run_recovery(tmp_path, [empty(), empty()])
        assert out["run"]["state"] == "succeeded"
        assert out["model"].requests == 3
        assert len(out["calls"]) == 3 and all(c["state"] == "settled" and c["cost_micro"] == 7 for c in out["calls"])

    asyncio.run(scenario())


def test_deterministic_empty_stops_recovery_early(tmp_path):
    async def scenario():
        out = await run_recovery(tmp_path, [empty(output=0), empty(output=0), empty(output=0)])
        assert out["run"]["state"] == "failed" and out["run"]["error_code"] == "model_empty_reply"
        assert out["model"].requests == 2 and len(out["calls"]) == 2

    asyncio.run(scenario())


def test_expensive_empty_reduces_budget_to_one_attempt(tmp_path):
    async def scenario():
        out = await run_recovery(tmp_path, [empty(cost=DEFAULT_COST_THRESHOLD_MICRO)])
        assert out["run"]["state"] == "failed" and out["run"]["error_code"] == "model_empty_reply"
        assert out["model"].requests == 1 and len(out["calls"]) == 1

    asyncio.run(scenario())


def test_unknown_cost_empty_is_never_replayed(tmp_path):
    async def scenario():
        out = await run_recovery(tmp_path, [ModelReply("", (), input_tokens=3, output_tokens=0, cost_micro=None)])
        assert out["run"]["state"] == "failed" and out["run"]["error_code"] == "model_empty_reply"
        assert out["model"].requests == 1
        assert len(out["calls"]) == 1 and out["calls"][0]["state"] == "unknown"

    asyncio.run(scenario())


def test_recovery_budget_exhausts_after_three_attempts(tmp_path):
    async def scenario():
        out = await run_recovery(tmp_path, [], always_empty=True)
        assert out["run"]["state"] == "failed" and out["run"]["error_code"] == "model_empty_reply"
        assert out["model"].requests == 3 and len(out["calls"]) == 3

    asyncio.run(scenario())


def test_default_off_fails_empty_immediately_without_retry(tmp_path):
    async def scenario():
        out = await run_recovery(tmp_path, [empty()], model_recovery=False)
        assert out["run"]["state"] == "failed" and out["run"]["error_code"] == "model_empty_reply"
        assert out["model"].requests == 1 and len(out["calls"]) == 1

    asyncio.run(scenario())


def test_non_empty_invalid_tool_calls_are_terminal_not_retried(tmp_path):
    async def scenario():
        bad = ModelReply("", (Invocation("one", "bad name", {}),), input_tokens=3, output_tokens=2, cost_micro=7)
        out = await run_recovery(tmp_path, [bad])
        assert out["run"]["state"] == "failed" and out["run"]["error_code"] == "invalid_model_tool_calls"
        assert out["model"].requests == 1 and len(out["calls"]) == 1

    asyncio.run(scenario())
