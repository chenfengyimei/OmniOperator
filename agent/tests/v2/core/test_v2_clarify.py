"""K10 structured clarification: pure domain rules and the production pause/respond/cancel/timeout chain."""

from __future__ import annotations

import pytest
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
from omni_test_support.v2.scheduling import Clock
from sqlalchemy import inspect, update

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.migration_config import head as migration_head
from omni.domain.clarify import (
    MAX_CHOICES,
    MAX_QUESTIONS,
    build_result,
    mark_recommended,
    normalize_questions,
    render_result,
    strip_recommended,
)
from omni.domain.contracts import BusinessError

QUESTIONS = [{"question": "Which approach?", "choices": ["Alpha", "Beta"]}]


def test_normalize_questions_marks_recommended_and_multi_select() -> None:
    normalized = normalize_questions(QUESTIONS)
    assert normalized[0]["qid"] == "q0"
    assert normalized[0]["choices"][0] == "Alpha (Recommended)"
    assert normalized[0]["choices_offered"] == ["Alpha", "Beta"]
    assert normalized[0]["multi_select"] is False
    multi = normalize_questions([{"question": "Pick", "choices": ["A", "B"], "multi_select": True}])
    assert multi[0]["multi_select"] is True
    open_ended = normalize_questions([{"question": "Why?"}])
    assert open_ended[0]["choices"] is None and open_ended[0]["multi_select"] is False


def test_normalize_rejects_invalid_questions() -> None:
    for bad in (
        [],
        "nope",
        [{"question": ""}],
        [{"question": "q", "choices": ["", "  "]}],
        [{"question": "q", "choices": [1]}],
        [{"question": "bad\x00"}],
        [{"question": "q", "choices": ["x" * 8001]}],
        [{"question": f"q{i}"} for i in range(MAX_QUESTIONS + 1)],
    ):
        with pytest.raises(BusinessError) as exc:
            normalize_questions(bad)
        assert exc.value.code == "invalid_clarify"


def test_choices_clamp_and_dedup_and_recommended_idempotent() -> None:
    normalized = normalize_questions([{"question": "q", "choices": ["A", "A", "B", "C", "D", "E"]}])
    assert len(normalized[0]["choices_offered"]) == MAX_CHOICES
    assert mark_recommended(["A (Recommended)", "B"]) == ["A (Recommended)", "B"]
    assert mark_recommended(["Solo"]) == ["Solo"]
    assert strip_recommended("Alpha (Recommended)") == "Alpha"
    assert strip_recommended("Alpha") == "Alpha"


def test_build_result_statuses_and_multi_select() -> None:
    normalized = normalize_questions(
        [
            {"question": "One?", "choices": ["A", "B"]},
            {"question": "Many?", "choices": ["X", "Y"], "multi_select": True},
            {"question": "Open?"},
        ]
    )
    result = build_result(normalized, {"q0": "A (Recommended)", "q1": '["X", "Y"]'}, "submitted")
    responses = result["responses"]
    assert responses[0]["status"] == "answered" and responses[0]["user_response"] == "A"
    assert responses[1]["status"] == "answered" and responses[1]["user_response"] == ["X", "Y"]
    assert responses[2]["status"] == "unanswered" and responses[2]["user_response"] is None
    skipped = build_result(normalized, {"q2": ""}, "cancelled", "owner left")
    assert skipped["responses"][2]["status"] == "skipped" and skipped["notice"] == "owner left"
    with pytest.raises(BusinessError):
        build_result(normalized, {}, "bogus")


def test_render_result_text() -> None:
    normalized = normalize_questions(QUESTIONS)
    result = build_result(normalized, {"q0": "Alpha"}, "submitted")
    text = render_result(result)
    assert "Clarify submitted." in text and "Which approach?" in text and "Alpha" in text
    assert "timed_out" in render_result(build_result(normalized, {}, "timed_out", "too slow"))


class ClarifyModel:
    def __init__(self, questions=None):
        self.questions = questions if questions is not None else QUESTIONS
        self.tool_texts: list[str] = []

    async def complete(self, messages, tools, settings):
        for message in messages:
            if message.get("role") == "tool":
                self.tool_texts.append(str(message.get("content")))
        if any(m.get("role") == "tool" for m in messages):
            return ModelReply("done", cost_micro=0)
        return ModelReply(
            "",
            (Invocation("c1", "clarify", {"questions": self.questions}),),
            cost_micro=0,
            completion_reason="tool_calls",
        )


async def paused_run(tmp_path, model=None, clock=None):
    instance = await open_instance(tmp_path, model=model or ClarifyModel(), clock=clock)
    instance.maintenance.stop_accepting()
    app = instance.application
    agent = await configure(app, instance._database)
    receipt = await app.submit_turn(agent, None, "k", "help me choose an approach")
    run_id = receipt["run_id"]
    await wait_state(app, agent, run_id, {"waiting_approval"})
    return instance, app, agent, run_id


async def first_clarification(app, agent):
    pending = await app.clarifications(agent)
    assert len(pending) == 1 and pending[0]["state"] == "pending"
    return pending[0]


