"""Production K14 visual path: authorized image object -> capability -> ModelCalls -> text.

The model is an injected routing fake so the default suite stays offline; it observes whether the
visual request contains an image part and never performs network I/O. A separate acceptance script
uses the same composition with the real vision model.
"""

from __future__ import annotations

import asyncio
import io

from omni_test_support.v2.android import SERIAL, fake_server
from omni_test_support.v2.v2_configuration_helpers import model_uow
from PIL import Image
from sqlalchemy import delete, select

from omni.adapters.persistence import schema as s
from omni.application.visual_subagent import VISUAL_SUBAGENT
from omni.composition.instance import open_instance
from omni.domain.contracts import Invocation, ModelReply


class RoutingModel:
    def __init__(self) -> None:
        self.object_id = ""
        self.visual_calls = 0
        self.master_calls = 0
        self.fail_visual = False

    async def complete(self, messages, tools, settings):
        visual = not tools and any(
            isinstance(message.get("content"), list)
            and any(
                isinstance(part, dict) and part.get("type") in {"image_url", "image", "input_image"}
                for part in message["content"]
            )
            for message in messages
        )
        if visual:
            self.visual_calls += 1
            if self.fail_visual:
                raise RuntimeError("provider down")
            return ModelReply("The screen is a solid green field.", cost_micro=7, input_tokens=100, output_tokens=12)
        self.master_calls += 1
        if any(message.get("role") == "tool" for message in messages):
            return ModelReply("done", cost_micro=1)
        return ModelReply(
            "",
            (
                Invocation(
                    "visual-1",
                    VISUAL_SUBAGENT.name,
                    {"task": "Describe the screenshot", "frame_object_id": self.object_id, "focus": "ui"},
                ),
            ),
            cost_micro=1,
            completion_reason="tool_calls",
        )


class RepeatVisualModel(RoutingModel):
    async def complete(self, messages, tools, settings):
        visual = not tools and any(
            isinstance(message.get("content"), list)
            and any(
                isinstance(part, dict) and part.get("type") in {"image_url", "image", "input_image"}
                for part in message["content"]
            )
            for message in messages
        )
        if visual:
            return await super().complete(messages, tools, settings)
        self.master_calls += 1
        if any(message.get("role") == "tool" for message in messages) and self.master_calls >= 3:
            return ModelReply("done", cost_micro=1)
        return ModelReply(
            "",
            (Invocation("visual", VISUAL_SUBAGENT.name, {"task": "Describe", "frame_object_id": self.object_id}),),
            cost_micro=1,
            completion_reason="tool_calls",
        )


def _png() -> bytes:
    with io.BytesIO() as output:
        Image.new("RGB", (64, 48), "green").save(output, format="PNG")
        return output.getvalue()


async def _setup(tmp_path, model, *, image_policy: bool = True, daily_budget: int = 1_000_000):
    instance = await open_instance(tmp_path, model=model)
    instance.maintenance.stop_accepting()
    app = instance.application
    agent = (await app.query("agents", ""))[0]["id"]
    await instance._database.write(lambda u: model_uow(u, agent, model="openai/gpt-4o"))
    settings = await app.settings(agent)
    value = dict(settings["value"])
    value["daily_budget_micro"] = daily_budget
    value["call_reservation_micro"] = 10_000
    await app.save_settings(agent, value, settings["revision"])
    config = await instance.configurations.get(agent, str(value["model_profile_id"]))
    await instance.configurations.save(
        agent,
        config["id"],
        config["name"],
        True,
        config["value"],
        config["revision"],
        image_policy={"protocol": "openai_low", "per_call_limit": 4, "run_budget": 12} if image_policy else None,
    )
    return instance, app, agent


async def _run_visual(app, agent, key: str = "visual"):
    receipt = await app.submit_turn(agent, None, key, "Please look at the screenshot")
    app.notify()
    async with asyncio.timeout(30):
        while True:
            run = await app.get_run(agent, receipt["run_id"])
            if run["state"] in {"succeeded", "failed"}:
                return receipt, run
            await asyncio.sleep(0.01)


async def _visual_action(instance, agent):
    rows = await instance._database.read(
        lambda u: (
            u.connection.execute(select(s.actions).where(s.actions.c.tool == VISUAL_SUBAGENT.name)).mappings().all()
        )
    )
    assert len(rows) == 1, rows
    return rows[0]


