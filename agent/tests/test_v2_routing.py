"""Tier/profile snapshots, actual execution guards and shared costs using SQLite and isolated models."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import delete, update
from test_v2_execution import wait_state
from test_v2_images import png
from v2_configuration_helpers import model_instance

from omni.adapters.persistence import schema as s
from omni.application.engine import Capability
from omni.composition.instance import open_instance
from omni.domain.contracts import ActionResult, BusinessError, Effect, Invocation, ModelReply, Outcome, ToolSpec
from omni.domain.routing import POLICY_DEFAULT, choose, select_tools


class Model:
    def __init__(self, replies=()):
        self.requests, self.replies = [], list(replies)

    async def complete(self, messages, tools, settings):
        self.requests.append({"messages": messages, "tools": [t.name for t in tools], "settings": dict(settings)})
        if self.replies:
            reply = self.replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply
        return ModelReply("completed", cost_micro=7)


class Tool:
    def __init__(self):
        self.calls = 0

    async def invoke(self, name, arguments, **kwargs):
        self.calls += 1
        return ActionResult(Outcome.OK, "verified", Effect.APPLIED if name == "files_write" else Effect.NONE)


async def configured(instance):
    agent = (await instance.application.query("agents", ""))[0]["id"]
    await model_instance(instance, agent, model="default")
    small = await instance.configurations.create(agent, "model", "Small", True, {"model": "small"})
    large = await instance.configurations.create(agent, "model", "Large", True, {"model": "large"})
    policy = {
        **POLICY_DEFAULT,
        "chat_model_id": small["id"],
        "agent_model_id": large["id"],
        "chat_reservation_micro": 123,
    }
    await instance.routing.save(agent, policy, 0)
    return agent, small, large


@pytest.mark.parametrize(
    "text,expected",
    [
        ("你好", "chat"),
        ("hello!", "chat"),
        ("查询资料", "retrieval"),
        ("查询后发送邮件", "agent"),
        ("continue", "agent"),
    ],
)
def test_auto_is_deterministic_and_conservative_without_paid_classifier(text, expected):
    assert choose(text, "auto", "auto", images=False, remote=False)["mode"] == expected
    assert choose(text, "auto", "auto", images=True, remote=False)["mode"] == "agent"
    assert choose(text, "agent", "agent", images=False, remote=True)["mode"] == "chat"


def test_tiers_intersect_only_known_reads_and_do_not_offer_useless_upgrade():
    specs = (
        ToolSpec("memory_search", "memory", {}),
        ToolSpec("files_write", "write", {}, True),
        ToolSpec("external_read", "plugin", {}),
    )
    assert [t.name for t in select_tools("chat", specs, escalate=True)] == ["memory_search", "request_mode"]
    assert [t.name for t in select_tools("retrieval", specs, escalate=False)] == ["memory_search"]
    assert not select_tools("chat", (), escalate=True)


def test_profile_reservation_tool_snapshot_and_unknown_cost_are_one_run_truth(tmp_path):
    async def scenario():
        model = Model([ModelReply("answer", cost_micro=None)])
        tool = Tool()
        caps = (
            Capability(ToolSpec("memory_search", "memory", {}), tool),
            Capability(ToolSpec("files_write", "write" * 500, {}, True), tool),
        )
        instance = await open_instance(tmp_path, model=model, capabilities=caps)
        try:
            agent, small, _ = await configured(instance)
            receipt = await instance.application.submit_turn(agent, None, "light", "你好", mode="auto")
            await wait_state(instance.application, agent, receipt["run_id"], {"succeeded"})
            report = await instance.routing.report(agent, receipt["run_id"])
            assert report["route"]["mode"] == "chat" and report["route"]["model_revision_id"] == small["revision_id"]
            assert report["route"]["reservation_micro"] == 123
            assert report["calls"][0]["ordinal"] == 1 and report["calls"][0]["tool_count"] == 2
            assert [r["name"] for r in report["calls"][0]["tools"]] == ["memory_search", "request_mode"]
            assert report["byte_delta"] > 0 and report["routing_model_calls"] == 0
            assert report["known_cost_micro"] == 0 and report["unsettled_calls"] == 1
            assert report["calls"][0]["call_state"] == "unknown"
            assert len(model.requests) == 1 and model.requests[0]["settings"]["model"] == "small"
            assert model.requests[0]["settings"]["call_reservation_micro"] == 123 and not tool.calls
            with pytest.raises(BusinessError):
                await instance._database.write(lambda u: u.connection.execute(delete(s.model_call_routes)))
            with pytest.raises(BusinessError):
                await instance._database.write(
                    lambda u: u.connection.execute(update(s.run_routes).values(initial_mode="agent"))
                )
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_upgrade_only_changes_next_call_and_write_still_requires_approval(tmp_path):
    async def scenario():
        model = Model(
            [
                ModelReply(
                    "more tools",
                    (Invocation("tier", "request_mode", {"mode": "agent"}), Invocation("early", "files_write", {})),
                    cost_micro=7,
                ),
                ModelReply("write", (Invocation("real-write", "files_write", {}),), cost_micro=7),
            ]
        )
        tool = Tool()
        caps = (Capability(ToolSpec("files_write", "write", {"type": "object"}, True), tool),)
        instance = await open_instance(tmp_path, model=model, capabilities=caps)
        try:
            agent, small, _ = await configured(instance)
            receipt = await instance.application.submit_turn(agent, None, "promote", "task", mode="chat")
            await wait_state(instance.application, agent, receipt["run_id"], {"waiting_approval"})
            report = await instance.routing.report(agent, receipt["run_id"])
            assert report["route"]["promoted"] and report["route"]["mode"] == "agent"
            assert report["route"]["change"]["source_snapshot_id"] == report["calls"][0]["id"]
            assert [r["mode"] for r in report["calls"]] == ["chat", "agent"]
            assert model.requests[0]["tools"] == ["request_mode"] and model.requests[1]["tools"] == ["files_write"]
            assert all(r["settings"]["model"] == "small" for r in model.requests) and tool.calls == 0
            assert all(r["settings"]["model_profile_id"] == small["id"] for r in model.requests)
            assert "outside this call's tier" in str(model.requests[1]["messages"])
            approvals = await instance.application.approvals(agent)
            assert len(approvals) == 1
            await instance.application.approve(agent, approvals[0]["id"], True, approvals[0]["policy_revision"])
            await wait_state(instance.application, agent, receipt["run_id"], {"succeeded"})
            final = await instance.routing.report(agent, receipt["run_id"])
            assert tool.calls == 1 and final["known_cost_micro"] == 21
            assert final["route"]["model_revision_id"] == small["revision_id"]
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_second_or_unoffered_tier_request_cannot_create_new_permission(tmp_path):
    async def scenario():
        model = Model(
            [
                ModelReply("first", (Invocation("one", "request_mode", {"mode": "retrieval"}),), cost_micro=7),
                ModelReply(
                    "again",
                    (Invocation("two", "request_mode", {"mode": "agent"}), Invocation("bad", "files_write", {})),
                    cost_micro=7,
                ),
            ]
        )
        tool = Tool()
        instance = await open_instance(
            tmp_path, model=model, capabilities=(Capability(ToolSpec("files_write", "write", {}, True), tool),)
        )
        try:
            agent, _, _ = await configured(instance)
            receipt = await instance.application.submit_turn(agent, None, "twice", "task", mode="chat")
            await wait_state(instance.application, agent, receipt["run_id"], {"succeeded"})
            report = await instance.routing.report(agent, receipt["run_id"])
            assert report["route"]["mode"] == "retrieval" and not tool.calls
            assert not await instance.application.approvals(agent)
            assert "mode_escalation_exhausted" in str(model.requests[-1]["messages"])
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_actual_tier_model_controls_image_admission_and_no_text_fallback(tmp_path):
    async def scenario():
        model = Model()
        instance = await open_instance(tmp_path, model=model, capabilities=())
        try:
            agent, small, _ = await configured(instance)
            image = await instance.images.upload(agent, png())
            with pytest.raises(BusinessError) as unsupported:
                await instance.application.submit_turn(agent, None, "wrong", "", (image["object_id"],), mode="chat")
            assert unsupported.value.code == "image_capability_disabled" and not await instance.application.query(
                "sessions", agent
            )
            vision = await instance.configurations.create(
                agent,
                "model",
                "Vision",
                True,
                {"model": "gpt-4o"},
                {"protocol": "openai_low", "per_call_limit": 1, "run_budget": 2},
            )
            current = await instance.routing.get(agent)
            await instance.routing.save(
                agent, {**{k: current[k] for k in POLICY_DEFAULT}, "agent_model_id": vision["id"]}, current["revision"]
            )
            receipt = await instance.application.submit_turn(
                agent, None, "vision", "", (image["object_id"],), mode="auto"
            )
            await wait_state(instance.application, agent, receipt["run_id"], {"succeeded"})
            report = await instance.routing.report(agent, receipt["run_id"])
            assert report["route"]["mode"] == "agent" and report["route"]["reason"] == "image_input"
            assert report["route"]["model_revision_id"] == vision["revision_id"] != small["revision_id"]
            assert model.requests[0]["settings"]["model"] == "gpt-4o" and "image_url" in str(
                model.requests[0]["messages"]
            )
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_policy_cas_scope_snapshot_and_disabled_models_fail_without_fallback(tmp_path):
    async def scenario():
        model = Model()
        instance = await open_instance(tmp_path, model=model, capabilities=())
        try:
            agent, small, _ = await configured(instance)
            current = await instance.routing.get(agent)
            with pytest.raises(BusinessError) as conflict:
                await instance.routing.save(agent, {k: current[k] for k in POLICY_DEFAULT}, 0)
            assert conflict.value.code == "revision_conflict"
            other = (await instance._database.write(lambda u: u.create_agent("Other")))["id"]
            with pytest.raises(BusinessError):
                await instance.routing.save(other, {**POLICY_DEFAULT, "chat_model_id": small["id"]}, 0)
            await instance.configurations.save(
                agent, small["id"], small["name"], False, small["value"], small["revision"]
            )
            with pytest.raises(BusinessError):
                await instance.application.submit_turn(agent, None, "disabled", "hello", mode="chat")
            assert not model.requests and not await instance.application.query("sessions", agent)
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_policy_change_does_not_replace_model_or_budget_in_an_admitted_run(tmp_path):
    class Waiting(Model):
        def __init__(self):
            super().__init__()
            self.entered, self.release = asyncio.Event(), asyncio.Event()

        async def complete(self, *args):
            reply = await super().complete(*args)
            self.entered.set()
            await self.release.wait()
            return reply

    async def scenario():
        model = Waiting()
        instance = await open_instance(tmp_path, model=model, capabilities=())
        try:
            agent, small, large = await configured(instance)
            first = await instance.application.submit_turn(agent, None, "old", "hello", mode="chat")
            await asyncio.wait_for(model.entered.wait(), 5)
            policy = await instance.routing.get(agent)
            await instance.routing.save(
                agent,
                {**{k: policy[k] for k in POLICY_DEFAULT}, "chat_model_id": large["id"], "chat_reservation_micro": 999},
                policy["revision"],
            )
            model.release.set()
            await wait_state(instance.application, agent, first["run_id"], {"succeeded"})
            route = (await instance.routing.report(agent, first["run_id"]))["route"]
            assert route["model_revision_id"] == small["revision_id"] and route["reservation_micro"] == 123
            second = await instance.application.submit_turn(agent, None, "new", "hello", mode="chat")
            await wait_state(instance.application, agent, second["run_id"], {"succeeded"})
            assert (
                model.requests[-1]["settings"]["model"] == "large"
                and model.requests[-1]["settings"]["call_reservation_micro"] == 999
            )
            with pytest.raises(BusinessError) as conflict:
                await instance.application.submit_turn(agent, None, "old", "hello", mode="agent")
            assert conflict.value.code == "idempotency_conflict"
            with pytest.raises(BusinessError):
                await instance._database.write(
                    lambda u: u.connection.execute(
                        update(s.runs)
                        .where(s.runs.c.id == first["run_id"])
                        .values(model_revision_id=large["revision_id"])
                    )
                )
        finally:
            model.release.set()
            await instance.close()

    asyncio.run(scenario())


def test_reservation_rejection_never_routes_to_another_model_or_calls_provider(tmp_path):
    async def scenario():
        model = Model()
        instance = await open_instance(tmp_path, model=model, capabilities=())
        try:
            agent, small, _ = await configured(instance)
            settings = await instance.application.settings(agent)
            await instance.application.save_settings(
                agent, {**settings["value"], "daily_budget_micro": 100}, settings["revision"]
            )
            receipt = await instance.application.submit_turn(agent, None, "over", "hello", mode="chat")
            run = await wait_state(instance.application, agent, receipt["run_id"], {"failed"})
            assert run["error_code"] == "budget_exceeded" and not model.requests
            report = await instance.routing.report(agent, receipt["run_id"])
            assert report["route"]["model_revision_id"] == small["revision_id"] and not report["calls"]
        finally:
            await instance.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["credential", "model", "cancel"])
def test_prepared_stream_revocation_or_cancellation_keeps_the_route_and_releases_only_unsent_cost(tmp_path, change):
    from test_v2_credentials import Backend
    from test_v2_model_stream import StreamingModel

    class Preparing(StreamingModel):
        def __init__(self):
            super().__init__()
            self.entered_prepare, self.allow = asyncio.Event(), asyncio.Event()

        async def prepare_stream(self, *args):
            result = await super().prepare_stream(*args)
            self.entered_prepare.set()
            try:
                await self.allow.wait()
            except BaseException:
                await result.close()
                raise
            return result

    async def scenario():
        model = Preparing()
        instance = await open_instance(
            tmp_path, model=model, model_streamer=model, capabilities=(), credential_backend=Backend()
        )
        try:
            agent, small, _ = await configured(instance)
            await instance.credentials.change("fixture/route-key", "old-key")
            small = await instance.configurations.save(
                agent,
                small["id"],
                small["name"],
                True,
                {**small["value"], "credential_ref": "fixture/route-key"},
                small["revision"],
            )
            receipt = await instance.application.submit_turn(agent, None, "blocked", "hello", mode="chat")
            await asyncio.wait_for(model.entered_prepare.wait(), 5)
            if change == "credential":
                await instance.credentials.change("fixture/route-key", "new-key")
            elif change == "model":
                await instance.configurations.save(
                    agent, small["id"], small["name"], False, small["value"], small["revision"]
                )
            else:
                await instance.application.cancel(agent, receipt["run_id"])
            model.allow.set()
            await wait_state(instance.application, agent, receipt["run_id"], {"failed", "cancelled"})
            report = await instance.routing.report(agent, receipt["run_id"])
            assert report["route"]["model_revision_id"] == small["revision_id"]
            assert len(report["calls"]) == 1 and report["calls"][0]["call_state"] == "not_sent"
            assert model.send_count == 0 and not model.prepared and report["unsettled_calls"] == 0
        finally:
            model.allow.set()
            assert (await instance.close())["complete"]

    asyncio.run(scenario())
