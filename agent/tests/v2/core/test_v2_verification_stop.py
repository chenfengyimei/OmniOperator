"""K11 verify-on-stop: applied writes without verification evidence earn a bounded follow-up.

The gate reads only persisted structured action receipts; the model's final answer text and
task-list checkboxes are never success evidence. Default OFF (opt-in), bounded nudges, candidate
answer kept as the fallback, and no write is ever replayed.
"""

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
from omni_test_support.v2.scheduling import interval as interval
from omni_test_support.v2.scheduling import setup as setup
from sqlalchemy import select

from omni.adapters.persistence import schema as s
from omni.domain.verification_stop import MAX_VERIFY_NUDGES, build_verify_nudge, unverified_writes

NUDGE_PREFIX = "[System: You applied changes this turn"
WRITE_SPEC = ToolSpec("write_probe", "isolated write", {"type": "object"}, writes=True)


def write(tool, verification=""):
    return {"tool": tool, "verification": verification}


def test_no_nudge_when_every_applied_write_is_verified():
    writes = [write("files_write", "readback ok"), write("browser_act", "https://example")]
    assert unverified_writes(writes) == []
    assert build_verify_nudge(writes, 0) is None


def test_nudge_lists_only_unverified_writes_in_order():
    writes = [write("files_write", "readback ok"), write("shell_run", ""), write("browser_act", "  ")]
    assert unverified_writes(writes) == ["shell_run", "browser_act"]
    nudge = build_verify_nudge(writes, 0)
    assert nudge is not None and nudge.startswith(NUDGE_PREFIX)
    assert "- shell_run" in nudge and "- browser_act" in nudge
    assert "files_write" not in nudge
    assert "task-list checkboxes are NOT success evidence" in nudge


def test_nudge_is_bounded_by_attempts_and_tool_count():
    assert build_verify_nudge([write("shell_run")], MAX_VERIFY_NUDGES) is None
    assert build_verify_nudge([write("shell_run")], MAX_VERIFY_NUDGES + 5) is None
    many = [write(f"tool_{i}") for i in range(12)]
    nudge = build_verify_nudge(many, 0)
    assert nudge is not None
    assert "- tool_7" in nudge and "- tool_8" not in nudge
    assert "... and 4 more" in nudge


def test_nudge_deduplicates_and_ignores_blank_tools():
    writes = [write("shell_run"), write("shell_run"), write(""), write("files_write", "ok")]
    assert unverified_writes(writes) == ["shell_run"]
    assert build_verify_nudge([write("")], 0) is None


class Writer:
    def __init__(self, verification=""):
        self.verification = verification
        self.calls = 0

    async def invoke(self, name, arguments, *, operation_id, context):
        self.calls += 1
        return ActionResult(Outcome.OK, "written", Effect.APPLIED, verification=self.verification)

    async def close(self, timeout_s):
        return True


class WriteThenStop:
    def __init__(self):
        self.snapshots = []

    async def complete(self, messages, tools, settings):
        self.snapshots.append([dict(m) for m in messages])
        if not any(m.get("role") == "tool" for m in messages):
            return ModelReply("", (Invocation(str(uuid.uuid4()), "write_probe", {}),), cost_micro=0)
        return ModelReply("done", cost_micro=0)

    def nudges(self):
        last = self.snapshots[-1]
        return sum(1 for m in last if m.get("role") == "user" and str(m.get("content", "")).startswith(NUDGE_PREFIX))


class AlwaysText:
    def __init__(self):
        self.calls = 0

    async def complete(self, messages, tools, settings):
        self.calls += 1
        return ModelReply("done", cost_micro=0)


async def write_stop(tmp_path, *, verification="", verify_on_stop=True):
    writer = Writer(verification)
    model = WriteThenStop()
    instance = await open_instance(
        tmp_path,
        model=model,
        capabilities=(Capability(WRITE_SPEC, writer),),
        verify_on_stop=verify_on_stop,
    )
    try:
        app = instance.application
        instance.maintenance.stop_accepting()
        agent = await configure(app, instance._database)
        receipt = await app.submit_turn(agent, None, "k", "go")
        run_id = receipt["run_id"]
        await wait_state(app, agent, run_id, {"waiting_approval"})
        approvals = await app.approvals(agent)
        await app.approve(agent, approvals[0]["id"], True, approvals[0]["policy_revision"])
        final = await wait_state(app, agent, run_id, {"succeeded"})
        evidence = await instance._database.read(lambda u: u.run_applied_writes(agent, run_id))
        answer = await instance._database.read(
            lambda u: (
                u.connection.execute(
                    select(s.messages.c.content).where(
                        s.messages.c.agent_id == agent, s.messages.c.run_id == run_id, s.messages.c.role == "assistant"
                    )
                )
                .scalars()
                .all()
            )
        )
        return {"state": final["state"], "model": model, "writer": writer, "evidence": evidence, "answer": answer}
    finally:
        await instance.close()