def test_authorized_image_reaches_one_visual_call_and_returns_traceable_text(tmp_path):
    async def scenario():
        model = RoutingModel()
        instance, app, agent = await _setup(tmp_path, model)
        try:
            uploaded = await instance.images.upload(agent, _png())
            model.object_id = str(uploaded["object_id"])
            _receipt, run = await _run_visual(app, agent)
            assert run["state"] == "succeeded" and model.visual_calls == 1
            action = await _visual_action(instance, agent)
            assert action["state"] == "ok" and action["result"]["verification"] == "visual_summary"
            assert action["result"]["data"]["frames"] == 1
            assert action["result"]["data"]["model"] == "openai/gpt-4o"
            assert action["result"]["data"]["cost_micro"] == 7
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_android_capture_object_reaches_the_same_visual_path(tmp_path):
    async def scenario():
        async with fake_server() as (_fake, port):
            model = RoutingModel()
            instance = await open_instance(tmp_path / "home", model=model)
            instance.maintenance.stop_accepting()
            try:
                app = instance.application
                agent = (await app.query("agents", ""))[0]["id"]
                await instance._database.write(lambda u: model_uow(u, agent, model="openai/gpt-4o"))
                settings = await app.settings(agent)
                value = dict(settings["value"])
                value["daily_budget_micro"] = 1_000_000
                value["call_reservation_micro"] = 10_000
                await app.save_settings(agent, value, settings["revision"])
                config = await instance.configurations.get(agent, str(value["model_profile_id"]))
                await instance.configurations.save(
                    agent,
                    config["id"],
                    config["name"],
                    True,
                    config["value"],
                    config["revision"],
                    image_policy={"protocol": "openai_low", "per_call_limit": 4, "run_budget": 12},
                )

                def setup(u):
                    channel = u.configuration_create(
                        agent,
                        "channel",
                        "Isolated Android",
                        True,
                        {"driver": "android", "endpoint": f"http://127.0.0.1:{port}", "account": SERIAL},
                    )
                    session = u._record(s.sessions, {"title": "Android fixture", "revision": 1, "next_seq": 0}, agent)
                    return channel, session["id"]

                channel, session = await instance._database.write(setup)
                probe = await instance.android.probe(agent, channel["id"], True)
                grant = await instance.android.grant(agent, probe["probe_id"], True)
                capture = await instance.android.capture(agent, grant["id"], session)
                model.object_id = str(capture["object_id"])
                _receipt, run = await _run_visual(app, agent)
                assert run["state"] == "succeeded" and model.visual_calls == 1
                action = await _visual_action(instance, agent)
                assert action["state"] == "ok" and action["result"]["data"]["frames"] == 1
            finally:
                await instance.close()

    asyncio.run(scenario())


def test_disabled_image_policy_fails_closed_without_a_visual_call(tmp_path):
    async def scenario():
        model = RoutingModel()
        instance, app, agent = await _setup(tmp_path, model, image_policy=False)
        try:
            uploaded = await instance.images.upload(agent, _png())
            model.object_id = str(uploaded["object_id"])
            _receipt, run = await _run_visual(app, agent)
            assert run["state"] == "succeeded" and model.visual_calls == 0
            action = await _visual_action(instance, agent)
            assert action["state"] == "error" and action["result"]["error_code"] == "visual_unavailable"
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_run_budget_exhaustion_blocks_the_visual_call_without_dispatch(tmp_path):
    async def scenario():
        model = RoutingModel()
        instance, app, agent = await _setup(tmp_path, model, daily_budget=10_000)
        try:
            uploaded = await instance.images.upload(agent, _png())
            model.object_id = str(uploaded["object_id"])
            _receipt, run = await _run_visual(app, agent)
            assert run["state"] in {"succeeded", "failed"} and model.visual_calls == 0
            action = await _visual_action(instance, agent)
            assert action["state"] == "error" and action["result"]["error_code"] == "budget_exceeded"
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_provider_failure_is_explicit_and_not_replayed(tmp_path):
    async def scenario():
        model = RoutingModel()
        model.fail_visual = True
        instance, app, agent = await _setup(tmp_path, model)
        try:
            uploaded = await instance.images.upload(agent, _png())
            model.object_id = str(uploaded["object_id"])
            _receipt, run = await _run_visual(app, agent)
            assert run["state"] == "succeeded" and model.visual_calls == 1
            action = await _visual_action(instance, agent)
            assert action["state"] == "error" and action["result"]["error_code"] == "visual_unavailable"
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_cross_agent_image_is_rejected_at_admission(tmp_path):
    async def scenario():
        model = RoutingModel()
        instance, app, agent = await _setup(tmp_path, model)
        try:
            other = await app.create_agent("Other")
            uploaded = await instance.images.upload(other["id"], _png())
            model.object_id = str(uploaded["object_id"])
            _receipt, run = await _run_visual(app, agent)
            assert run["state"] == "succeeded" and model.visual_calls == 0
            action = await _visual_action(instance, agent)
            assert action["state"] == "error" and action["result"]["error_code"] == "image_source_changed"
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_erased_image_source_fails_before_dispatch(tmp_path):
    async def scenario():
        model = RoutingModel()
        instance, app, agent = await _setup(tmp_path, model)
        try:
            uploaded = await instance.images.upload(agent, _png())
            model.object_id = str(uploaded["object_id"])
            await instance._database.write(
                lambda u: u.connection.execute(delete(s.objects).where(s.objects.c.id == model.object_id))
            )
            _receipt, run = await _run_visual(app, agent)
            assert run["state"] == "succeeded" and model.visual_calls == 0
            action = await _visual_action(instance, agent)
            assert action["state"] == "error" and action["result"]["error_code"] == "image_source_changed"
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_second_visual_call_in_one_run_is_rejected_without_another_model_call(tmp_path):
    async def scenario():
        model = RepeatVisualModel()
        instance, app, agent = await _setup(tmp_path, model)
        try:
            uploaded = await instance.images.upload(agent, _png())
            model.object_id = str(uploaded["object_id"])
            _receipt, run = await _run_visual(app, agent)
            assert run["state"] == "succeeded" and model.visual_calls == 1
            rows = await instance._database.read(
                lambda u: (
                    u.connection.execute(select(s.actions).where(s.actions.c.tool == VISUAL_SUBAGENT.name))
                    .mappings()
                    .all()
                )
            )
            assert len(rows) == 2
            assert sorted(row["state"] for row in rows) == ["error", "ok"]
            assert next(row for row in rows if row["state"] == "error")["result"]["error_code"] == "visual_unavailable"
        finally:
            await instance.close()

    asyncio.run(scenario())
