"""K17 owner decision closeout through the production HTTP entry and restart paths."""

import asyncio
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from omni_test_support.v2.documents import Clock, read_action
from sqlalchemy import func, select

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.memory_repository import MemoryRepositories
from omni.composition.http import http_app
from omni.composition.instance import open_instance
from omni.domain.contracts import (
    ActionResult,
    BusinessError,
    Invocation,
    InvocationContext,
    ModelReply,
    Outcome,
)

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
    """Reads the requested documents, then drives observations/read/select inside one run."""

    def __init__(self, documents):
        self.documents = documents
        self.step = 0
        self.journal = None
        self.candidate = None
        self.seen = []

    async def complete(self, messages, tools, settings):
        names = {tool.name for tool in tools}
        if not tools:
            return ModelReply("Earlier evidence read was completed.", cost_micro=0)
        if "reflection_journal" not in names:
            # K08 background review runs after the turn with its own read-only tool face.
            return ModelReply("no candidates", cost_micro=0)
        self.step += 1
        self.seen.append(messages[-1])
        if self.step <= len(self.documents):
            arguments = {"document_id": self.documents[self.step - 1]}
            return ModelReply("", (Invocation(str(uuid.uuid4()), "document_read", arguments),), cost_micro=0)
        stage = self.step - len(self.documents)
        if stage == 1:
            arguments = {"operation": "observations"}
        elif stage == 2:
            items = json.loads(messages[-1]["content"])["items"]
            arguments = {
                "operation": "save",
                "body": BODY,
                "anchors": [{"capture_id": item["capture_id"], "field": "facts"} for item in items],
            }
        elif stage == 3:
            self.journal = json.loads(messages[-1]["content"])["journal_id"]
            arguments = {"operation": "read", "journal_id": self.journal}
        elif stage == 4:
            read = json.loads(messages[-1]["content"])
            arguments = {
                "operation": "select",
                "journal_id": self.journal,
                "revision": read["revision"],
                "field": "revision",
                "start": 0,
                "end": len(SENTENCE),
                "sentence": SENTENCE,
            }
        elif stage == 5:
            self.candidate = json.loads(messages[-1]["content"])["candidate_id"]
            # The model cannot decide: this must fail as an unknown operation.
            arguments = {"operation": "approve", "candidate_id": self.candidate}
        else:
            assert "Unknown reflection operation" in messages[-1]["content"], messages[-1]
            return ModelReply("submitted for review", cost_micro=0)
        return ModelReply("", (Invocation(str(uuid.uuid4()), "reflection_journal", arguments),), cost_micro=0)


class TextModel:
    async def complete(self, messages, tools, settings):
        return ModelReply("done", cost_micro=0)


def credentials(client, home):
    client.headers["Authorization"] = "Bearer " + (home / "run/owner.token").read_text()
    return "/api/v1/agents/" + client.get("/api/v1/agents").json()["items"][0]["id"]


def configure(client, base):
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