def test_clarify_pauses_and_owner_response_resumes_same_run(tmp_path):
    async def scenario():
        model = ClarifyModel()
        instance, app, agent, run_id = await paused_run(tmp_path, model)
        try:
            request = await first_clarification(app, agent)
            run = await app.get_run(agent, run_id)
            resolved = await app.respond_clarify(
                agent, request["id"], {"q0": "Alpha"}, request["revision"], int(run["policy_epoch"])
            )
            assert resolved["state"] == "answered" and resolved["outcome"] == "submitted"
            final = await wait_state(app, agent, run_id, {"succeeded"})
            assert final["id"] == run_id
            stored = await instance._database.read(lambda u: u.clarification(agent, request["id"]))
            assert stored["answers"] == {"q0": "Alpha"}
            assert any("Alpha" in text and "submitted" in text for text in model.tool_texts)
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_owner_cancel_resumes_run_with_cancelled_outcome(tmp_path):
    async def scenario():
        model = ClarifyModel()
        instance, app, agent, run_id = await paused_run(tmp_path, model)
        try:
            request = await first_clarification(app, agent)
            resolved = await app.cancel_clarify(agent, request["id"], request["revision"], "changed my mind")
            assert resolved["state"] == "cancelled" and resolved["outcome"] == "cancelled"
            await wait_state(app, agent, run_id, {"succeeded"})
            assert any("cancelled" in text for text in model.tool_texts)
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_run_cancel_cleans_pending_clarification(tmp_path):
    async def scenario():
        instance, app, agent, run_id = await paused_run(tmp_path)
        try:
            request = await first_clarification(app, agent)
            await app.cancel(agent, run_id)
            await wait_state(app, agent, run_id, {"cancelled", "interrupted"})
            stored = await instance._database.read(lambda u: u.clarification(agent, request["id"]))
            assert stored["state"] == "cancelled" and stored["outcome"] == "cancelled"
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_clarify_timeout_resumes_run_with_timed_out(tmp_path):
    async def scenario():
        clock = Clock()
        model = ClarifyModel()
        instance, app, agent, run_id = await paused_run(tmp_path, model, clock=clock)
        try:
            request = await first_clarification(app, agent)
            for _ in range(160):
                clock.advance(3)
                await asyncio.sleep(0.05)
                run = await app.get_run(agent, run_id)
                if run["state"] in {"succeeded", "failed", "interrupted"}:
                    break
            await wait_state(app, agent, run_id, {"succeeded"})
            stored = await instance._database.read(lambda u: u.clarification(agent, request["id"]))
            assert stored["state"] == "timed_out" and stored["outcome"] == "timed_out"
            assert any("timed_out" in text for text in model.tool_texts)
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_respond_rejects_stale_revision_and_policy(tmp_path):
    async def scenario():
        instance, app, agent, run_id = await paused_run(tmp_path)
        try:
            request = await first_clarification(app, agent)
            run = await app.get_run(agent, run_id)
            policy = int(run["policy_epoch"])
            with pytest.raises(BusinessError) as rev:
                await app.respond_clarify(agent, request["id"], {"q0": "Alpha"}, request["revision"] + 5, policy)
            assert rev.value.code == "revision_conflict"
            with pytest.raises(BusinessError) as pol:
                await app.respond_clarify(agent, request["id"], {"q0": "Alpha"}, request["revision"], policy + 5)
            assert pol.value.code == "policy_changed"
            await app.cancel_clarify(agent, request["id"], request["revision"])
            await wait_state(app, agent, run_id, {"succeeded"})
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_invalid_clarify_arguments_settle_error_without_request(tmp_path):
    async def scenario():
        instance = await open_instance(tmp_path, model=ClarifyModel(questions=[]))
        instance.maintenance.stop_accepting()
        app = instance.application
        agent = await configure(app, instance._database)
        try:
            receipt = await app.submit_turn(agent, None, "k", "help me choose")
            await wait_state(app, agent, receipt["run_id"], {"succeeded"})
            assert await app.clarifications(agent) == []
        finally:
            await instance.close()

    asyncio.run(scenario())


NEW_TABLES = {"clarifications"}
NEW_TRIGGERS = {"clarification_identity", "clarification_keep"}


def test_v44_head_declares_clarifications_with_matching_columns_and_triggers(tmp_path):
    async def scenario():
        instance = await open_instance(tmp_path, model=ClarifyModel(), capabilities=())
        try:
            database = instance._database
            assert database.schema_version == migration_head()

            def compare(u):
                reflection = inspect(u.connection)
                assert NEW_TABLES <= set(reflection.get_table_names())
                for name in NEW_TABLES:
                    table = s.metadata.tables[name]
                    assert {c["name"] for c in reflection.get_columns(name)} == set(table.c.keys())
                    declared = {tuple(c.column_keys) for c in table.foreign_key_constraints}
                    actual = {tuple(c["constrained_columns"]) for c in reflection.get_foreign_keys(name)}
                    assert declared == actual, name
                triggers = {
                    row[0]
                    for row in u.connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='trigger'")
                }
                assert NEW_TRIGGERS <= triggers

            await database.read(compare)
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_clarification_evidence_is_immutable_and_one_way(tmp_path):
    async def scenario():
        instance, app, agent, run_id = await paused_run(tmp_path)
        try:
            database = instance._database
            request = await first_clarification(app, agent)

            async def denied(statement):
                try:
                    await database.write(lambda u: u.connection.execute(statement))
                except BusinessError as exc:
                    assert exc.code == "data_constraint"
                    return
                raise AssertionError("immutable clarification accepted a forbidden write")

            await denied(
                update(s.clarifications)
                .where(s.clarifications.c.id == request["id"])
                .values(run_id="00000000-0000-0000-0000-000000000000")
            )
            await denied(s.clarifications.delete().where(s.clarifications.c.id == request["id"]))
            run = await app.get_run(agent, run_id)
            await app.respond_clarify(
                agent, request["id"], {"q0": "Alpha"}, request["revision"], int(run["policy_epoch"])
            )
            await denied(update(s.clarifications).where(s.clarifications.c.id == request["id"]).values(state="pending"))
            report = await database.integrity()
            assert report["integrity"] == "ok" and report["foreign_keys"] == 0
            await wait_state(app, agent, run_id, {"succeeded"})
        finally:
            await instance.close()

    asyncio.run(scenario())
