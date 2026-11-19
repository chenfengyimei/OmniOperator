"""K08 post-turn background review: pure domain rules and the production reviewer chain."""

from __future__ import annotations

import uuid

import pytest
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
    open_instance as open_instance,
)
from omni_test_support.v2.execution import (
    wait_state as wait_state,
)

from omni.domain.background_review import (
    DECISIONS,
    MAX_CALLS,
    PURPOSE,
    READ_TOOLS,
    REVIEW_PROMPT,
    WRITE_TOOLS,
    classify_tool,
    page,
    parse_decision,
    parse_proposal,
    parse_request,
    proposal_fingerprint,
    request_fingerprint,
    review_key,
)
from omni.domain.contracts import BusinessError

RUN = str(uuid.uuid4())
MESSAGE = str(uuid.uuid4())
SKILL = str(uuid.uuid4())
EPOCH = str(uuid.uuid4())


def test_review_key_is_stable_per_run() -> None:
    assert review_key(RUN) == review_key(RUN)
    assert review_key(RUN) != review_key(str(uuid.uuid4()))
    assert parse_request({"run_id": RUN}) == {"run_id": RUN, "key": review_key(RUN)}
    assert request_fingerprint({"run_id": RUN}) == request_fingerprint({"run_id": RUN})


def test_parse_request_rejects_unbounded_or_extra_fields() -> None:
    for bad in ({}, {"run_id": "nope"}, {"run_id": RUN, "extra": 1}, {"run_id": None}, []):
        with pytest.raises(BusinessError) as exc:
            parse_request(bad)
        assert exc.value.code == "background_review_invalid"


def test_classify_tool_whitelist() -> None:
    for name in READ_TOOLS:
        assert classify_tool(name) == "read"
    for name in WRITE_TOOLS:
        assert classify_tool(name) == "write"
    for name in ("shell", "browser_open", "request_mode", "delegate_task", "unknown"):
        assert classify_tool(name) == "deny"


def test_parse_memory_proposal_requires_evidence() -> None:
    proposal = parse_proposal(
        "memory_remember", {"content": "User prefers concise answers", "quote": "concise", "message_id": MESSAGE}
    )
    assert proposal["kind"] == "memory"
    assert proposal["content"] == "User prefers concise answers"
    assert proposal["quote"] == "concise"
    assert proposal["message_id"] == MESSAGE
    with pytest.raises(BusinessError):
        parse_proposal("memory_remember", {"content": "fact"})  # no quote
    with pytest.raises(BusinessError):
        parse_proposal("memory_remember", {"content": "x" * 20_001, "quote": "q"})


def test_parse_skill_proposals() -> None:
    created = parse_proposal("skill_create", {"content": "# Skill\nDo the thing."})
    assert created["kind"] == "skill" and created["skill_id"] is None
    patched = parse_proposal(
        "skill_patch", {"skill_id": SKILL, "section": "Steps", "text": "Grep first.", "revision": 2}
    )
    assert patched["skill_id"] == SKILL and patched["section"] == "Steps" and patched["revision"] == 2
    forgot = parse_proposal("memory_forget", {"memory_id": SKILL, "revision": 3})
    assert forgot["kind"] == "memory" and forgot["revision"] == 3
    with pytest.raises(BusinessError):
        parse_proposal("skill_patch", {"skill_id": SKILL, "section": "s"})  # no text
    with pytest.raises(BusinessError):
        parse_proposal("shell", {"content": "x"})


def test_proposal_fingerprint_binds_identity() -> None:
    proposal = parse_proposal("memory_remember", {"content": "fact", "quote": "q", "message_id": MESSAGE})
    base = proposal_fingerprint("batch", "agent", EPOCH, proposal)
    assert base == proposal_fingerprint("batch", "agent", EPOCH, proposal)
    assert base != proposal_fingerprint("batch", "agent", str(uuid.uuid4()), proposal)
    assert base != proposal_fingerprint("other", "agent", EPOCH, proposal)


