"""Production reflection control with injected models, media, and owner HTTP decisions."""

import asyncio
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from omni_test_support.v2.execution import wait_state
from omni_test_support.v2.media import BV, _seed, _settle
from omni_test_support.v2.media_zoom import configure_image_policy, zoom_instance
from omni_test_support.v2.routing import configured
from omni_test_support.v2.v2_configuration_helpers import model_instance

from omni.composition.http import http_app
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError, Invocation, ModelReply

SENTENCE = "📖 Observe before inferring."
BODY = {
    "encounter": "private encounter",
    "facts": "bounded observation",
    "author_view": "",
    "inference": "",
    "previous_judgment": "",
    "revision": SENTENCE,
    "missing": "unknown",
    "next_response": "",
}


class Script:
    def __init__(self, tool):
        self.tool = tool
        self.args = {}
        self.step = 0
        self.journal = None
        self.candidate = None
        self.seen = []

    async def complete(self, messages, tools, settings):
        if not tools:
            return ModelReply("Earlier evidence read was completed.", cost_micro=0)
        if "reflection_journal" not in {tool.name for tool in tools}:
            # K08 background review runs after the turn with its own read-only tool face.
            return ModelReply("no candidates", cost_micro=0)
        self.step += 1
        self.seen.append(messages[-1])
        assert "reflection_journal" in [t.name for t in tools]
        name = "reflection_journal"
        if self.step == 1:
            name, args = self.tool, self.args
        elif self.step == 2:
            args = {"operation": "observations"}
        elif self.step == 3:
            items = json.loads(messages[-1]["content"])["items"]
            anchor = items[0]
            link = (
                {"capture_id": anchor["capture_id"]}
                if self.tool == "document_read"
                else {"action_id": anchor["action_id"], "position": anchor["position"]}
            )
            args = {"operation": "save", "body": BODY, "anchors": [{**link, "field": "facts"}]}
        elif self.step == 4:
            self.journal = json.loads(messages[-1]["content"])["journal_id"]
            args = {"operation": "read", "journal_id": self.journal}
        elif self.step == 5:
            read = json.loads(messages[-1]["content"])
            assert read["body"]["revision"] == SENTENCE
            args = {
                "operation": "select",
                "journal_id": self.journal,
                "revision": read["revision"],
                "field": "revision",
                "start": 0,
                "end": len(SENTENCE),
                "sentence": SENTENCE,
            }
        elif self.step == 6:
            self.candidate = json.loads(messages[-1]["content"])["candidate_id"]
            args = {"operation": "approve", "candidate_id": self.candidate}
        else:
            assert "Unknown reflection operation" in messages[-1]["content"]
            return ModelReply("submitted for review", cost_micro=0)
        return ModelReply("", (Invocation(str(uuid.uuid4()), name, args),), cost_micro=0)


def credentials(client, home):
    client.headers["Authorization"] = "Bearer " + (home / "run/owner.token").read_text()
    return "/api/v1/agents/" + client.get("/api/v1/agents").json()["items"][0]["id"]


def test_owner_http_model_proposal_restart_and_source_erasure(tmp_path):
    model = Script("document_read")
    with TestClient(http_app(tmp_path, model=model)) as client:
        base = credentials(client, tmp_path)
        settings = client.get(base + "/settings").json()["value"]
        target = base + "/configurations/" + settings["model_profile_id"]
        profile = client.get(target).json()
        configured = client.put(
            target,
            json={
                "revision": profile["revision"],
                "name": "script model",
                "enabled": True,
                "value": {**profile["value"], "model": "fake", "context_tokens": 128000},
            },
        )
        assert configured.is_success, configured.text
        response = client.post(
            base + "/documents",
            json={
                "title": "evidence",
                "content": "bounded observation",
                "document_id": str(uuid.uuid4()),
                "revision": 0,
            },
        )
        assert response.status_code == 201, response.text
        doc = response.json()["id"]
        model.args = {"document_id": doc}
        turn = client.post(
            base + "/turns", json={"key": "reflect", "message": "review local evidence", "mode": "agent"}
        ).json()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            run = client.get(base + "/runs/" + turn["run_id"]).json()
            if run["state"] in ("failed", "succeeded", "interrupted"):
                break
            time.sleep(0.02)
        assert run["state"] == "succeeded", (run, model.seen[-1:])
        assert client.get(base + "/reflections?approved=true").json()["items"] == []
        detail = client.get(base + "/reflections/" + model.journal).json()
        candidate = detail["candidate"]
        url = base + "/reflections/candidates/" + model.candidate + "/decision"
        decision = {"decision": "approve", "revision": 1, "digest": candidate["digest"]}
        assert client.post(url, json=decision, headers={"Authorization": "Bearer invalid"}).status_code == 401
        assert client.post(url, json=decision).json()["state"] == "approved"
    with TestClient(http_app(tmp_path, model=model)) as client:
        base = credentials(client, tmp_path)
        assert client.get(base + "/reflections?approved=true").json()["items"][0]["sentence"] == SENTENCE
        erased = client.delete(base + "/documents/" + doc + "?revision=1")
        assert erased.is_success, erased.text
    with TestClient(http_app(tmp_path, model=model)) as client:
        base = credentials(client, tmp_path)
        assert client.get(base + "/reflections").json()["items"] == []
        assert client.get(base + "/reflections/" + model.journal).status_code == 409
        assert not client.post(url, json=decision).is_success
        assert model.step == 7


