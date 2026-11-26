"""K16 batch 3: media_zoom end-to-end security through the production approval gate and frames job."""

from __future__ import annotations

import asyncio
import json
import uuid

from omni_test_support.v2.execution import wait_state
from omni_test_support.v2.media import (
    BV,
    CHANNEL,
    FakeProcessor,
    _seed,
    _settle,
)
from omni_test_support.v2.media_source import fixture as gateway_fixture
from omni_test_support.v2.media_zoom import configure_image_policy, zoom_instance
from sqlalchemy import select, update

from omni.adapters.persistence import schema as s
from omni.application.media import ZOOM_SPEC
from omni.domain.contracts import Invocation, ModelReply


class ZoomModel:
    def __init__(self):
        self.requests = []
        self.arguments = {}

    async def complete(self, messages, tools, settings):
        self.requests.append(messages)
        if len(self.requests) == 1:
            return ModelReply("", (Invocation(str(uuid.uuid4()), ZOOM_SPEC.name, self.arguments),), cost_micro=0)
        return ModelReply("done", cost_micro=0)


class WaitingProcessor(FakeProcessor):
    def __init__(self):
        super().__init__()
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.cleaned = False

    async def frames(self, video, sheet_output, count, width, timestamps=None, select="equidistant"):
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cleaned = True
            raise
        return await super().frames(video, sheet_output, count, width, timestamps, select)


async def bilibili_source(instance, model):
    agent, config = await _seed(instance)
    source = await instance.media.register(agent, str(config["id"]), BV)
    await configure_image_policy(instance, agent)
    model.arguments = {"source_id": str(source["id"]), "timestamp": 12.5}
    return agent, str(source["id"]), config


async def start_approval(instance, agent):
    receipt = await instance.application.submit_turn(agent, None, "zoom the frame", "zoom")
    run_id = receipt["run_id"]
    await wait_state(instance.application, agent, run_id, {"waiting_approval"})
    approval = (await instance.application.approvals(agent))[0]
    assert approval["tool"] == ZOOM_SPEC.name
    return run_id, approval


async def only_action(instance, agent):
    actions = await instance.application.query("actions", agent)
    assert len(actions) == 1
    return actions[0]


# ---------- approval gate security ----------


def test_zoom_denied_approval_submits_no_job(tmp_path):
    async def scenario():
        model = ZoomModel()
        instance = await zoom_instance(tmp_path, model)
        try:
            agent, source_id, _config = await bilibili_source(instance, model)
            run_id, approval = await start_approval(instance, agent)
            await instance.application.approve(agent, approval["id"], False, approval["policy_revision"])
            run = await wait_state(instance.application, agent, run_id, {"succeeded", "failed"})
            assert run["state"] == "succeeded"
            action = await only_action(instance, agent)
            assert action["state"] == "denied" and action["effect"] == "none"
            assert (await instance.media.jobs(agent, source_id))["items"] == []
            assert not (await instance.media.source(agent, source_id))["latest_frames_id"]
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_zoom_revoked_approval_cancels_without_job(tmp_path):
    async def scenario():
        model = ZoomModel()
        instance = await zoom_instance(tmp_path, model)
        try:
            agent, source_id, _config = await bilibili_source(instance, model)
            run_id, _approval = await start_approval(instance, agent)
            await instance.application.cancel(agent, run_id)
            run = await wait_state(instance.application, agent, run_id, {"cancelled"})
            assert run["state"] == "cancelled"
            action = await only_action(instance, agent)
            assert action["state"] == "cancelled" and action["effect"] == "none"
            assert (await instance.media.jobs(agent, source_id))["items"] == []
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_zoom_expired_approval_interrupts_without_job(tmp_path):
    async def scenario():
        model = ZoomModel()
        instance = await zoom_instance(tmp_path, model)
        try:
            agent, source_id, _config = await bilibili_source(instance, model)
            run_id, _approval = await start_approval(instance, agent)
            await instance._database.write(
                lambda u: u.connection.execute(
                    update(s.approvals)
                    .where(s.approvals.c.agent_id == agent)
                    .values(expires_at="2020-01-01T00:00:00.000000+00:00")
                )
            )
            run = await wait_state(instance.application, agent, run_id, {"interrupted"})
            assert run["error_code"] == "approval_expired"
            action = await only_action(instance, agent)
            assert action["effect"] == "none"
            assert (await instance.media.jobs(agent, source_id))["items"] == []
        finally:
            await instance.close()

    asyncio.run(scenario())