def test_parse_decision_binds_revision_fingerprint_epoch() -> None:
    digest = "a" * 64
    decision = parse_decision({"revision": 1, "fingerprint": digest, "epoch": EPOCH, "decision": "apply"})
    assert decision["decision"] == "apply"
    assert DECISIONS == {"apply", "reject", "undo"}
    for bad in (
        {"revision": 1, "fingerprint": digest, "epoch": EPOCH, "decision": "delete"},
        {"revision": 0, "fingerprint": digest, "epoch": EPOCH, "decision": "apply"},
        {"revision": 1, "fingerprint": "short", "epoch": EPOCH, "decision": "apply"},
        {"revision": 1, "fingerprint": digest, "epoch": "nope", "decision": "apply"},
    ):
        with pytest.raises(BusinessError):
            parse_decision(bad)


def test_page_bounds_and_budget_constants() -> None:
    page(0, 50)
    for after, limit in ((-1, 10), (0, 0), (0, 51)):
        with pytest.raises(BusinessError):
            page(after, limit)
    assert PURPOSE == "review"
    assert 1 <= MAX_CALLS <= 16


def read_spec():
    return ToolSpec("big_read", "read", {"type": "object"}, False)


class Reader:
    async def invoke(self, name, arguments, *, operation_id, context):
        return ActionResult(Outcome.OK, "read result")

    async def close(self, timeout_s):
        return True


class ReviewModel:
    def __init__(self, proposals=(), review_text="Nothing to save."):
        self.turn = 0
        self.review = 0
        self.proposals = tuple(proposals)
        self.review_text = review_text

    async def complete(self, messages, tools, settings):
        if messages and messages[0].get("content") == REVIEW_PROMPT:
            self.review += 1
            if messages[-1].get("role") == "user" and self.proposals:
                return ModelReply("", self.proposals, cost_micro=5)
            return ModelReply(self.review_text, cost_micro=5)
        self.turn += 1
        return ModelReply("done", cost_micro=0)


def memory_proposal(call_id="c1"):
    return Invocation(call_id, "memory_remember", {"content": "User prefers concise answers", "quote": "concise"})


def decision_for(batch, candidate, kind):
    return {
        "revision": candidate["revision"],
        "fingerprint": candidate["fingerprint"],
        "epoch": batch["history_epoch"],
        "decision": kind,
    }


async def open_finished_turn(tmp_path, model):
    instance = await open_instance(tmp_path, model=model, capabilities=(Capability(read_spec(), Reader()),))
    instance.maintenance.stop_accepting()
    app = instance.application
    agent = await configure(app, instance._database)
    receipt = await app.submit_turn(agent, None, "k", "I prefer concise answers")
    await wait_state(app, agent, receipt["run_id"], {"succeeded"})
    return instance, agent, receipt["run_id"]