def test_unverified_write_earns_bounded_nudges_then_finishes_with_candidate(tmp_path):
    async def scenario():
        out = await write_stop(tmp_path, verification="", verify_on_stop=True)
        assert out["state"] == "succeeded"
        assert out["writer"].calls == 1
        assert out["model"].nudges() == MAX_VERIFY_NUDGES
        assert len(out["model"].snapshots) == MAX_VERIFY_NUDGES + 2
        assert out["evidence"] == [{"tool": "write_probe", "verification": ""}]
        assert out["answer"] == ["done"]

    asyncio.run(scenario())


def test_verified_write_finishes_without_a_nudge(tmp_path):
    async def scenario():
        out = await write_stop(tmp_path, verification="readback ok", verify_on_stop=True)
        assert out["state"] == "succeeded"
        assert out["model"].nudges() == 0
        assert len(out["model"].snapshots) == 2
        assert out["evidence"] == [{"tool": "write_probe", "verification": "readback ok"}]

    asyncio.run(scenario())


def test_gate_is_opt_in_and_default_off_finishes_immediately(tmp_path):
    async def scenario():
        out = await write_stop(tmp_path, verification="", verify_on_stop=False)
        assert out["state"] == "succeeded"
        assert out["model"].nudges() == 0
        assert len(out["model"].snapshots) == 2
        assert out["writer"].calls == 1

    asyncio.run(scenario())


def test_run_without_writes_finishes_without_a_nudge(tmp_path):
    async def scenario():
        model = AlwaysText()
        instance = await open_instance(tmp_path, model=model, capabilities=(), verify_on_stop=True)
        try:
            app = instance.application
            instance.maintenance.stop_accepting()
            agent = await configure(app, instance._database)
            receipt = await app.submit_turn(agent, None, "k", "go")
            final = await wait_state(app, agent, receipt["run_id"], {"succeeded"})
            assert final["state"] == "succeeded" and model.calls == 1
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_run_applied_writes_filters_effect_and_isolation(tmp_path):
    async def scenario():
        db, agent, clock, service = await setup(tmp_path)
        try:
            plan = await service.save(agent, "verify", True, interval())
            clock.advance(60)
            assert await service.tick() == 1
            run = (await service.occurrences(agent, plan["id"]))[0]["run_id"]

            def settle(u, tool, result, writes=True):
                policy = u.settings(agent)["policy_revision"]
                action = u.prepare_action(agent, run, ToolSpec(tool, "isolated", {}, writes=writes), {}, policy)
                if writes:
                    approval = next(r for r in u.pending_approvals(agent) if r["action_id"] == action["id"])
                    u.resolve_approval(agent, approval["id"], True, policy)
                u.authorize_action(agent, action["id"])
                u.settle_action(agent, action["id"], result)
                clock.advance(1)

            def seed(u):
                u.transition(agent, run, "running")
                settle(u, "write_blank", ActionResult(Outcome.OK, "written", Effect.APPLIED))
                settle(u, "write_verified", ActionResult(Outcome.OK, "written", Effect.APPLIED, verification="ok"))
                settle(u, "read_probe", ActionResult(Outcome.OK, "read", Effect.APPLIED), writes=False)
                settle(u, "write_failed", ActionResult(Outcome.ERROR, "boom", Effect.NONE))

            await db.write(seed)
            evidence = await db.read(lambda u: u.run_applied_writes(agent, run))
            assert {e["tool"]: e["verification"] for e in evidence} == {
                "write_blank": "",
                "write_verified": "ok",
            }
            other = await db.read(lambda u: u.run_applied_writes(agent, "00000000-0000-0000-0000-000000000000"))
            assert other == []
        finally:
            await db.close()

    asyncio.run(scenario())
