"""Campus assistant full-chain offline scenario through the production Web entry."""

import asyncio
import json
import time
from threading import Event

from fastapi.testclient import TestClient
from omni_test_support.v2.http import LocalModel

from omni.adapters.persistence.sqlite_access import sqlite_connection
from omni.composition.http import http_app
from omni.domain.background_review import REVIEW_PROMPT
from omni.domain.contracts import Invocation, ModelReply


def _authorize(client, home):
    client.headers["Authorization"] = "Bearer " + (home / "run/owner.token").read_text(encoding="utf-8")
    return client.get("/api/v1/agents").json()["items"][0]["id"]


def _model_base(client, agent):
    base = f"/api/v1/agents/{agent}"
    settings = client.get(base + "/settings").json()
    url = base + "/configurations/" + settings["value"]["model_profile_id"]
    profile = client.get(url).json()
    response = client.put(
        url,
        json={
            "name": profile["name"],
            "enabled": True,
            "revision": profile["revision"],
            "value": {**profile["value"], "model": "fixture"},
        },
    )
    assert response.status_code == 200, response.text
    return base


def _select_main(client, base):
    selection = client.put(base + "/omniweave/main-session", json={"session_id": None})
    assert selection.status_code == 200, selection.text
    return selection.json()["session_id"]


def _ticket_url(base, receipt):
    return base + f"/omniweave/sessions/{receipt['session_id']}/tickets"


def _wait_tasks(client, base, receipt, expected):
    view = None
    for _ in range(2000):
        response = client.get(_ticket_url(base, receipt), params={"run_id": receipt["run_id"]})
        assert response.status_code == 200, response.text
        view = response.json()
        if {task["key"]: task["state"] for task in view["tasks"]} == expected:
            return view
        time.sleep(0.05)
    raise AssertionError(view)


def _wait_message(client, base, session_id, fragment):
    message = None
    items: list = []
    for _ in range(2000):
        items = client.get(base + f"/sessions/{session_id}/messages").json()["items"]
        message = next(
            (row for row in items if row["role"] == "assistant" and fragment in row["content"]),
            message,
        )
        if message is not None:
            return message
        time.sleep(0.05)
    raise AssertionError(items)


def _rows(home, query, args=()):
    with sqlite_connection(home / "data/omni.db") as connection:
        return connection.execute(query, args).fetchall()