def document(client, base, content):
    response = client.post(
        base + "/documents",
        json={"title": "evidence", "content": content, "document_id": str(uuid.uuid4()), "revision": 0},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def run_turn(client, base, model, documents, key="reflect"):
    model.documents = documents
    turn = client.post(base + "/turns", json={"key": key, "message": "review local evidence", "mode": "agent"}).json()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        run = client.get(base + "/runs/" + turn["run_id"]).json()
        if run["state"] in ("failed", "succeeded", "interrupted"):
            return run
        time.sleep(0.02)
    raise AssertionError(run)


def test_submit_approve_retract_restart_never_resurrects(tmp_path):
    model = Script([])
    with TestClient(http_app(tmp_path, model=model)) as client:
        base = credentials(client, tmp_path)
        configure(client, base)
        doc = document(client, base, "bounded observation")
        run = run_turn(client, base, model, [doc])
        assert run["state"] == "succeeded", (run, model.seen[-1:])
        queue = client.get(base + "/reflections").json()["items"]
        assert len(queue) == 1
        item = queue[0]
        assert item["sentence"] == SENTENCE and item["candidate_id"]
        assert client.get(base + "/reflections?approved=true").json()["items"] == []
        decided = client.post(
            base + "/reflections/candidates/" + item["candidate_id"] + "/decision",
            json={"decision": "approve", "revision": item["candidate_revision"], "digest": item["digest"]},
        ).json()
        assert decided["state"] == "approved" and decided["revision"] == 2
        approved = client.get(base + "/reflections?approved=true").json()["items"]
        assert [row["sentence"] for row in approved] == [SENTENCE]
        # An approved journal leaves the owner queue immediately.
        assert client.get(base + "/reflections").json()["items"] == []
        detail = client.get(base + "/reflections/" + model.journal).json()
        assert detail["candidate"]["state"] == "approved" and detail["withdrawn"] is False
        retracted = client.post(
            base + "/reflections/candidates/" + item["candidate_id"] + "/decision",
            json={
                "decision": "retract",
                "revision": detail["candidate"]["revision"],
                "digest": detail["candidate"]["digest"],
            },
        ).json()
        assert retracted["state"] == "retracted"
        assert client.get(base + "/reflections?approved=true").json()["items"] == []
        assert client.get(base + "/reflections").json()["items"] == []
        after = client.get(base + "/reflections/" + model.journal).json()
        assert after["withdrawn"] is True and after["candidate"]["state"] == "retracted"
        steps = model.step
    with TestClient(http_app(tmp_path, model=model)) as client:
        base = credentials(client, tmp_path)
        assert client.get(base + "/reflections?approved=true").json()["items"] == []
        assert client.get(base + "/reflections").json()["items"] == []
        detail = client.get(base + "/reflections/" + model.journal).json()
        assert detail["withdrawn"] is True and detail["candidate"]["revision"] == 3
        replay = client.post(
            base + "/reflections/candidates/" + after["candidate"]["id"] + "/decision",
            json={"decision": "approve", "revision": 2, "digest": after["candidate"]["digest"]},
        )
        assert not replay.is_success
        # Restarting the owner workbench must not wake the run or revive the sentence.
        assert model.step == steps


def test_multisource_delete_one_scrubs_the_whole_journal(tmp_path):
    model = Script([])
    with TestClient(http_app(tmp_path, model=model)) as client:
        base = credentials(client, tmp_path)
        configure(client, base)
        first = document(client, base, "first observation")
        second = document(client, base, "second observation")
        run = run_turn(client, base, model, [first, second])
        assert run["state"] == "succeeded", (run, model.seen[-1:])
        item = client.get(base + "/reflections").json()["items"][0]
        client.post(
            base + "/reflections/candidates/" + item["candidate_id"] + "/decision",
            json={"decision": "approve", "revision": item["candidate_revision"], "digest": item["digest"]},
        )
        assert len(client.get(base + "/reflections?approved=true").json()["items"]) == 1
        erased = client.delete(base + "/documents/" + first + "?revision=1")
        assert erased.is_success, erased.text
        assert client.get(base + "/reflections?approved=true").json()["items"] == []
        assert client.get(base + "/reflections").json()["items"] == []
        assert client.get(base + "/reflections/" + model.journal).status_code == 409
        # The untouched source still exists; only the shared journal was invalidated.
        assert client.get(base + "/documents/" + second + "?revision=1").status_code == 200
    with TestClient(http_app(tmp_path, model=model)) as client:
        base = credentials(client, tmp_path)
        assert client.get(base + "/reflections?approved=true").json()["items"] == []
        assert client.get(base + "/reflections/" + model.journal).status_code == 409


async def seed_pending(instance, agent):
    """One journal with a proposed candidate through the live services of a production instance."""
    document = await instance.documents.save(agent, "source", "ab📖")
    turn, action = await instance._database.write(lambda u: read_action(u, agent))
    context = InvocationContext(agent, turn["run_id"], turn["session_id"], 1)
    await instance.documents.invoke(
        "document_read",
        {"document_id": document["id"], "start": 2, "length": 1},
        operation_id=action["id"],
        context=context,
    )
    await instance._database.write(
        lambda u: u.settle_action(agent, action["id"], ActionResult(Outcome.OK, "ab📖 evidence"))
    )
    await instance._database.write(
        lambda u: u.remember(agent, "derived fact", document_revision_id=str(document["revision_id"]), quote="📖")
    )
    capture = await instance._database.read(
        lambda u: u.connection.scalar(
            select(s.context_documents.c.id).where(s.context_documents.c.action_id == action["id"])
        )
    )
    journal = await instance._database.write(
        lambda u: u.reflection_journal_save(agent, turn["run_id"], BODY, [{"capture_id": capture, "field": "facts"}])
    )
    await instance._database.write(
        lambda u: u.reflection_model_review(agent, turn["run_id"], {"operation": "read", "journal_id": journal["id"]})
    )
    candidate = await instance._database.write(
        lambda u: u.reflection_model_review(
            agent,
            turn["run_id"],
            {
                "operation": "select",
                "journal_id": journal["id"],
                "revision": journal["revision"],
                "field": "revision",
                "start": 0,
                "end": len(SENTENCE),
                "sentence": SENTENCE,
            },
        )
    )
    return document, journal, candidate


def test_restart_resumes_interrupted_erasure_behind_the_barrier(tmp_path, monkeypatch):
    async def first():
        instance = await open_instance(tmp_path, clock=Clock(), capabilities=(), model=TextModel())
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            document, journal, candidate = await seed_pending(instance, agent)
            candidate_id = candidate["candidate_id"]
            detail = await instance.reflections.get(agent, journal["id"])
            approved = await instance.reflections.decide(
                agent, candidate_id, {"decision": "approve", "revision": 1, "digest": detail["candidate"]["digest"]}
            )
            assert approved["state"] == "approved"

            original = MemoryRepositories._forget_one

            def cut(self, *args, **kwargs):
                original(self, *args, **kwargs)
                raise RuntimeError("fault after derived erasure")

            with monkeypatch.context() as patch:
                patch.setattr(MemoryRepositories, "_forget_one", cut)
                with pytest.raises(RuntimeError, match="fault"):
                    await instance.documents.erase(agent, document["id"], 1)
            # The erasure request committed its barrier and scrubbed the journal even though
            # the source removal rolled back; the failed job stays retryable.
            job = await instance._database.read(lambda u: u.document_erasure_status(agent, document["id"]))
            assert job["state"] == "failed" and job["error_code"] == "erasure_interrupted"
            assert (await instance.reflections.page(agent, "", 20, True))["items"] == []
            assert (await instance.reflections.page(agent, "", 20, False))["items"] == []
            with pytest.raises(BusinessError):
                await instance.reflections.get(agent, journal["id"])
            return agent, document, journal, candidate_id
        finally:
            assert (await instance.close())["complete"]

    agent, document, journal, candidate = asyncio.run(first())

    async def second():
        instance = await open_instance(tmp_path, clock=Clock(), capabilities=(), model=TextModel())
        try:
            job = await instance._database.read(lambda u: u.document_erasure_status(agent, document["id"]))
            assert job["state"] == "failed"
            with pytest.raises(BusinessError):
                await instance.reflections.get(agent, journal["id"])
            await instance.documents.erase(agent, document["id"], 1)
            job = await instance._database.read(lambda u: u.document_erasure_status(agent, document["id"]))
            assert job["state"] == "done" and job["attempt"] == 2
            row = await instance._database.read(
                lambda u: dict(
                    u.connection.execute(
                        select(s.reflection_journals).where(s.reflection_journals.c.id == journal["id"])
                    )
                    .mappings()
                    .one()
                )
            )
            assert (row["state"], row["body"], row["fingerprint"]) == ("erased", None, None)
            for table in (s.reflection_reads, s.reflection_candidates, s.reflection_document_links):
                assert (
                    await instance._database.read(
                        lambda u: u.connection.scalar(select(func.count()).select_from(table))
                    )
                    == 0
                )
            assert (await instance.reflections.page(agent, "", 20, True))["items"] == []
            assert (await instance.reflections.page(agent, "", 20, False))["items"] == []
            with pytest.raises(BusinessError):
                await instance.reflections.get(agent, journal["id"])
            with pytest.raises(BusinessError):
                await instance.reflections.decide(
                    agent, candidate, {"decision": "approve", "revision": 1, "digest": "0" * 64}
                )
            # A retried request after completion is idempotent and still reports done.
            await instance.documents.erase(agent, document["id"], 1)
            job = await instance._database.read(lambda u: u.document_erasure_status(agent, document["id"]))
            assert job["state"] == "done"
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(second())