def test_review_stages_pending_proposal_with_anchor_and_bills(tmp_path):
    async def scenario():
        model = ReviewModel(proposals=(memory_proposal(),))
        instance, agent, run_id = await open_finished_turn(tmp_path, model)
        try:
            batch = await instance.background_review.submit(agent, {"run_id": run_id})
            assert batch["state"] == "queued"
            assert await instance.background_review.process_one() is True
            batch = await instance.background_review.get(agent, batch["id"])
            assert batch["state"] == "ready" and batch["candidate_count"] == 1
            assert batch["call_count"] >= 1 and batch["budget_micro"] > 0
            candidate = batch["candidates"][0]
            assert candidate["state"] == "pending" and candidate["kind"] == "memory"
            assert candidate["proposal"]["quote"] == "concise" and candidate["fingerprint"]
            assert await instance._database.read(lambda u: u.search_memory(agent, "concise")) == []
            calls = await instance.application.query("calls", agent)
            assert any(c["purpose"] == "summary" and c["cost_micro"] == 5 for c in calls)
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_owner_apply_uses_evidence_chain_and_undo_revokes(tmp_path):
    async def scenario():
        model = ReviewModel(proposals=(memory_proposal(),))
        instance, agent, run_id = await open_finished_turn(tmp_path, model)
        try:
            batch = await instance.background_review.submit(agent, {"run_id": run_id})
            await instance.background_review.process_one()
            batch = await instance.background_review.get(agent, batch["id"])
            candidate = batch["candidates"][0]
            applied = await instance.background_review.decide(agent, candidate["id"], decision_for(batch, candidate, "apply"))
            applied_candidate = applied["candidates"][0]
            assert applied_candidate["state"] == "applied" and applied_candidate["applied_memory_id"]
            memories = await instance._database.read(lambda u: u.search_memory(agent, "concise"))
            assert len(memories) == 1
            revoked = await instance.background_review.decide(
                agent, candidate["id"], decision_for(applied, applied_candidate, "undo")
            )
            assert revoked["candidates"][0]["state"] == "revoked"
            assert await instance._database.read(lambda u: u.search_memory(agent, "concise")) == []
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_owner_reject_writes_no_memory(tmp_path):
    async def scenario():
        model = ReviewModel(proposals=(memory_proposal(),))
        instance, agent, run_id = await open_finished_turn(tmp_path, model)
        try:
            batch = await instance.background_review.submit(agent, {"run_id": run_id})
            await instance.background_review.process_one()
            batch = await instance.background_review.get(agent, batch["id"])
            candidate = batch["candidates"][0]
            rejected = await instance.background_review.decide(agent, candidate["id"], decision_for(batch, candidate, "reject"))
            assert rejected["candidates"][0]["state"] == "rejected"
            assert await instance._database.read(lambda u: u.search_memory(agent, "concise")) == []
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_nothing_to_save_writes_no_candidates(tmp_path):
    async def scenario():
        model = ReviewModel()
        instance, agent, run_id = await open_finished_turn(tmp_path, model)
        try:
            batch = await instance.background_review.submit(agent, {"run_id": run_id})
            await instance.background_review.process_one()
            batch = await instance.background_review.get(agent, batch["id"])
            assert batch["state"] == "complete" and batch["candidate_count"] == 0
            assert batch["candidates"] == []
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_idempotent_trigger_does_not_repay(tmp_path):
    async def scenario():
        model = ReviewModel(proposals=(memory_proposal(),))
        instance, agent, run_id = await open_finished_turn(tmp_path, model)
        try:
            first = await instance.background_review.submit(agent, {"run_id": run_id})
            second = await instance.background_review.submit(agent, {"run_id": run_id})
            assert first["id"] == second["id"]
            await instance.background_review.process_one()
            assert await instance.background_review.process_one() is False
            review_count = model.review
            again = await instance.background_review.submit(agent, {"run_id": run_id})
            assert again["id"] == first["id"] and again["state"] == "ready"
            assert model.review == review_count
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_whitelist_denies_non_review_tools(tmp_path):
    async def scenario():
        proposals = (Invocation("c1", "shell", {"command": "rm -rf /"}), memory_proposal("c2"))
        model = ReviewModel(proposals=proposals)
        instance, agent, run_id = await open_finished_turn(tmp_path, model)
        try:
            batch = await instance.background_review.submit(agent, {"run_id": run_id})
            await instance.background_review.process_one()
            batch = await instance.background_review.get(agent, batch["id"])
            assert batch["candidate_count"] == 1
            assert batch["candidates"][0]["kind"] == "memory"
            assert batch["candidates"][0]["tool"] == "memory_remember"
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_cancel_pending_review_settles_without_model_call(tmp_path):
    async def scenario():
        model = ReviewModel(proposals=(memory_proposal(),))
        instance, agent, run_id = await open_finished_turn(tmp_path, model)
        try:
            batch = await instance.background_review.submit(agent, {"run_id": run_id})
            cancelled = await instance.background_review.cancel(agent, batch["id"], batch["revision"])
            assert cancelled["state"] == "cancelled"
            assert await instance.background_review.process_one() is False
            assert model.review == 0
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_engine_trigger_enqueues_review_after_finish(tmp_path):
    async def scenario():
        model = ReviewModel()
        instance = await open_instance(tmp_path, model=model)
        try:
            app = instance.application
            agent = await configure(app, instance._database)
            receipt = await app.submit_turn(agent, None, "k", "I prefer concise answers")
            await wait_state(app, agent, receipt["run_id"], {"succeeded"})
            for _ in range(100):
                page_result = await instance.background_review.page(agent, 0, 20)
                if page_result["items"]:
                    break
                await asyncio.sleep(0.05)
            items = (await instance.background_review.page(agent, 0, 20))["items"]
            assert any(item["run_id"] == receipt["run_id"] for item in items)
        finally:
            await instance.close()

    asyncio.run(scenario())