# ---------- owner cancellation joins real job cleanup ----------


def test_zoom_owner_cancel_during_job_cleans_up(tmp_path):
    async def scenario():
        processor = WaitingProcessor()
        model = ZoomModel()
        instance = await zoom_instance(tmp_path, model, processor=processor)
        try:
            agent, source_id, _config = await bilibili_source(instance, model)
            run_id, approval = await start_approval(instance, agent)
            await instance.application.approve(agent, approval["id"], True, approval["policy_revision"])
            await asyncio.wait_for(processor.entered.wait(), 8)
            job_id = str((await instance.media.jobs(agent, source_id))["items"][0]["id"])
            await instance.application.cancel(agent, run_id)
            run = await wait_state(instance.application, agent, run_id, {"cancelled"})
            assert run["state"] == "cancelled"
            processor.release.set()
            job = await _settle(instance, agent, job_id, timeout=8, wait_for_cleanup=True)
            assert job["state"] == "cancelled" and processor.cleaned
            assert not (instance.media._staging / job_id).exists()
            assert not (await instance.media.source(agent, source_id))["latest_frames_id"]
        finally:
            processor.release.set()
            await instance.close()

    asyncio.run(scenario())


# ---------- credential / source CAS and delete barrier ----------


def test_zoom_credential_revision_cas_fails_job_without_artifact(tmp_path):
    async def scenario():
        model = ZoomModel()
        instance = await zoom_instance(tmp_path, model)
        try:
            agent, source_id, config = await bilibili_source(instance, model)
            row = await instance.configurations.get(agent, str(config["id"]))
            await instance.configurations.save(agent, row["id"], row["name"], True, CHANNEL, row["revision"])
            run_id, approval = await start_approval(instance, agent)
            await instance.application.approve(agent, approval["id"], True, approval["policy_revision"])
            run = await wait_state(instance.application, agent, run_id, {"succeeded", "failed"})
            assert run["state"] == "succeeded"
            jobs = (await instance.media.jobs(agent, source_id))["items"]
            assert len(jobs) == 1
            assert jobs[0]["state"] == "failed"
            assert jobs[0]["error_code"] in {"bilibili_credential_changed", "bilibili_configuration_revoked"}
            action = await only_action(instance, agent)
            assert action["state"] == "error" and action["effect"] == "none"
            assert not (await instance.media.source(agent, source_id))["latest_frames_id"]
            await _settle(instance, agent, jobs[0]["id"], wait_for_cleanup=True)
            assert not (instance.media._staging / str(jobs[0]["id"])).exists()
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_zoom_source_identity_cas_fails_job_without_artifact(tmp_path):
    async def scenario():
        gateway, state, _calls, _gate = gateway_fixture()
        model = ZoomModel()
        instance = await zoom_instance(tmp_path, model, gateway=gateway)
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            await configure_image_policy(instance, agent)
            model.arguments = {"source_id": str(source["id"]), "timestamp": 1.0}
            state["cid"] = 790
            run_id, approval = await start_approval(instance, agent)
            await instance.application.approve(agent, approval["id"], True, approval["policy_revision"])
            run = await wait_state(instance.application, agent, run_id, {"succeeded", "failed"})
            assert run["state"] == "succeeded"
            jobs = (await instance.media.jobs(agent, str(source["id"])))["items"]
            assert len(jobs) == 1
            assert jobs[0]["state"] == "failed" and jobs[0]["error_code"] == "bilibili_source_changed"
            assert not (await instance.media.source(agent, str(source["id"])))["latest_frames_id"]
            assert not gateway.payloads
        finally:
            state["cid"] = 789
            await instance.close()
            await gateway.close()

    asyncio.run(scenario())