def test_campus_assistant_full_chain_with_revision_and_restart(tmp_path):
    class Model:
        def __init__(self):
            self.survey_published = asyncio.Event()
            self.probed = asyncio.Event()
            self.release_verification = Event()
            self.artifacts: dict[str, str] = {}

        async def complete(self, messages, tools, settings):
            if messages[0]["content"] == REVIEW_PROMPT:
                return ModelReply("No reusable procedure in this test turn", cost_micro=0)
            if str(messages[0]["content"]).startswith("OMNI_CONTEXT_SUMMARY_V1"):
                return ModelReply("Campus assistant kickoff", cost_micro=0)
            last = messages[-1]
            text = str(next(row["content"] for row in reversed(messages) if row["role"] == "user"))
            if not text.startswith("OmniWeave Worker."):
                if last["role"] == "tool":
                    return ModelReply("Plan queued in the main conversation", cost_micro=0)
                tasks = [
                    {
                        "key": "research",
                        "goal": "survey competitors",
                        "profile": "research",
                        "artifact_readers": ["coding", "verification"],
                        "peers": ["coding"],
                        "max_calls": 8,
                    },
                    {
                        "key": "coding",
                        "goal": "build prototype",
                        "profile": "coding",
                        "tools": ["files_write", "files_read"],
                        "peers": ["research"],
                        "artifact_readers": ["verification", "documentation"],
                        "max_calls": 8,
                    },
                    {
                        "key": "verification",
                        "goal": "verify prototype",
                        "profile": "verification",
                        "depends_on": ["coding"],
                        "max_calls": 8,
                    },
                    {
                        "key": "documentation",
                        "goal": "write handbook",
                        "profile": "documentation",
                        "depends_on": ["research", "coding", "verification"],
                        "artifact_readers": ["verification"],
                        "max_calls": 8,
                    },
                ]
                if text == "Campus install appendix":
                    tasks = [
                        {
                            "key": "research",
                            "goal": "survey competitors",
                            "profile": "research",
                            "reuse": {"task_key": "research", "goal_version": 1},
                        },
                        {
                            "key": "coding",
                            "goal": "build prototype",
                            "profile": "coding",
                            "reuse": {"task_key": "coding", "goal_version": 1},
                        },
                        {
                            "key": "verification",
                            "goal": "verify prototype",
                            "profile": "verification",
                            "reuse": {"task_key": "verification", "goal_version": 1},
                        },
                        {
                            "key": "documentation",
                            "goal": "document installation",
                            "profile": "documentation",
                            "tools": ["files_write", "files_read"],
                            "max_calls": 8,
                        },
                    ]
                return ModelReply("Plan", (Invocation("plan", "owa_plan", {"tasks": tasks}),), cost_micro=0)
            payload = json.loads(text.split("\n", 1)[1])
            results = [row["content"] for row in messages if row["role"] == "tool"]
            if "events" in payload:
                for event in payload["events"]:
                    if event["type"] == "peer.request":
                        if last["role"] == "tool":
                            return ModelReply("Interface details answered", cost_micro=0)
                        [citation] = event["references"]
                        assert citation["name"] == "survey" and citation["version"] == 1
                        return ModelReply(
                            "Answer",
                            (
                                Invocation(
                                    "answer",
                                    "reply_peer",
                                    {
                                        "request_id": event["request_id"],
                                        "answer": "Use the survey REST endpoints for schedules",
                                        "references": [{"artifact_id": self.artifacts["survey"], "version": 1}],
                                    },
                                ),
                            ),
                            cost_micro=0,
                        )
                    assert event["type"] == "peer.reply"
                    [citation] = event["references"]
                    assert citation["name"] == "survey" and citation["version"] == 1
                    if last["role"] != "tool":
                        return ModelReply(
                            "Build",
                            (
                                Invocation(
                                    "notes",
                                    "files_write",
                                    {"path": "prototype.txt", "content": "prototype with cited interface"},
                                ),
                            ),
                            cost_micro=0,
                        )
                    if '"duplicate"' in str(last["content"]):
                        return ModelReply("Prototype recorded with cited interface", cost_micro=0)
                    if str(last["content"]).startswith("sha256="):
                        return ModelReply(
                            "Publish",
                            (
                                Invocation(
                                    "prototype",
                                    "publish_artifact",
                                    {
                                        "name": "prototype",
                                        "key": "prototype",
                                        "expected_version": 0,
                                        "readers": ["verification", "documentation"],
                                        "text": "campus prototype v1",
                                    },
                                ),
                            ),
                            cost_micro=0,
                        )
                    return ModelReply(
                        "Check",
                        (Invocation("verify", "files_read", {"path": "prototype.txt"}),),
                        cost_micro=0,
                    )
                raise AssertionError("Unexpected events")
            goal = payload["goal"]
            if goal == "survey competitors":
                if last["role"] == "tool":
                    await self.probed.wait()
                    return ModelReply("Survey published, standing by for questions", cost_micro=0)
                return ModelReply(
                    "Publish",
                    (
                        Invocation(
                            "survey",
                            "publish_artifact",
                            {
                                "name": "survey",
                                "key": "survey",
                                "expected_version": 0,
                                "readers": ["coding", "verification"],
                                "text": "competitor survey v1",
                            },
                        ),
                    ),
                    cost_micro=0,
                )
            if goal == "build prototype":
                if not results:
                    await self.survey_published.wait()
                    return ModelReply(
                        "Ask",
                        (
                            Invocation(
                                "ask",
                                "request_peer",
                                {
                                    "key": "interface",
                                    "target": "research",
                                    "question": "Which interface should the prototype use?",
                                    "blocking": False,
                                    "references": [{"artifact_id": self.artifacts["survey"], "version": 1}],
                                },
                            ),
                        ),
                        cost_micro=0,
                    )
                return ModelReply("Waiting for the interface answer", cost_micro=0)
            if goal == "verify prototype":
                if not results:
                    return ModelReply(
                        "Verify",
                        (
                            Invocation("survey", "read_shared", {"artifact_id": self.artifacts["survey"]}),
                            Invocation("prototype", "read_shared", {"artifact_id": self.artifacts["prototype"]}),
                        ),
                        cost_micro=0,
                    )
                assert json.loads(results[0])["text"] == "competitor survey v1"
                assert json.loads(results[1])["text"] == "campus prototype v1"
                while not self.release_verification.is_set():
                    await asyncio.sleep(0.05)
                return ModelReply("Prototype verified against the survey", cost_micro=0)
            if goal == "write handbook":
                if last["role"] == "tool":
                    return ModelReply("Handbook drafted", cost_micro=0)
                return ModelReply(
                    "Draft",
                    (
                        Invocation(
                            "handbook",
                            "publish_artifact",
                            {
                                "name": "handbook",
                                "key": "handbook",
                                "expected_version": 0,
                                "readers": ["verification"],
                                "text": "campus handbook v1",
                            },
                        ),
                    ),
                    cost_micro=0,
                )
            if goal == "document installation":
                if not results:
                    return ModelReply(
                        "Draft",
                        (
                            Invocation(
                                "chapter",
                                "files_write",
                                {"path": "handbook_v2.txt", "content": "campus handbook v2 with installation"},
                            ),
                        ),
                        cost_micro=0,
                    )
                if str(last["content"]).startswith("sha256="):
                    return ModelReply("Installation chapter added", cost_micro=0)
                return ModelReply(
                    "Check",
                    (Invocation("verify", "files_read", {"path": "handbook_v2.txt"}),),
                    cost_micro=0,
                )
            raise AssertionError("Unexpected worker turn")

    model = Model()
    original_complete = Model.complete

    async def capture(self, messages, tools, settings):
        if messages[-1]["role"] == "tool":
            text = str(next((row["content"] for row in reversed(messages) if row["role"] == "user"), ""))
            if text.startswith("OmniWeave Worker."):
                try:
                    receipt = json.loads(str(messages[-1]["content"]))
                except json.JSONDecodeError:
                    receipt = {}
                if isinstance(receipt, dict):
                    if receipt.get("name") == "survey" and "survey" not in self.artifacts:
                        self.artifacts["survey"] = receipt["artifact_id"]
                        self.survey_published.set()
                    if receipt.get("name") == "prototype" and "prototype" not in self.artifacts:
                        self.artifacts["prototype"] = receipt["artifact_id"]
                    if receipt.get("request_id") and receipt.get("state") == "pending":
                        self.probed.set()
        return await original_complete(self, messages, tools, settings)

    Model.complete = capture

    home = tmp_path / "home"
    with TestClient(http_app(home, model=model, omniweave_mode="dispatch")) as client:
        agent = _authorize(client, home)
        base = _model_base(client, agent)
        session_id = _select_main(client, base)
        root = client.post(
            base + "/turns", json={"key": "campus", "session_id": session_id, "message": "Campus assistant kickoff"}
        ).json()
        exchange = None
        for _ in range(2000):
            rows = _rows(home, "SELECT request_references, reply_references FROM owa_peer_requests")
            if rows and rows[0][0] is not None and rows[0][1] is not None:
                exchange = rows[0]
                break
            time.sleep(0.05)
        assert exchange is not None, rows
        request_citation = json.loads(exchange[0]) if isinstance(exchange[0], str) else exchange[0]
        reply_citation = json.loads(exchange[1]) if isinstance(exchange[1], str) else exchange[1]
        assert request_citation[0]["artifact_id"] == model.artifacts["survey"]
        assert reply_citation == request_citation
        approval = None
        for _ in range(2000):
            items = client.get(base + "/approvals").json()["items"]
            if items:
                [approval] = items
                break
            time.sleep(0.05)
        assert approval is not None and approval["state"] == "pending"
        approved = client.post(
            base + f"/approvals/{approval['id']}",
            json={"allowed": True, "policy_revision": approval["policy_revision"]},
        )
        assert approved.status_code == 200, approved.text
        _wait_tasks(
            client,
            base,
            root,
            {
                "research": "succeeded",
                "coding": "succeeded",
                "verification": "running",
                "documentation": "queued",
            },
        )
        model.release_verification.set()
        finished = _wait_tasks(
            client,
            base,
            root,
            {
                "research": "succeeded",
                "coding": "succeeded",
                "verification": "succeeded",
                "documentation": "succeeded",
            },
        )
        assert finished["goal_version"] == 1
        _wait_message(client, base, session_id, "OmniWeave 交付 · 第 1 版 · 已完成")

    with TestClient(http_app(home, model=model, omniweave_mode="dispatch")) as client:
        agent = _authorize(client, home)
        base = _model_base(client, agent)
        revised = client.post(
            base + "/turns",
            json={
                "key": "appendix",
                "session_id": session_id,
                "message": "Campus install appendix",
                "goal_update": {"ticket_id": finished["ticket_id"], "expected_goal_version": 1},
            },
        ).json()
        assert revised["run_id"] != root["run_id"]
        approval = None
        for _ in range(2000):
            items = client.get(base + "/approvals").json()["items"]
            if items:
                [approval] = items
                break
            time.sleep(0.05)
        assert approval is not None and approval["state"] == "pending"
        approved = client.post(
            base + f"/approvals/{approval['id']}",
            json={"allowed": True, "policy_revision": approval["policy_revision"]},
        )
        assert approved.status_code == 200, approved.text
        _wait_message(client, base, session_id, "OmniWeave 交付 · 第 2 版 · 已完成")
        assert dict(_rows(home, "SELECT task_key, source_goal_version FROM owa_task_reuse")) == {
            "coding": 1,
            "research": 1,
            "verification": 1,
        }
        [delivery] = _rows(
            home,
            "SELECT d.outcome, d.evidence FROM owa_deliveries d JOIN owa_tickets t ON t.id = d.ticket_id "
            "WHERE t.goal_version = 2 AND (t.id = ? OR t.root_ticket_id = ?)",
            (finished["ticket_id"], finished["ticket_id"]),
        )
        assert delivery[0] == "completed"
        evidence = delivery[1] if isinstance(delivery[1], dict) else json.loads(delivery[1])
        retained = sorted(item["name"] for item in evidence.get("retained_artifacts", []))
        assert retained == ["prototype", "survey"]
        assert evidence["artifacts"] == []
        for name in ("survey", "prototype"):
            download = client.get(base + f"/omniweave/sessions/{session_id}/artifacts/{model.artifacts[name]}/content")
            assert download.status_code == 200, download.text
        handbook_v2 = list((home / "owa-workspaces").rglob("handbook_v2.txt"))
        assert len(handbook_v2) == 1 and "installation" in handbook_v2[0].read_text(encoding="utf-8")
        assert len(_rows(home, "SELECT id FROM owa_tickets")) == 2
    with TestClient(http_app(home, model=LocalModel(), omniweave_mode="enabled")) as client:
        _authorize(client, home)
        view = client.get(_ticket_url(base, root), params={"run_id": root["run_id"]}).json()
        assert view["goal_version"] == 2
        assert {task["key"] for task in view["tasks"]} == {"research", "coding", "verification", "documentation"}