@pytest.mark.parametrize("tool", ["media_zoom", "media_locate"])
def test_settled_media_evidence_survives_run_and_erases_with_source(tmp_path, tool):
    async def scenario():
        model = Script(tool)
        instance = await zoom_instance(tmp_path, model)
        try:
            agent, config = await _seed(instance)
            await model_instance(instance, agent, model="fake", context_tokens=128000)
            source = await instance.media.register(agent, config["id"], BV)
            if tool == "media_zoom":
                await configure_image_policy(instance, agent)
                model.args = {"source_id": source["id"], "timestamp": 12.5}
            else:
                job = await instance.media.submit(agent, source["id"], "transcribe", {})
                assert (await _settle(instance, agent, job["id"]))["state"] == "done"
                model.args = {"source_id": source["id"], "query": "你好"}
            turn = await instance.application.submit_turn(agent, None, "reflect", "local review", mode="agent")
            if tool == "media_zoom":
                await wait_state(instance.application, agent, turn["run_id"], {"waiting_approval"})
                approval = (await instance.application.approvals(agent))[0]
                await instance.application.approve(agent, approval["id"], True, approval["policy_revision"])
            # This turn has seven model rounds, summary calls and, for zoom, an approval.
            async with asyncio.timeout(40):
                while True:
                    run = await instance.application.get_run(agent, turn["run_id"])
                    if run["state"] in {"succeeded", "failed", "interrupted"}:
                        break
                    await asyncio.sleep(0.05)
            assert run["state"] == "succeeded", (run, model.seen[-1:])
            detail = await instance.reflections.get(agent, model.journal)
            receipt = detail["sources"][0]
            assert receipt["source_id"] == source["id"] and receipt["start"] == (12.5 if tool == "media_zoom" else 0)
            assert receipt["kind"] == ("frame" if tool == "media_zoom" else "asr")
            protections = await instance._database.read(lambda u: u.retention_inspect(agent, turn["run_id"]))
            assert "reflection_media_source" in protections["protected_reasons"]
            c = detail["candidate"]
            await instance.reflections.decide(
                agent, c["id"], {"decision": "approve", "revision": 1, "digest": c["digest"]}
            )
            assert (await instance.reflections.page(agent, "", 20, True))["items"][0]["sentence"] == SENTENCE
            await instance.media.delete(agent, source["id"])
            assert not (await instance.reflections.page(agent, "", 20, True))["items"]
            protections = await instance._database.read(lambda u: u.retention_inspect(agent, turn["run_id"]))
            assert "reflection_media_source" not in protections["protected_reasons"]
            with pytest.raises(BusinessError):
                await instance.reflections.get(agent, model.journal)
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_journal_face_requires_an_evidence_source_and_is_inherited_by_children(tmp_path):
    """No anchor can exist without a document or media source, so the journal is not offered."""

    class Probe:
        def __init__(self):
            self.faces = []
            self.delegate = False

        async def complete(self, messages, tools, settings):
            if not tools:
                return ModelReply("no candidates", cost_micro=0)
            child = any(m["role"] == "user" and str(m["content"]).startswith("Delegated task.") for m in messages)
            names = {t.name for t in tools}
            if not child and "delegate_task" not in names:
                # K08 background review runs after the turn with its own read-only face.
                return ModelReply("no candidates", cost_micro=0)
            self.faces.append((child, "reflection_journal" in names))
            if child:
                return ModelReply("child done", cost_micro=0)
            if self.delegate and not any(m["role"] == "tool" for m in messages):
                return ModelReply(
                    "delegate", (Invocation("delegate", "delegate_task", {"goal": "review later"}),), cost_micro=0
                )
            return ModelReply("parent done", cost_micro=0)

    async def scenario():
        probe = Probe()
        instance = await open_instance(tmp_path, model=probe)
        try:
            agent = (await configured(instance))[0]
            plain = await instance.application.submit_turn(agent, None, "plain", "no evidence yet", mode="agent")
            await wait_state(instance.application, agent, plain["run_id"], {"succeeded", "failed"}, timeout=30)
            assert probe.faces == [(False, False)]
            await instance.documents.save(agent, "evidence", "bounded observation")
            probe.delegate = True
            second = await instance.application.submit_turn(agent, None, "reflect", "delegate a review", mode="agent")
            ended = await wait_state(instance.application, agent, second["run_id"], {"succeeded", "failed"}, timeout=30)
            assert ended["state"] == "succeeded", ended
            # Parent face, delegated child face, then the parent's follow-up call all carry the journal.
            assert probe.faces[1:] == [(False, True), (True, True), (False, True)], probe.faces
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())