def test_zoom_deleted_source_barrier_produces_no_artifact(tmp_path):
    async def scenario():
        model = ZoomModel()
        instance = await zoom_instance(tmp_path, model)
        try:
            agent, source_id, _config = await bilibili_source(instance, model)
            await instance.media.delete(agent, source_id)
            run_id, approval = await start_approval(instance, agent)
            await instance.application.approve(agent, approval["id"], True, approval["policy_revision"])
            run = await wait_state(instance.application, agent, run_id, {"succeeded", "failed"})
            assert run["state"] == "succeeded"
            action = await only_action(instance, agent)
            assert action["state"] == "error" and action["effect"] == "none"
        finally:
            await instance.close()

    asyncio.run(scenario())


# ---------- unknown outcome is never replayed ----------


def test_zoom_unknown_job_never_replayed(tmp_path):
    async def scenario():
        processor = FakeProcessor()
        processor.failure = RuntimeError("worker crashed")
        model = ZoomModel()
        instance = await zoom_instance(tmp_path, model, processor=processor)
        try:
            agent, source_id, _config = await bilibili_source(instance, model)
            run_id, approval = await start_approval(instance, agent)
            await instance.application.approve(agent, approval["id"], True, approval["policy_revision"])
            run = await wait_state(instance.application, agent, run_id, {"failed"})
            assert run["state"] == "failed" and run["error_code"].startswith("action_outcome_unknown")
            jobs = (await instance.media.jobs(agent, source_id))["items"]
            assert len(jobs) == 1 and jobs[0]["state"] == "unknown"
            action = await only_action(instance, agent)
            assert action["state"] == "unknown" and action["effect"] == "unknown"
            assert len(model.requests) == 1
            assert not (await instance.media.source(agent, source_id))["latest_frames_id"]
        finally:
            await instance.close()

    asyncio.run(scenario())


# ---------- full chain on an uploaded object source ----------


def test_zoom_uploaded_video_full_chain_visible_and_released(tmp_path):
    async def scenario():
        model = ZoomModel()
        instance = await zoom_instance(tmp_path, model)
        try:
            agent, _config = await _seed(instance)
            obj = await instance.objects.publish(agent, b"isolated-video-bytes", "video/mp4")
            source = await instance.media.register_upload(agent, str(obj["id"]), "local video")
            await configure_image_policy(instance, agent)
            model.arguments = {"source_id": str(source["id"]), "timestamp": 1.0}
            run_id, approval = await start_approval(instance, agent)
            await instance.application.approve(agent, approval["id"], True, approval["policy_revision"])
            await wait_state(instance.application, agent, run_id, {"succeeded"})
            assert len(model.requests) == 2
            tool_messages = [m for m in model.requests[1] if m.get("role") == "tool"]
            assert len(tool_messages) == 1
            content = tool_messages[0]["content"]
            assert isinstance(content, list) and content[0]["type"] == "text"
            assert json.dumps(content).count("data:image/png;base64,") == 1
            assert '"detail": "low"' in json.dumps(content)
            rows = await instance._database.read(
                lambda u: [
                    dict(r)
                    for r in u.connection.execute(
                        select(s.model_call_images)
                        .join(s.model_calls, s.model_calls.c.id == s.model_call_images.c.call_id)
                        .where(s.model_call_images.c.agent_id == agent, s.model_calls.c.run_id == run_id)
                    ).mappings()
                ]
            )
            assert len(rows) == 1
            assert rows[0]["message_id"] is None and rows[0]["object_id"] is None
            assert rows[0]["erased_at"] is None and rows[0]["state"] == "attached"
        finally:
            await instance.close()

    asyncio.run(scenario())
