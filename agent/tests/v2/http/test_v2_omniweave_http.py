"""OWA observes admissions at the production owner Web entry point."""

import asyncio
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier, Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from omni_test_support.v2.http import LocalModel
from omni_test_support.v2.mcp_flow import wait_run
from omni_test_support.v2.v2_action_review_helpers import review_request

from omni.adapters.object_store import ObjectFiles
from omni.adapters.persistence import schema as s
from omni.adapters.persistence.delegation_repository import DelegationRepositories
from omni.adapters.persistence.lease_repository import LeaseRepositories
from omni.adapters.persistence.omniweave_repository import OmniWeaveRepositories
from omni.adapters.persistence.repository import Repositories
from omni.adapters.persistence.sqlite_access import sqlite_connection
from omni.adapters.primitives import SystemClock
from omni.adapters.workers.client import WorkerClient
from omni.composition.http import http_app
from omni.domain.background_review import REVIEW_PROMPT
from omni.domain.contracts import ActionResult, BusinessError, Effect, Invocation, ModelReply, Outcome


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


def _select_main(client, base, session_id=None):
    selection = client.put(base + "/omniweave/main-session", json={"session_id": session_id})
    assert selection.status_code == 200, selection.text
    assert selection.json()["enabled"] is True
    return selection.json()["session_id"]


def _rows(home, query, args=()):
    with sqlite_connection(home / "data/omni.db") as connection:
        return connection.execute(query, args).fetchall()


def _counts(home, *tables):
    with sqlite_connection(home / "data/omni.db") as connection:
        return [connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tables]


OWA_TABLES = ("owa_threads", "owa_tickets", "owa_triggers")


@pytest.mark.parametrize("observe", [False, True])
def test_owner_web_admission_keeps_normal_receipt_and_work(tmp_path, observe):
    options = {"omniweave_mode": "observe"} if observe else {}
    with TestClient(http_app(tmp_path, model=LocalModel(), **options)) as client:
        payload = {"key": "owner-web", "message": "private request"}
        unauthenticated = client.post("/api/v1/agents/00000000-0000-0000-0000-000000000000/turns", json=payload)
        assert unauthenticated.status_code == 401
        agent = _authorize(client, tmp_path)
        base = _model_base(client, agent)
        assert client.post(base + "/turns", json={**payload, "ticket_id": agent}).status_code == 422
        accepted = client.post(base + "/turns", json=payload)
        assert accepted.status_code == 202, accepted.text
        receipt = accepted.json()
        run, session = receipt["run_id"], receipt["session_id"]
        assert receipt == {"run_id": run, "session_id": session, "duplicate": False}
        assert wait_run(client, base, run)["state"] == "succeeded"
        messages = client.get(base + f"/sessions/{session}/messages").json()["items"]
        assert [item["content"] for item in messages] == ["private request", "verified reply"]
        events = client.get(base + f"/runs/{run}/events").json()
        assert events["next_cursor"] and events["stream"]["high_water"] > 0
        assert _rows(tmp_path, "SELECT id, parent_id FROM runs") == [(run, None)]
        assert _rows(tmp_path, "SELECT run_id, kind FROM work_items") == [(run, "turn")]
        assert _counts(tmp_path, "inbox", "run_delegations", *OWA_TABLES) == [1, 0, *([int(observe)] * 3)]
        if observe:
            owner = _rows(tmp_path, "SELECT owner_id FROM instances WHERE slot=1")[0][0]
            digest = _rows(tmp_path, "SELECT request_fingerprint FROM inbox WHERE key='owner-web'")[0][0]
            assert _rows(
                tmp_path,
                """SELECT h.agent_id, h.main_session_id, h.owner_id, t.run_id, i.key,
                          g.source, g.version, g.decision, g.payload_sha256
                   FROM owa_threads h JOIN owa_tickets t ON t.thread_id=h.id
                   JOIN owa_triggers g ON g.ticket_id=t.id JOIN inbox i ON g.inbox_id=i.id""",
            ) == [(agent, session, owner, run, "owner-web", "owner_web", 1, "observe", digest)]


def test_concurrent_replays_and_payload_conflict_keep_one_admission(tmp_path):
    with TestClient(http_app(tmp_path, model=LocalModel(), omniweave_mode="observe")) as client:
        base = _model_base(client, _authorize(client, tmp_path))
        simultaneous = Barrier(2, timeout=10)

        def submit(message):
            simultaneous.wait()
            return client.post(base + "/turns", json={"key": "retry", "message": message})

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(submit, ["same", "same"]))
        assert [response.status_code for response in responses] == [202, 202]
        original, replay = sorted((response.json() for response in responses), key=lambda row: row["duplicate"])
        assert original["duplicate"] is False
        assert replay == {**original, "duplicate": True}
        conflict = client.post(base + "/turns", json={"key": "retry", "message": "different"})
        assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "idempotency_conflict"
        assert _counts(tmp_path, "inbox", "runs", "work_items", *OWA_TABLES) == [1] * 6


def test_interrupted_observation_rolls_back_turn_before_it_can_run(tmp_path, monkeypatch):
    with TestClient(http_app(tmp_path, model=LocalModel(), omniweave_mode="observe")) as client:
        base = _model_base(client, _authorize(client, tmp_path))
        write_observation = OmniWeaveRepositories.omniweave_observe

        def abort(self, *args, **kwargs):
            write_observation(self, *args, **kwargs)
            raise BusinessError("fixture_abort", "Interrupted before commit", status=503)

        payload = {"key": "rolled-back", "message": "retry after rollback"}
        with monkeypatch.context() as interrupted:
            interrupted.setattr(OmniWeaveRepositories, "omniweave_observe", abort)
            response = client.post(base + "/turns", json=payload)
            assert response.status_code == 503 and response.json()["error"]["code"] == "fixture_abort"
        assert (
            _counts(tmp_path, "inbox", "sessions", "runs", "messages", "work_items", "run_events", *OWA_TABLES)
            == [0] * 9
        )
        accepted = client.post(base + "/turns", json=payload)
        assert accepted.status_code == 202 and accepted.json()["duplicate"] is False
        assert wait_run(client, base, accepted.json()["run_id"])["state"] == "succeeded"
        assert _counts(tmp_path, "inbox", "sessions", "runs", "work_items", *OWA_TABLES) == [1] * 7


def test_token_revoked_between_http_authentication_and_admission_cannot_create_ticket(tmp_path, monkeypatch):
    with TestClient(http_app(tmp_path, model=LocalModel(), omniweave_mode="observe")) as client:
        base = _model_base(client, _authorize(client, tmp_path))
        runtime = client.app.state.runtime
        authenticate = runtime.authenticate

        async def rotate_after_authentication(token):
            assert await authenticate(token)
            await runtime.rotate_token()
            return True

        payload = {"key": "rotated", "message": "only the current token can submit"}
        with monkeypatch.context() as rotation:
            rotation.setattr(runtime, "authenticate", rotate_after_authentication)
            revoked = client.post(base + "/turns", json=payload)
        assert revoked.status_code == 401 and revoked.json()["error"]["code"] == "unauthenticated"
        assert _counts(tmp_path, "inbox", "runs", "work_items", *OWA_TABLES) == [0] * 6
        _authorize(client, tmp_path)
        accepted = client.post(base + "/turns", json=payload)
        assert accepted.status_code == 202 and accepted.json()["duplicate"] is False


def test_restart_preserves_off_and_observe_decisions_and_reuses_thread(tmp_path):
    original = {"key": "before-observe", "message": "legacy request"}
    with TestClient(http_app(tmp_path, model=LocalModel())) as client:
        base = _model_base(client, _authorize(client, tmp_path))
        old = client.post(base + "/turns", json=original).json()
        assert wait_run(client, base, old["run_id"])["state"] == "succeeded"
    new = {"key": "observed", "message": "next request", "session_id": old["session_id"]}
    with TestClient(http_app(tmp_path, model=LocalModel(), omniweave_mode="observe")) as client:
        _authorize(client, tmp_path)
        assert client.post(base + "/turns", json=original).json() == {**old, "duplicate": True}
        assert _counts(tmp_path, *OWA_TABLES) == [0, 0, 0]
        observed = client.post(base + "/turns", json=new).json()
        assert observed["duplicate"] is False
        assert wait_run(client, base, observed["run_id"])["state"] == "succeeded"
        ids = _rows(tmp_path, "SELECT ticket_id, id FROM owa_triggers")
    with TestClient(http_app(tmp_path, model=LocalModel(), omniweave_mode="dispatch")) as client:
        _authorize(client, tmp_path)
        assert client.post(base + "/turns", json=new).json() == {**observed, "duplicate": True}
        assert _rows(tmp_path, "SELECT ticket_id, id FROM owa_triggers") == ids
        assert _counts(tmp_path, "owa_executions") == [0]
        assert client.get(base + "/omniweave/main-session").json() == {"enabled": True, "session_id": None}
        assert _select_main(client, base, old["session_id"]) == old["session_id"]
        response = client.post(base + "/turns", json={**new, "key": "same-session"})
        assert response.status_code == 202 and response.json()["session_id"] == old["session_id"]
        assert _counts(tmp_path, *OWA_TABLES, "owa_executions", "owa_tasks") == [1, 2, 2, 1, 0]


def test_enabled_requires_explicit_selection_and_keeps_one_private_main_after_restart(tmp_path):
    with TestClient(http_app(tmp_path, model=LocalModel(), omniweave_mode="enabled")) as client:
        base = _model_base(client, _authorize(client, tmp_path))
        path = base + "/omniweave/main-session"
        original = {"key": "ordinary-first", "message": "Regular Web turn"}
        ordinary = client.post(base + "/turns", json=original).json()
        assert wait_run(client, base, ordinary["run_id"])["state"] == "succeeded"
        assert client.get(path).json() == {"enabled": True, "session_id": None}
        assert _counts(tmp_path, *OWA_TABLES, "owa_executions") == [0, 0, 0, 0]

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.map(lambda _: _select_main(client, base), range(2))
        assert first == second and first != ordinary["session_id"]
        conflict = client.put(path, json={"session_id": ordinary["session_id"]})
        assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "owa_main_session_exists"
        foreign_agent = client.post("/api/v1/agents", json={"name": "Foreign"}).json()["id"]
        foreign_path = f"/api/v1/agents/{foreign_agent}/omniweave/main-session"
        assert client.put(foreign_path, json={"session_id": first}).status_code == 404
        assert client.get(foreign_path).json()["session_id"] is None
        bound = {"key": "selected", "message": "Selected Web turn", "session_id": first}
        receipt = client.post(base + "/turns", json=bound).json()
        assert wait_run(client, base, receipt["run_id"])["state"] == "succeeded"

    with TestClient(http_app(tmp_path, model=LocalModel(), omniweave_mode="enabled")) as client:
        _authorize(client, tmp_path)
        assert client.get(path).json() == {"enabled": True, "session_id": first}
        assert _select_main(client, base, first) == first
        assert client.post(base + "/turns", json=original).json() == {**ordinary, "duplicate": True}
        assert client.post(base + "/turns", json=bound).json() == {**receipt, "duplicate": True}
        assert _counts(tmp_path, *OWA_TABLES, "owa_executions") == [1, 1, 1, 1]


def test_main_conversation_selection_rechecks_owner_token_before_writing(tmp_path, monkeypatch):
    with TestClient(http_app(tmp_path, model=LocalModel(), omniweave_mode="enabled")) as client:
        agent = _authorize(client, tmp_path)
        runtime = client.app.state.runtime
        authenticate = runtime.authenticate

        async def rotate_after_authentication(token):
            assert await authenticate(token)
            await runtime.rotate_token()
            return True

        path = f"/api/v1/agents/{agent}/omniweave/main-session"
        with monkeypatch.context() as rotation:
            rotation.setattr(runtime, "authenticate", rotate_after_authentication)
            revoked = client.put(path, json={"session_id": None})
        assert revoked.status_code == 401 and revoked.json()["error"]["code"] == "unauthenticated"
        assert _counts(tmp_path, "sessions", *OWA_TABLES) == [0, 0, 0, 0]


def test_web_scope_rejects_cross_agent_and_cross_main_session_references(tmp_path):
    with TestClient(http_app(tmp_path, model=LocalModel(), omniweave_mode="observe")) as client:
        base = _model_base(client, _authorize(client, tmp_path))
        space = client.post("/api/v1/agents", json={"name": "Other Agent"})
        assert space.status_code == 201, space.text
        other = _model_base(client, space.json()["id"])
        first = client.post(base + "/turns", json={"key": "first", "message": "a"}).json()
        second = client.post(base + "/turns", json={"key": "second", "message": "b"}).json()
        payload = {"key": "bound", "message": "bound input", "session_id": first["session_id"]}
        assert client.post(other + "/turns", json=payload).status_code == 404
        assert client.post(base + "/turns", json=payload).status_code == 202
        wrong_session = client.post(base + "/turns", json={**payload, "session_id": second["session_id"]})
        assert wrong_session.status_code == 409 and wrong_session.json()["error"]["code"] == "idempotency_conflict"
        separate = client.post(other + "/turns", json={"key": "bound", "message": "other Agent input"})
        assert separate.status_code == 202 and separate.json()["session_id"] != first["session_id"]
        assert _counts(tmp_path, "inbox", "runs", "work_items", *OWA_TABLES) == [4, 4, 4, 3, 4, 4]


def test_legacy_delegate_runs_but_its_private_session_cannot_become_a_thread(tmp_path):
    class DelegatingModel:
        async def complete(self, messages, tools, settings):
            if any(str(row["content"]).startswith("Delegated task.") for row in messages if row["role"] == "user"):
                return ModelReply("child summary", cost_micro=0)
            if any(row["role"] == "tool" for row in messages):
                return ModelReply("root reply", cost_micro=0)
            assert "delegate_task" in {tool.name for tool in tools}
            return ModelReply(
                "delegating", (Invocation("child", "delegate_task", {"goal": "read only"}),), cost_micro=0
            )

    app = http_app(tmp_path, model=DelegatingModel(), omniweave_mode="observe")
    with TestClient(app) as client:
        base = _model_base(client, _authorize(client, tmp_path))
        receipt = client.post(base + "/turns", json={"key": "root", "message": "delegate", "mode": "agent"})
        assert receipt.status_code == 202, receipt.text
        assert wait_run(client, base, receipt.json()["run_id"])["state"] == "succeeded"
        [(child_session,)] = _rows(tmp_path, "SELECT child_session_id FROM run_delegations")
        denied = client.post(
            base + "/turns", json={"key": "child-injection", "message": "wrong session", "session_id": child_session}
        )
        assert denied.status_code == 404, denied.text
        assert _counts(tmp_path, "inbox", "runs", "run_delegations", *OWA_TABLES) == [1, 2, 1, 1, 1, 1]


def _worker_goal(messages):
    text = str(next(row["content"] for row in reversed(messages) if row["role"] == "user"))
    return json.loads(text.split("\n", 1)[1])["goal"] if text.startswith("OmniWeave Worker.") else None


def _ticket_url(base, receipt):
    return base + f"/omniweave/sessions/{receipt['session_id']}/tickets"


def _wait_tasks(client, base, receipt, expected):
    for _ in range(400):
        response = client.get(_ticket_url(base, receipt), params={"run_id": receipt["run_id"]})
        assert response.status_code == 200, response.text
        view = response.json()
        if {task["key"]: task["state"] for task in view["tasks"]} == expected:
            return view
        time.sleep(0.025)
    raise AssertionError(view)


def test_router_accepts_new_input_while_three_workers_run_and_waits_for_dependencies(tmp_path):
    extras = ("extra-a", "extra-b", "extra-c", "extra-d")
    profiles = {"alpha": "research", "beta": "coding", "gamma": "verification", "join": "documentation"}

    class Model:
        def __init__(self):
            self.release = asyncio.Event()
            self.started = Event()
            self.waiting = set()
            self.completed = set()
            self.workspaces = {}

        async def complete(self, messages, tools, settings):
            names = {tool.name for tool in tools}
            if goal := _worker_goal(messages):
                assert not names
                if goal in profiles:
                    assert any(f"Profile {profiles[goal]} v1." in str(row["content"]) for row in messages)
                self.workspaces[goal] = settings["workspace"]
                if goal == "join":
                    assert self.completed == {"alpha", "beta", "gamma"}
                elif goal in ("alpha", "beta", "gamma"):
                    self.waiting.add(goal)
                    if len(self.waiting) == 3:
                        self.started.set()
                    await self.release.wait()
                    self.completed.add(goal)
                return ModelReply(f"result for {goal}", cost_micro=0)
            text = next(row["content"] for row in reversed(messages) if row["role"] == "user")
            if text == "Plan independent work":
                assert names == {"owa_plan", "owa_status"}
                plans = [json.loads(row["content"]) for row in messages if row["role"] == "tool"]
                if len(plans) == 2:
                    assert plans[0]["ticket_id"] == plans[1]["ticket_id"]
                    assert all(len(plan["tasks"]) == 8 for plan in plans)
                    return ModelReply("Work is queued", cost_micro=0)
                tasks = [{"key": key, "goal": key, "profile": profiles[key]} for key in ("alpha", "beta", "gamma")]
                tasks.append(
                    {
                        "key": "join",
                        "goal": "join",
                        "profile": profiles["join"],
                        "depends_on": ["alpha", "beta", "gamma"],
                    }
                )
                tasks.extend({"key": key, "goal": key, "depends_on": ["join"]} for key in extras)
                return ModelReply(
                    "Plan", (Invocation(f"plan-{len(plans)}", "owa_plan", {"tasks": tasks}),), cost_micro=0
                )
            if text == "Get the progress":
                if messages[-1]["role"] == "tool":
                    current = json.loads(messages[-1]["content"])
                    assert sum(child["state"] == "running" for child in current["tasks"]) == 3
                    return ModelReply("Three Workers are running", cost_micro=0)
                assert "owa_status" in names
                return ModelReply("Read progress", (Invocation("status", "owa_status", {}),), cost_micro=0)
            if text == "Plan overflow":
                if messages[-1]["role"] == "tool":
                    return ModelReply("Overflow queued", cost_micro=0)
                tasks = [{"key": "overflow", "goal": "overflow"}]
                return ModelReply("Plan", (Invocation("overflow", "owa_plan", {"tasks": tasks}),), cost_micro=0)
            assert "owa_plan" not in names and "owa_status" not in names
            return ModelReply("Separate conversation", cost_micro=0)

    model = Model()
    with TestClient(http_app(tmp_path, model=model, omniweave_mode="enabled")) as client:
        agent = _authorize(client, tmp_path)
        base = _model_base(client, agent)
        selected = _select_main(client, base)
        accepted = client.post(
            base + "/turns", json={"key": "plan", "message": "Plan independent work", "session_id": selected}
        )
        assert accepted.status_code == 202, accepted.text
        root = accepted.json()
        assert wait_run(client, base, root["run_id"])["state"] == "succeeded"
        assert model.started.wait(10), model.waiting
        states = {"alpha": "running", "beta": "running", "gamma": "running", "join": "queued"}
        states.update(dict.fromkeys(extras, "queued"))
        ticket = _wait_tasks(client, base, root, states)
        assert {row["key"]: row["profile"] for row in ticket["tasks"]} == {
            **dict.fromkeys(extras, "coding"),
            **profiles,
        }
        assert all(row["profile_version"] == row["goal_version"] == 1 for row in ticket["tasks"])
        child_rows = _rows(
            tmp_path,
            "SELECT r.id, r.session_id FROM runs r JOIN owa_tasks t ON t.run_id=r.id",
        )
        child_runs = {run for run, _ in child_rows}
        child_sessions = {session for _, session in child_rows}
        assert len(child_runs) == len(child_sessions) == 8
        assert {row["id"] for row in client.get(base + "/runs").json()["items"]}.isdisjoint(child_runs)
        assert {row["id"] for row in client.get(base + "/sessions").json()["items"]}.isdisjoint(child_sessions)
        assert client.get(base + f"/runs/{root['run_id']}/delegations").json() == {"items": []}
        child, private_session = child_rows[0]
        assert client.put(base + "/omniweave/main-session", json={"session_id": private_session}).status_code == 404
        for path in (
            f"/runs/{child.upper()}",
            f"/runs/{child}/events",
            f"/sessions/{private_session.replace('-', '')}/messages",
            f"/sessions/{private_session}/context-overview",
        ):
            assert client.get(base + path).status_code == 404, path
        [(private_message,)] = _rows(tmp_path, "SELECT id FROM messages WHERE session_id=?", (private_session,))
        assert client.delete(base + f"/messages/{private_message}").status_code == 404
        assert (
            client.post(
                base + "/turns", json={"key": "private", "message": "wrong session", "session_id": private_session}
            ).status_code
            == 404
        )
        application = client.app.state.runtime.application
        history = client.portal.call(lambda: application.history("events", agent))
        assert not child_runs.intersection(item["run_id"] for item in history["items"])
        assert len([row for row in history["items"] if row["kind"] == "omniweave.planned"]) == 1

        follow_up = client.post(
            base + "/turns",
            json={"key": "follow-up", "message": "Get the progress", "session_id": root["session_id"], "mode": "chat"},
        ).json()
        assert wait_run(client, base, follow_up["run_id"])["state"] == "succeeded"
        assert _wait_tasks(client, base, root, states)["ticket_id"] == ticket["ticket_id"]
        main = client.get(base + f"/sessions/{root['session_id']}/messages").json()["items"]
        assert main[-1]["content"] == "Three Workers are running"
        overflow = client.post(
            base + "/turns", json={"key": "overflow", "message": "Plan overflow", "session_id": root["session_id"]}
        ).json()
        assert wait_run(client, base, overflow["run_id"])["state"] == "succeeded"
        _wait_tasks(client, base, overflow, {"overflow": "queued"})
        other = client.post(base + "/turns", json={"key": "other", "message": "Separate conversation"}).json()
        assert wait_run(client, base, other["run_id"])["state"] == "succeeded"
        assert client.get(_ticket_url(base, other), params={"run_id": other["run_id"]}).status_code == 404
        assert client.get(_ticket_url(base, other), params={"ticket_id": ticket["ticket_id"]}).status_code == 404
        other_agent = client.post("/api/v1/agents", json={"name": "Separate Agent"}).json()["id"]
        foreign_base = f"/api/v1/agents/{other_agent}"
        assert client.get(_ticket_url(foreign_base, root), params={"ticket_id": ticket["ticket_id"]}).status_code == 404

        client.portal.call(model.release.set)
        _wait_tasks(client, base, root, dict.fromkeys(states, "succeeded"))
        _wait_tasks(client, base, overflow, {"overflow": "succeeded"})
        [(overflow_run,)] = _rows(tmp_path, "SELECT run_id FROM owa_tasks WHERE task_key='overflow'")
        child_runs.add(overflow_run)
        assert len(set(model.workspaces.values())) == 9
        assert set(model.workspaces.values()) == {str(tmp_path / "owa-workspaces" / child) for child in child_runs}


@pytest.mark.parametrize("unknown_write", [False, True])
def test_main_goal_update_preserves_ticket_and_fences_previous_work(tmp_path, monkeypatch, unknown_write):
    original = WorkerClient.invoke
    observed = []
    artifact = []

    async def invoke(self, name, arguments, *, operation_id, context):
        if name == "files_write":
            observed.append(operation_id)
            assert unknown_write, "A superseded approval must never reach the executor"
            return ActionResult(Outcome.UNKNOWN, "", Effect.UNKNOWN, error_code="fixture_lost")
        return await original(self, name, arguments, operation_id=operation_id, context=context)

    monkeypatch.setattr(WorkerClient, "invoke", invoke)

    class Model:
        async def complete(self, messages, tools, settings):
            if messages[0]["content"] == REVIEW_PROMPT:
                return ModelReply("No reusable procedure in this test turn", cost_micro=0)
            if str(messages[0]["content"]).startswith("OMNI_CONTEXT_SUMMARY_V1"):
                return ModelReply("Original campus assistant", cost_micro=0)
            goal = _worker_goal(messages)
            last = messages[-1]
            if goal == "approved write":
                return ModelReply(
                    "Propose write",
                    (Invocation("write", "files_write", {"path": "old.txt", "content": "old revision"}),),
                    cost_micro=0,
                )
            if goal == "publish early evidence":
                if last["role"] == "tool":
                    artifact.append(json.loads(last["content"])["artifact_id"])
                    return ModelReply("Published", cost_micro=0)
                draft = {
                    "name": "old",
                    "key": "old",
                    "expected_version": 0,
                    "readers": ["verify"],
                    "text": "old evidence",
                }
                return ModelReply("Publish", (Invocation("publish", "publish_artifact", draft),), cost_micro=0)
            if goal == "verify old evidence":
                return ModelReply("Old verification", cost_micro=0)
            if goal == "revision worker":
                if last["role"] == "tool":
                    assert "outside the authorized reader scope" in last["content"]
                    return ModelReply("Revised verification", cost_micro=0)
                assert "old evidence" not in str(messages)
                return ModelReply(
                    "Probe old reference",
                    (Invocation("old", "read_shared", {"artifact_id": artifact[0]}),),
                    cost_micro=0,
                )
            if goal == "new source":
                return ModelReply("New scoped result", cost_micro=0)
            if last["role"] == "tool":
                receipt = json.loads(last["content"])
                return ModelReply(f"Accepted goal version {receipt['goal_version']}", cost_micro=0)
            assert last["role"] == "user"
            assert {tool.name for tool in tools} == {"owa_plan", "owa_status"}
            if last["content"] == "Original campus assistant":
                tasks = [
                    {"key": "writer", "goal": "approved write", "tools": ["files_write"]},
                    {"key": "source", "goal": "publish early evidence", "artifact_readers": ["verify"]},
                    {"key": "verify", "goal": "verify old evidence", "profile": "verification"},
                ]
            else:
                assert last["content"].startswith("Add ")
                assert any(row["content"] == "Original campus assistant" for row in messages)
                tasks = [
                    {"key": "source", "goal": "new source", "artifact_readers": ["verify"]},
                    {"key": "verify", "goal": "revision worker", "profile": "verification"},
                ]
            return ModelReply("Plan", (Invocation("plan", "owa_plan", {"tasks": tasks}),), cost_micro=0)

    model = Model()
    with TestClient(http_app(tmp_path, model=model, omniweave_mode="dispatch")) as client:
        agent = _authorize(client, tmp_path)
        base = _model_base(client, agent)
        session_id = _select_main(client, base)
        url = base + "/turns"
        root = client.post(
            url, json={"key": "root", "session_id": session_id, "message": "Original campus assistant"}
        ).json()
        ticket = _wait_tasks(
            client, base, root, {"writer": "waiting_approval", "source": "succeeded", "verify": "succeeded"}
        )
        options = base + f"/omniweave/sessions/{session_id}/ticket-options"
        assert client.get(options).json()["items"] == [
            {"ticket_id": ticket["ticket_id"], "goal_version": 1, "title": "Original campus assistant"}
        ]
        [approval] = client.get(base + "/approvals").json()["items"]
        approve_url = base + f"/approvals/{approval['id']}"
        decision = {"allowed": True, "policy_revision": approval["policy_revision"]}
        assert ticket["goal_version"] == 1 and ticket["artifacts"][0]["artifact_id"] == artifact[0]
        if unknown_write:
            assert client.post(approve_url, json=decision).status_code == 200
            _wait_tasks(client, base, root, {"writer": "failed", "source": "succeeded", "verify": "succeeded"})

        update = {"ticket_id": ticket["ticket_id"], "expected_goal_version": 1}
        data = {"session_id": session_id, "goal_update": update}
        outside = client.post(
            url, json={**data, "key": "outside", "session_id": str(uuid4()), "message": "Add outside"}
        )
        assert outside.status_code == 404
        if not unknown_write:
            record = Repositories._record

            def fail_after_cancellation(self, table, values, agent_id=None):
                if table is s.owa_executions:
                    raise BusinessError("fixture_rollback", "Abort the final write", status=503)
                return record(self, table, values, agent_id)

            with monkeypatch.context() as failure:
                failure.setattr(Repositories, "_record", fail_after_cancellation)
                attempted = client.post(url, json={**data, "key": "rollback", "message": "Add after rollback"})
                assert attempted.status_code == 503 and attempted.json()["error"]["code"] == "fixture_rollback"
            assert client.get(base + "/approvals").json()["items"][0]["id"] == approval["id"]
            assert _counts(tmp_path, "owa_tickets", "owa_triggers") == [1, 1]
        entered = Barrier(2)

        def submit(number):
            payload = {**data, "key": f"append-{number}", "message": f"Add feature {number}"}
            entered.wait(timeout=8)
            return payload, client.post(url, json=payload)

        with ThreadPoolExecutor(max_workers=2) as pool:
            submissions = list(pool.map(submit, (1, 2)))
        submissions.sort(key=lambda pair: pair[1].status_code)
        (payload, accepted), (_, rejected) = submissions
        assert accepted.status_code == 202 and rejected.status_code == 409
        assert rejected.json()["error"]["code"] == "revision_conflict"
        revised = accepted.json()
        assert revised["session_id"] == session_id and revised["run_id"] != root["run_id"]
        assert client.post(url, json=payload).json() == {**revised, "duplicate": True}
        changed = client.post(url, json={**payload, "goal_update": {**update, "expected_goal_version": 2}})
        assert changed.status_code == 409 and changed.json()["error"]["code"] == "idempotency_conflict"
        assert wait_run(client, base, revised["run_id"])["state"] == "succeeded"
        states = dict.fromkeys(("source", "verify"), "queued" if unknown_write else "succeeded")
        current = _wait_tasks(client, base, root, states)
        assert current["ticket_id"] == ticket["ticket_id"] and current["run_id"] == revised["run_id"]
        assert current["goal_version"] == 2 and current["artifacts"] == []
        assert {row["goal_version"] for row in current["tasks"]} == {2}
        assert current["prior_work"] == ("needs_review" if unknown_write else None)
        assert _rows(tmp_path, "SELECT root_ticket_id, goal_version FROM owa_tickets ORDER BY goal_version") == [
            (None, 1),
            (ticket["ticket_id"], 2),
        ]
        assert _counts(tmp_path, "owa_triggers", "owa_artifacts") == [2, 1]
        assert client.get(base + "/approvals").json()["items"] == []
        if not unknown_write:
            stale = client.post(approve_url, json=decision)
            assert stale.status_code == 409 and stale.json()["error"]["code"] == "approval_expired"
            assert observed == []
        else:
            assert len(observed) == 1
            review = client.get(base + f"/actions/{observed[0]}/review").json()
            assert review["eligible"]
        messages = client.get(base + f"/sessions/{session_id}/messages").json()["items"]
        assert [row["content"] for row in messages if row["role"] == "user"] == [
            "Original campus assistant",
            payload["message"],
        ]
        assert [
            row["content"] for row in messages if row["role"] == "assistant" and row["run_id"] == revised["run_id"]
        ] == ["Accepted goal version 2"]
    with TestClient(http_app(tmp_path, model=model, omniweave_mode="dispatch")) as client:
        _authorize(client, tmp_path)
        assert _wait_tasks(client, base, root, states)["goal_version"] == 2
        if unknown_write:
            review_url = base + f"/actions/{observed[0]}/review"
            confirmed = client.post(review_url, json=review_request(client.get(review_url).json()))
            assert confirmed.status_code == 200 and confirmed.json()["resolved"], confirmed.text
            completed = _wait_tasks(client, base, root, dict.fromkeys(states, "succeeded"))
            assert completed["prior_work"] is None and len(observed) == 1
        assert client.post(base + f"/runs/{revised['run_id']}/cancel").json() == {"accepted": True}
        cancelled = client.post(
            url,
            json={
                **data,
                "key": "cancelled",
                "message": "Add after cancellation",
                "goal_update": {**update, "expected_goal_version": 2},
            },
        )
        assert cancelled.status_code == 409 and cancelled.json()["error"]["code"] == "owa_ticket_cancelled"


@pytest.mark.parametrize("broken", ["none", "worker", "artifact", "stale", "unchecked"])
def test_router_delivery_commits_once_in_original_session_after_restart(tmp_path, monkeypatch, broken):
    allow_delivery = Event()
    reading_old_delivery = Event()
    release_old_delivery = asyncio.Event()
    claim = LeaseRepositories.claim_runs
    read = ObjectFiles.read

    def claims(self, limit, *, include_owa_deliveries=False):
        return claim(self, limit, include_owa_deliveries=include_owa_deliveries and allow_delivery.is_set())

    async def read_blob(store, blob):
        data = await read(store, blob)
        if broken == "artifact" and allow_delivery.is_set() and b"campus-draft-fixture" in data:
            raise BusinessError("object_integrity_failed", "Injected bad content", status=503)
        if broken == "stale" and allow_delivery.is_set() and not reading_old_delivery.is_set():
            reading_old_delivery.set()
            await release_old_delivery.wait()
        return data

    monkeypatch.setattr(LeaseRepositories, "claim_runs", claims)
    monkeypatch.setattr(ObjectFiles, "read", read_blob)
    artifacts = []

    class Model:
        async def complete(self, messages, tools, settings):
            if messages[0]["content"] == REVIEW_PROMPT:
                return ModelReply("No reusable procedure", cost_micro=0)
            if str(messages[0]["content"]).startswith("OMNI_CONTEXT_SUMMARY_V1"):
                return ModelReply("Prepare a campus assistant", cost_micro=0)
            goal = _worker_goal(messages)
            last = messages[-1]
            if goal == "publish evidence":
                if last["role"] == "tool":
                    artifacts.append(json.loads(last["content"])["artifact_id"])
                    return ModelReply("private-author-draft", cost_micro=0)
                draft = {
                    "key": "patch",
                    "name": "patch",
                    "expected_version": 0,
                    "readers": ["verify"],
                    "text": "campus-draft-fixture",
                }
                return ModelReply("Publish", (Invocation("patch", "publish_artifact", draft),), cost_micro=0)
            if goal == "verify evidence":
                if broken == "unchecked":
                    return ModelReply("Claimed verification without a matching read", cost_micro=0)
                if last["role"] == "tool":
                    assert json.loads(last["content"])["text"] == "campus-draft-fixture"
                    return ModelReply("private-verifier-notes", cost_micro=0)
                return ModelReply(
                    "Verify", (Invocation("read", "read_shared", {"artifact_id": artifacts[-1]}),), cost_micro=0
                )
            if goal == "document validated work":
                return ModelReply("private-documentation-notes", cost_micro=0)
            assert {tool.name for tool in tools} == {"owa_plan", "owa_status"}
            if last["role"] == "tool":
                return ModelReply("Plan accepted", cost_micro=0)
            tasks = [
                {"key": "code", "goal": "publish evidence", "artifact_readers": ["verify"]},
                {
                    "key": "verify",
                    "goal": "verify evidence",
                    "profile": "verification",
                    "depends_on": ["code"],
                    "max_calls": 1 if broken == "worker" else 3,
                },
                {
                    "key": "docs",
                    "goal": "document validated work",
                    "profile": "documentation",
                    "depends_on": ["verify"],
                },
            ]
            return ModelReply("Plan", (Invocation("plan", "owa_plan", {"tasks": tasks}),), cost_micro=0)

    model = Model()
    # Keep the delivery queued. The ordinary Router acknowledgement and Worker Runs still use production dispatch.
    with TestClient(http_app(tmp_path, model=model, omniweave_mode="dispatch")) as client:
        agent = _authorize(client, tmp_path)
        base = _model_base(client, agent)
        session = _select_main(client, base)
        accepted = client.post(
            base + "/turns", json={"session_id": session, "key": "project", "message": "Prepare a campus assistant"}
        ).json()
        expected = (
            {"code": "succeeded", "verify": "failed", "docs": "cancelled"}
            if broken == "worker"
            else dict.fromkeys(("code", "verify", "docs"), "succeeded")
        )
        root = _wait_tasks(client, base, accepted, expected)
        deadline = time.monotonic() + 12
        while (
            not (pending := _rows(tmp_path, "SELECT run_id, message_id FROM owa_deliveries"))
            and time.monotonic() < deadline
        ):
            time.sleep(0.05)
        [(delivery_run, pending_message)] = pending
        assert pending_message is None
        assert _rows(tmp_path, "SELECT state FROM runs WHERE id=?", (delivery_run,)) == [("queued",)]
        if broken == "stale":
            allow_delivery.set()
            assert reading_old_delivery.wait(10)
            try:
                revised = client.post(
                    base + "/turns",
                    json={
                        "session_id": session,
                        "key": "revise-during-delivery",
                        "message": "Prepare a revised campus assistant",
                        "goal_update": {"ticket_id": root["ticket_id"], "expected_goal_version": 1},
                    },
                )
                assert revised.status_code == 202, revised.text
            finally:
                client.portal.call(release_old_delivery.set)
            assert wait_run(client, base, delivery_run, states=("cancelled",))["state"] == "cancelled"
            deadline = time.monotonic() + 22
            while time.monotonic() < deadline:
                ticket = client.get(_ticket_url(base, accepted), params={"run_id": accepted["run_id"]}).json()
                if ticket["delivery"]:
                    break
                time.sleep(0.05)
            assert ticket["goal_version"] == 2 and ticket["ticket_id"] == root["ticket_id"]
            assert ticket["delivery"]["outcome"] == "completed" and ticket["delivery"]["run_id"] != delivery_run
            assert _rows(tmp_path, "SELECT count(*) FROM messages WHERE run_id=?", (delivery_run,)) == [(0,)]
            old_url = base + f"/omniweave/sessions/{session}/artifacts/{artifacts[0]}/content"
            assert client.get(old_url).status_code == 404
            return
    allow_delivery.set()
    with TestClient(http_app(tmp_path, model=LocalModel(), omniweave_mode="off")) as client:
        _authorize(client, tmp_path)
        assert client.get(base + f"/runs/{delivery_run}").json()["state"] == "queued"
        control = client.post(
            base + "/turns", json={"session_id": session, "key": "normal-chat", "message": "Ordinary off-mode turn"}
        ).json()
        assert wait_run(client, base, control["run_id"])["state"] == "succeeded"
    with TestClient(http_app(tmp_path, model=model, omniweave_mode="dispatch")) as client:
        _authorize(client, tmp_path)
        assert wait_run(client, base, delivery_run)["state"] == "succeeded"
        ticket = _wait_tasks(client, base, accepted, expected)
        outcome = "completed" if broken == "none" else "partial"
        delivery = ticket["delivery"]
        assert delivery["outcome"] == outcome and delivery["run_id"] == delivery_run
        item_url = base + f"/omniweave/sessions/{session}/artifacts/{artifacts[0]}/content"
        messages = client.get(base + f"/sessions/{session}/messages").json()["items"]
        [published] = [row for row in messages if row["id"] == delivery["message_id"]]
        assert published["session_id"] == session and "OmniWeave 交付" in published["content"]
        assert "private-" not in published["content"]
        assert [row["content"] for row in messages if row["role"] == "user"] == [
            "Prepare a campus assistant",
            "Ordinary off-mode turn",
        ]
        if broken == "artifact":
            assert published["omniweave_delivery"]["artifacts"] == [] and "无法核对存储内容" in published["content"]
            assert client.get(item_url).status_code == 503
        else:
            assert published["omniweave_delivery"]["artifacts"][0]["artifact_id"] == artifacts[0]
            assert client.get(item_url).content == b"campus-draft-fixture"
        assert client.get(item_url.replace(session, str(uuid4()))).status_code == 404
        assert client.get(item_url, headers={"Authorization": ""}).status_code == 401
        assert _rows(tmp_path, "SELECT id FROM model_calls WHERE run_id=?", (delivery_run,)) == []
    with TestClient(http_app(tmp_path, model=model, omniweave_mode="dispatch")) as client:
        _authorize(client, tmp_path)
        assert _wait_tasks(client, base, accepted, expected)["delivery"] == delivery
        assert _counts(tmp_path, "owa_deliveries") == [1]
        assert _rows(tmp_path, "SELECT count(*) FROM messages WHERE run_id=?", (delivery_run,)) == [(1,)]
        if broken == "none":
            path = base + f"/sessions/{session}/messages"
            [origin] = [
                row
                for row in client.get(path).json()["items"]
                if row["run_id"] == accepted["run_id"] and row["role"] == "user"
            ]
            assert client.delete(base + f"/messages/{origin['id']}").json() == {"erased": True}
            assert all("omniweave_delivery" not in row for row in client.get(path).json()["items"])
            assert client.get(item_url).status_code == 404


def test_router_rejects_cyclic_and_unauthorized_worker_plans_atomically(tmp_path):
    class Model:
        async def complete(self, messages, tools, settings):
            assert not _worker_goal(messages)
            assert {tool.name for tool in tools} == {"owa_plan", "owa_status"}
            if messages[-1]["role"] == "tool":
                replies = [row["content"] for row in messages if row["role"] == "tool"]
                assert "dependency plan" in replies[0] and "current Agent policy" in replies[1]
                assert "Tool arguments do not match its schema" in replies[2]
                assert "Unknown capability" in replies[3]
                assert "selected Worker Profile" in replies[4]
                return ModelReply("No plan was admitted", cost_micro=0)
            tasks = [{"key": "ok", "goal": "task"}, {"key": "unoffered", "goal": "write", "tools": ["files_write"]}]
            cycle = [{"key": "cycle", "goal": "retry", "depends_on": ["cycle"]}]
            overflow = [{"key": f"worker-{index}", "goal": "overflow"} for index in range(9)]
            wrong_profile = [{"key": "research", "goal": "write", "profile": "research", "tools": ["files_write"]}]
            return ModelReply(
                "Plan",
                (
                    Invocation("cyclic", "owa_plan", {"tasks": cycle}),
                    Invocation("denied", "owa_plan", {"tasks": tasks}),
                    Invocation("overflow", "owa_plan", {"tasks": overflow}),
                    Invocation("forged", "files_read", {"path": "private.txt"}),
                    Invocation("wrong-profile", "owa_plan", {"tasks": wrong_profile}),
                ),
                cost_micro=0,
            )

    with TestClient(http_app(tmp_path, model=Model(), omniweave_mode="dispatch")) as client:
        base = _model_base(client, _authorize(client, tmp_path))
        settings = client.get(base + "/settings").json()
        response = client.put(
            base + "/settings",
            json={"revision": settings["revision"], "value": {**settings["value"], "disabled_tools": ["files_write"]}},
        )
        assert response.status_code == 200
        selected = _select_main(client, base)
        receipt = client.post(
            base + "/turns", json={"key": "denied", "message": "Build a plan", "session_id": selected}
        ).json()
        assert wait_run(client, base, receipt["run_id"])["state"] == "succeeded"
        assert _rows(tmp_path, "SELECT plan_sha256 FROM owa_executions") == [(None,)]
        assert _rows(tmp_path, "SELECT id FROM actions WHERE tool='files_read'") == []
        assert _counts(tmp_path, "runs", "work_items", "run_delegations", "owa_tasks", "owa_dependencies") == [
            1,
            1,
            0,
            0,
            0,
        ]


@pytest.mark.parametrize("unknown", [False, True])
def test_worker_file_write_uses_private_workspace_and_existing_owner_approval(tmp_path, monkeypatch, unknown):
    dispatches = []
    private_memory = "The owner's private profile fixture must stay in the main conversation."

    if unknown:
        original = WorkerClient.invoke

        async def lose_receipt(self, name, arguments, *, operation_id, context):
            if name == "files_write":
                dispatches.append(operation_id)
                return ActionResult(Outcome.UNKNOWN, "Test receipt lost", Effect.UNKNOWN, error_code="fixture_lost")
            return await original(self, name, arguments, operation_id=operation_id, context=context)

        monkeypatch.setattr(WorkerClient, "invoke", lose_receipt)

    class Model:
        async def complete(self, messages, tools, settings):
            system_text = messages[0]["content"]
            if goal := _worker_goal(messages):
                assert goal == "write a result"
                assert private_memory not in system_text
                assert {tool.name for tool in tools} == {"files_write"}
                if messages[-1]["role"] == "tool":
                    return ModelReply("Writer result", cost_micro=0)
                write = Invocation("write", "files_write", {"path": "result.txt", "content": "worker-only fixture"})
                return ModelReply("Draft file", (write,), cost_micro=0)
            assert private_memory in system_text
            assert "files_write" not in {tool.name for tool in tools}
            if messages[-1]["role"] == "tool":
                return ModelReply("Writer planned", cost_micro=0)
            tasks = [{"key": "writer", "goal": "write a result", "tools": ["files_write"], "max_calls": 2}]
            if unknown:
                tasks.append({"key": "after", "goal": "must wait for a verified write", "depends_on": ["writer"]})
            return ModelReply("Plan", (Invocation("plan", "owa_plan", {"tasks": tasks}),), cost_micro=0)

    with TestClient(http_app(tmp_path, model=Model(), omniweave_mode="dispatch")) as client:
        base = _model_base(client, _authorize(client, tmp_path))
        owner = client.get(base + "/people/owner").json()["id"]
        profile = client.put(
            base + f"/people/{owner}/documents/profile",
            json={"entry_id": None, "revision": 0, "content": private_memory},
        )
        assert profile.status_code == 200, profile.text
        selected = _select_main(client, base)
        receipt = client.post(
            base + "/turns", json={"key": "write", "message": "Plan a write", "session_id": selected}
        ).json()
        dependencies = {"after": "queued"} if unknown else {}
        _wait_tasks(client, base, receipt, {"writer": "waiting_approval", **dependencies})
        [(worker,)] = _rows(tmp_path, "SELECT run_id FROM owa_tasks WHERE task_key='writer'")
        target = tmp_path / "owa-workspaces" / worker / "result.txt"
        assert target.parent.is_dir() and not target.exists()
        [approval] = client.get(base + "/approvals").json()["items"]
        allowed = client.post(
            base + f"/approvals/{approval['id']}",
            json={"allowed": True, "policy_revision": approval["policy_revision"]},
        )
        assert allowed.status_code == 200, allowed.text
        if unknown:
            _wait_tasks(client, base, receipt, {"writer": "failed", "after": "cancelled"})
            [(action_id, state, effect)] = _rows(
                tmp_path, "SELECT id, state, effect FROM actions WHERE run_id=?", (worker,)
            )
            assert dispatches == [action_id] and state == effect == "unknown"
            review = client.get(base + f"/actions/{action_id}/review")
            assert review.status_code == 200 and review.json()["eligible"]
            assert not target.exists()
            return
        _wait_tasks(client, base, receipt, {"writer": "succeeded"})
        assert target.read_text(encoding="utf-8") == "worker-only fixture"
        assert _rows(tmp_path, "SELECT tool, state, effect FROM actions WHERE run_id=?", (worker,)) == [
            ("files_write", "ok", "applied")
        ]


def test_main_run_cancellation_stops_active_and_waiting_workers(tmp_path):
    class Model:
        started = Event()
        cleaned = Event()

        async def complete(self, messages, tools, settings):
            if goal := _worker_goal(messages):
                assert goal == "wait for cancellation"
                self.started.set()
                try:
                    await asyncio.Future()
                finally:
                    self.cleaned.set()
            if messages[-1]["role"] == "tool":
                return ModelReply("Plan submitted", cost_micro=0)
            tasks = [
                {"key": "running", "goal": "wait for cancellation"},
                {"key": "waiting", "goal": "run only on success", "depends_on": ["running"]},
            ]
            return ModelReply("Plan", (Invocation("plan", "owa_plan", {"tasks": tasks}),), cost_micro=0)

    model = Model()
    with TestClient(http_app(tmp_path, model=model, omniweave_mode="dispatch")) as client:
        base = _model_base(client, _authorize(client, tmp_path))
        selected = _select_main(client, base)
        receipt = client.post(
            base + "/turns", json={"key": "cancel", "message": "Start cancellable work", "session_id": selected}
        ).json()
        assert wait_run(client, base, receipt["run_id"])["state"] == "succeeded"
        assert model.started.wait(10)
        response = client.post(base + f"/runs/{receipt['run_id']}/cancel")
        assert response.status_code == 200 and response.json()["accepted"]
        ticket = _wait_tasks(client, base, receipt, {"running": "cancelled", "waiting": "cancelled"})
        assert ticket["cancel_requested"] and model.cleaned.is_set()
        assert (
            _rows(tmp_path, "SELECT state FROM leases WHERE run_id IN (SELECT run_id FROM owa_tasks) AND state='held'")
            == []
        )


def test_worker_budget_denies_model_dispatch_before_reservation(tmp_path):
    class Model:
        async def complete(self, messages, tools, settings):
            assert not _worker_goal(messages)
            if messages[-1]["role"] == "tool":
                return ModelReply("Plan registered", cost_micro=0)
            task = {"key": "bounded", "goal": "respect the per-worker budget", "budget_micro": 1}
            return ModelReply("Plan", (Invocation("plan", "owa_plan", {"tasks": [task]}),), cost_micro=0)

    with TestClient(http_app(tmp_path, model=Model(), omniweave_mode="dispatch")) as client:
        base = _model_base(client, _authorize(client, tmp_path))
        selected = _select_main(client, base)
        receipt = client.post(
            base + "/turns", json={"key": "small-cap", "message": "Run within budget", "session_id": selected}
        ).json()
        ticket = _wait_tasks(client, base, receipt, {"bounded": "failed"})
        assert ticket["tasks"][0]["error_code"] == "delegation_budget_exceeded"
        assert wait_run(client, base, receipt["run_id"])["state"] == "succeeded"
        assert _rows(tmp_path, "SELECT id FROM model_calls WHERE run_id IN (SELECT run_id FROM owa_tasks)") == []


def test_worker_mandate_expiry_blocks_an_approved_write_and_keeps_immutable_scope(tmp_path, monkeypatch):
    start = datetime.fromisoformat(SystemClock().now())
    advanced = Event()
    monkeypatch.setattr(
        SystemClock,
        "now",
        lambda self: (start + timedelta(seconds=2 * advanced.is_set())).isoformat(timespec="microseconds"),
    )
    authorize = Repositories.authorize_action

    def expire_on_dispatch(unit, agent, action_id):
        if unit.one(s.actions, agent, action_id)["tool"] == "files_write":
            advanced.set()
        return authorize(unit, agent, action_id)

    monkeypatch.setattr(Repositories, "authorize_action", expire_on_dispatch)

    class Model:
        async def complete(self, messages, tools, settings):
            if _worker_goal(messages):
                assert {tool.name for tool in tools} == {"files_write"}
                return ModelReply(
                    "Write",
                    (Invocation("write", "files_write", {"path": "expired.txt", "content": "fixture"}),),
                    cost_micro=0,
                )
            if messages[-1]["role"] == "tool":
                return ModelReply("Plan accepted", cost_micro=0)
            task = {"key": "writer", "goal": "write before deadline", "tools": ["files_write"], "deadline_seconds": 1}
            return ModelReply("Plan", (Invocation("plan", "owa_plan", {"tasks": [task]}),), cost_micro=0)

    with TestClient(http_app(tmp_path, model=Model(), omniweave_mode="enabled")) as client:
        base = _model_base(client, _authorize(client, tmp_path))
        session = _select_main(client, base)
        receipt = client.post(base + "/turns", json={"key": "expiry", "message": "Draft", "session_id": session}).json()
        _wait_tasks(client, base, receipt, {"writer": "waiting_approval"})
        assert wait_run(client, base, receipt["run_id"])["state"] == "succeeded"
        [(worker, root_scope, worker_scope, root_expires, worker_expires, grant)] = _rows(
            tmp_path,
            "SELECT w.run_id, p.main_session_id, w.main_session_id, p.expires_at, w.expires_at, w.tools "
            "FROM owa_mandates w JOIN owa_mandates p ON p.id=w.parent_id",
        )
        assert root_scope == worker_scope == session and worker_expires < root_expires
        assert json.loads(grant)["files_write"]["writes"] is True
        with (
            sqlite_connection(tmp_path / "data/omni.db", read_only=False) as connection,
            pytest.raises(sqlite3.IntegrityError, match="OWA Mandate is immutable"),
        ):
            connection.execute("UPDATE owa_mandates SET expires_at=? WHERE run_id=?", (root_expires, worker))
        approval = client.get(base + "/approvals").json()["items"][0]
        response = client.post(
            base + f"/approvals/{approval['id']}",
            json={"allowed": True, "policy_revision": approval["policy_revision"]},
        )
        assert response.status_code == 200, response.text
        ticket = _wait_tasks(client, base, receipt, {"writer": "failed"})
        assert ticket["tasks"][0]["error_code"] == "owa_mandate_expired"
        assert advanced.is_set()
        assert not (tmp_path / "owa-workspaces" / worker / "expired.txt").exists()
        assert _rows(tmp_path, "SELECT state FROM actions WHERE run_id=? AND started_at IS NOT NULL", (worker,)) == []


def test_restart_reclaims_untouched_worker_and_quarantines_uncertain_attempt(tmp_path, monkeypatch):
    class Model:
        def __init__(self):
            self.workers = []

        async def complete(self, messages, tools, settings):
            if goal := _worker_goal(messages):
                self.workers.append(goal)
                return ModelReply("Recovered Worker result", cost_micro=0)
            if messages[-1]["role"] == "tool":
                return ModelReply("Workers admitted", cost_micro=0)
            tasks = [{"key": key, "goal": key} for key in ("pending", "starting", "uncertain")]
            return ModelReply("Plan", (Invocation("plan", "owa_plan", {"tasks": tasks}),), cost_micro=0)

    model = Model()
    with monkeypatch.context() as old_boot:
        old_boot.setattr(DelegationRepositories, "claim_delegated_runs", lambda self, limit, **options: [])
        with TestClient(http_app(tmp_path, model=model, omniweave_mode="dispatch")) as client:
            agent = _authorize(client, tmp_path)
            base = _model_base(client, agent)
            body = {"key": "restart", "message": "Plan recoverable work", "session_id": _select_main(client, base)}
            receipt = client.post(base + "/turns", json=body).json()
            assert wait_run(client, base, receipt["run_id"])["state"] == "succeeded"
            ticket = _wait_tasks(client, base, receipt, dict.fromkeys(("pending", "starting", "uncertain"), "queued"))
            children = dict(_rows(tmp_path, "SELECT task_key, run_id FROM owa_tasks"))
            database = client.app.state.runtime.application._db

            def imitate_last_operation_of_old_boot(unit):
                assert unit._claim_run(agent, children["pending"]) is not None
                starting = children["starting"]
                claim = unit._claim_run(agent, starting)
                assert claim is not None
                unit.start_claimed_run(agent, starting, claim["claim_token"], claim["lease_fence"])
                unit.transition(agent, starting, "running")
                unit.reserve_call(agent, children["uncertain"], 10000, 1000000, "fixture")

            client.portal.call(database.write, imitate_last_operation_of_old_boot)

    with TestClient(http_app(tmp_path, model=model, omniweave_mode="observe")) as client:
        _authorize(client, tmp_path)
        paused = _wait_tasks(
            client, base, receipt, {"pending": "queued", "starting": "queued", "uncertain": "interrupted"}
        )
        assert paused["ticket_id"] == ticket["ticket_id"] and model.workers == []
        for key in ("pending", "starting"):
            assert _rows(tmp_path, "SELECT state, claim_token FROM work_items WHERE run_id=?", (children[key],)) == [
                ("pending", None)
            ]
        assert _rows(tmp_path, "SELECT state FROM model_calls WHERE run_id=?", (children["uncertain"],)) == [
            ("unknown",)
        ]

    expected = {"pending": "succeeded", "starting": "succeeded", "uncertain": "interrupted"}
    with TestClient(http_app(tmp_path, model=model, omniweave_mode="dispatch")) as client:
        _authorize(client, tmp_path)
        _wait_tasks(client, base, receipt, expected)
        assert client.post(base + "/turns", json=body).json() == {**receipt, "duplicate": True}
        assert sorted(model.workers) == ["pending", "starting"]

    with TestClient(http_app(tmp_path, model=model, omniweave_mode="dispatch")) as client:
        _authorize(client, tmp_path)
        _wait_tasks(client, base, receipt, expected)
        for key in ("pending", "starting"):
            assert _rows(tmp_path, "SELECT state FROM model_calls WHERE run_id=?", (children[key],)) == [("settled",)]
        assert _counts(tmp_path, "owa_tasks", "runs", "work_items") == [3, 4, 4]


@pytest.mark.parametrize("scenario", ["early", "restart", "timeout"])
def test_peer_mailbox_reuses_worker_sessions_and_delivers_once(tmp_path, monkeypatch, scenario):
    restart = scenario == "restart"
    timeout = scenario == "timeout"
    advance = Event()
    if timeout:
        start = datetime.fromisoformat(SystemClock().now())
        monkeypatch.setattr(
            SystemClock,
            "now",
            lambda self: (start + timedelta(minutes=6 * advance.is_set())).isoformat(timespec="microseconds"),
        )
    tick = Repositories.omniweave_mailbox_tick
    run_mailbox = Event()
    if scenario == "early":
        run_mailbox.set()

    def pause_delivery(unit):
        if run_mailbox.is_set():
            tick(unit)

    monkeypatch.setattr(Repositories, "omniweave_mailbox_tick", pause_delivery)

    class Model:
        def __init__(self):
            self.request_committed = Event()
            self.reply_committed = Event()
            self.release_research = asyncio.Event()
            self.release_coding = asyncio.Event()
            self.workspaces = {"coding": set(), "research": set()}

        async def complete(self, messages, tools, settings):
            current = next(row["content"] for row in reversed(messages) if row["role"] == "user")
            names = {tool.name for tool in tools}
            if not current.startswith("OmniWeave Worker."):
                if messages[-1]["role"] == "tool":
                    return ModelReply("Plan queued in the main conversation", cost_micro=0)
                tasks = [
                    {"key": "coding", "goal": "coordinate coding", "peers": ["research", "docs"]},
                    {"key": "research", "goal": "prepare sources", "profile": "research"},
                    {"key": "docs", "goal": "document after reply", "depends_on": ["coding"]},
                ]
                return ModelReply("Plan", (Invocation("plan", "owa_plan", {"tasks": tasks}),), cost_micro=0)
            payload = json.loads(current.split("\n", 1)[1])
            if events := payload.get("events"):
                [event] = events
                if event["type"] == "peer.request":
                    assert names == {"reply_peer"} and event["question"] == "Which source is verified?"
                    assert any("prepare sources" in row["content"] for row in messages[:-1])
                    self.workspaces["research"].add(settings["workspace"])
                    if messages[-1]["role"] == "tool":
                        assert json.loads(messages[-1]["content"])["duplicate"] is True
                        self.reply_committed.set()
                        return ModelReply("Delivered research evidence", cost_micro=0)
                    arguments = {"request_id": event["request_id"], "answer": "verified source v1"}
                    return ModelReply(
                        "Answer",
                        (Invocation("answer", "reply_peer", arguments), Invocation("retry", "reply_peer", arguments)),
                        cost_micro=0,
                    )
                assert event["type"] == "peer.reply"
                if timeout:
                    assert event["disposition"] == "timed_out" and event["answer"] is None
                    self.reply_committed.set()
                else:
                    assert event["answer"] == "verified source v1"
                assert names == {"request_peer"}
                assert any("coordinate coding" in row["content"] for row in messages[:-1])
                self.workspaces["coding"].add(settings["workspace"])
                return ModelReply("Peer outcome recorded", cost_micro=0)
            goal = payload["goal"]
            if goal == "prepare sources":
                self.workspaces["research"].add(settings["workspace"])
                await self.release_research.wait()
                return ModelReply("Research baseline ready", cost_micro=0)
            if goal == "document after reply":
                assert self.reply_committed.is_set()
                return ModelReply("Documented the acknowledged evidence", cost_micro=0)
            assert goal == "coordinate coding" and names == {"request_peer"}
            self.workspaces["coding"].add(settings["workspace"])
            if messages[-1]["role"] == "tool":
                results = [row["content"] for row in messages if row["role"] == "tool"]
                first, retry = map(json.loads, results[1:3])
                assert "waiting cycle" in results[0] and "different request" in results[3]
                assert "outside the authorized plan" in results[4]
                assert first["request_id"] == retry["request_id"] and retry["duplicate"] is True
                self.request_committed.set()
                await self.release_coding.wait()
                return ModelReply("Waiting for verified source", cost_micro=0)
            request = {"key": "source", "target": "research", "question": "Which source is verified?", "blocking": True}
            return ModelReply(
                "Ask",
                (
                    Invocation("cycle", "request_peer", {**request, "key": "cycle", "target": "docs"}),
                    Invocation("ask", "request_peer", request),
                    Invocation("retry", "request_peer", request),
                    Invocation("conflict", "request_peer", {**request, "question": "different question"}),
                    Invocation("outsider", "request_peer", {**request, "key": "outsider", "target": "unknown-worker"}),
                ),
                cost_micro=0,
            )

    model = Model()

    def verify_delivery(client, base, receipt):
        _wait_tasks(client, base, receipt, dict.fromkeys(("coding", "research", "docs"), "succeeded"))
        [(sent, answered, disposition)] = _rows(
            tmp_path, "SELECT request_ack_run_id, reply_ack_run_id, disposition FROM owa_peer_requests"
        )
        assert disposition == ("timed_out" if timeout else "answered") and sent != answered
        assert bool(sent) is not timeout and answered
        assert _rows(
            tmp_path,
            "SELECT t.task_key, count(*), count(DISTINCT r.session_id) FROM owa_task_attempts a JOIN owa_tasks t ON t.id=a.task_id JOIN runs r ON r.id=a.run_id GROUP BY t.task_key ORDER BY t.task_key",
        ) == [
            ("coding", 2, 1),
            ("docs", 1, 1),
            ("research", 1 if timeout else 2, 1),
        ]
        assert all(len(paths) == 1 for paths in model.workspaces.values())
        for private_run in (sent, answered):
            if private_run is None:
                continue
            assert client.get(base + f"/runs/{private_run}").status_code == 404
            assert private_run not in {row["id"] for row in client.get(base + "/runs").json()["items"]}

    with TestClient(http_app(tmp_path, model=model, omniweave_mode="enabled")) as client:
        base = _model_base(client, _authorize(client, tmp_path))
        selected = _select_main(client, base)
        receipt = client.post(
            base + "/turns", json={"key": "peer", "message": "Collaborate", "session_id": selected}
        ).json()
        assert model.request_committed.wait(15)
        client.portal.call(model.release_research.set)
        if scenario == "early":
            assert model.reply_committed.wait(15)
            assert _rows(tmp_path, "SELECT reply_ack_run_id FROM owa_peer_requests") == [(None,)]
        client.portal.call(model.release_coding.set)
        if scenario != "early":
            _wait_tasks(client, base, receipt, {"coding": "waiting_peer", "research": "waiting_peer", "docs": "queued"})
            assert _rows(tmp_path, "SELECT request_ack_run_id FROM owa_peer_requests") == [(None,)]
            if timeout:
                assert wait_run(client, base, receipt["run_id"])["state"] == "succeeded"
                advance.set()
                run_mailbox.set()
                verify_delivery(client, base, receipt)
        else:
            verify_delivery(client, base, receipt)
    if restart:
        run_mailbox.set()
        claim = Repositories.claim_delegated_runs

        def stop_after_ack(unit, limit, **options):
            return [] if options.get("include_owa") else claim(unit, limit, **options)

        with monkeypatch.context() as before_model:
            before_model.setattr(Repositories, "claim_delegated_runs", stop_after_ack)
            with TestClient(http_app(tmp_path, model=model, omniweave_mode="enabled")) as client:
                _authorize(client, tmp_path)
                _wait_tasks(client, base, receipt, {"coding": "waiting_peer", "research": "queued", "docs": "queued"})
                [(claimed,)] = _rows(tmp_path, "SELECT request_ack_run_id FROM owa_peer_requests")
                assert claimed is not None
                assert _rows(tmp_path, "SELECT state FROM runs WHERE id=?", (claimed,)) == [("queued",)]
        with TestClient(http_app(tmp_path, model=model, omniweave_mode="enabled")) as client:
            _authorize(client, tmp_path)
            verify_delivery(client, base, receipt)
            assert _rows(tmp_path, "SELECT request_ack_run_id FROM owa_peer_requests") == [(claimed,)]


def test_peer_messages_cite_only_authorized_artifact_versions(tmp_path):
    def _peer_goal_of(messages):
        text = str(next(row["content"] for row in reversed(messages) if row["role"] == "user"))
        if not text.startswith("OmniWeave Worker."):
            return None
        return json.loads(text.split("\n", 1)[1]).get("goal")

    class Model:
        def __init__(self):
            self.release_source = asyncio.Event()
            self.published = asyncio.Event()
            self.requested = Event()
            self.replied = Event()

        async def complete(self, messages, tools, settings):
            if messages[0]["content"] == REVIEW_PROMPT:
                return ModelReply("No reusable procedure in this test turn", cost_micro=0)
            if str(messages[0]["content"]).startswith("OMNI_CONTEXT_SUMMARY_V1"):
                return ModelReply("Campus peer citation", cost_micro=0)
            goal = _peer_goal_of(messages)
            last = messages[-1]
            if goal == "publish sources":
                if last["role"] == "tool":
                    await self.release_source.wait()
                    return ModelReply("Published sources", cost_micro=0)
                return ModelReply(
                    "Publish",
                    (
                        Invocation(
                            "shared",
                            "publish_artifact",
                            {
                                "name": "sources",
                                "key": "sources",
                                "expected_version": 0,
                                "readers": ["asker"],
                                "text": "shared source v1",
                            },
                        ),
                    ),
                    cost_micro=0,
                )
            if goal == "seal secret":
                if last["role"] == "tool":
                    return ModelReply("Sealed", cost_micro=0)
                return ModelReply(
                    "Seal",
                    (
                        Invocation(
                            "secret",
                            "publish_artifact",
                            {
                                "name": "secret",
                                "key": "secret",
                                "expected_version": 0,
                                "readers": ["third"],
                                "text": "sealed source v1",
                            },
                        ),
                    ),
                    cost_micro=0,
                )
            if goal == "standby":
                return ModelReply("Third standby", cost_micro=0)
            if goal == "ask about sources":
                await self.published.wait()
                if last["role"] == "tool":
                    results = [row["content"] for row in messages if row["role"] == "tool"]
                    assert "outside this Worker's authorized reader scope" in results[0]
                    assert "outside this Worker's authorized reader scope" in results[1]
                    first, retry = map(json.loads, results[2:4])
                    assert first["state"] == "pending" and retry["duplicate"] is True
                    self.requested.set()
                    return ModelReply("Waiting at a safe boundary", cost_micro=0)
                reference = [{"artifact_id": shared_artifact[0], "version": 1}]
                return ModelReply(
                    "Ask",
                    (
                        Invocation(
                            "denied",
                            "request_peer",
                            {
                                "key": "d1",
                                "target": "source",
                                "question": "q",
                                "blocking": True,
                                "references": [{"artifact_id": secret_artifact[0], "version": 1}],
                            },
                        ),
                        Invocation(
                            "stale",
                            "request_peer",
                            {
                                "key": "d2",
                                "target": "source",
                                "question": "q",
                                "blocking": True,
                                "references": [{"artifact_id": shared_artifact[0], "version": 2}],
                            },
                        ),
                        Invocation(
                            "ask",
                            "request_peer",
                            {
                                "key": "ask",
                                "target": "source",
                                "question": "Which source is verified?",
                                "blocking": True,
                                "references": reference,
                            },
                        ),
                        Invocation(
                            "retry",
                            "request_peer",
                            {
                                "key": "ask",
                                "target": "source",
                                "question": "Which source is verified?",
                                "blocking": True,
                                "references": reference,
                            },
                        ),
                    ),
                    cost_micro=0,
                )
            current = str(next(row["content"] for row in reversed(messages) if row["role"] == "user"))
            if not current.startswith("OmniWeave Worker."):
                if last["role"] == "tool":
                    return ModelReply("Plan queued in the main conversation", cost_micro=0)
                assert last["role"] == "user"
                tasks = [
                    {
                        "key": "source",
                        "goal": "publish sources",
                        "profile": "research",
                        "artifact_readers": ["asker"],
                        "max_calls": 8,
                    },
                    {"key": "keeper", "goal": "seal secret", "profile": "documentation", "artifact_readers": ["third"]},
                    {
                        "key": "asker",
                        "goal": "ask about sources",
                        "profile": "research",
                        "peers": ["source"],
                        "max_calls": 8,
                    },
                    {"key": "third", "goal": "standby", "profile": "documentation"},
                ]
                return ModelReply("Plan", (Invocation("plan", "owa_plan", {"tasks": tasks}),), cost_micro=0)
            payload = json.loads(current.split("\n", 1)[1])
            for event in payload.get("events", []):
                if event["type"] == "peer.request":
                    assert {tool.name for tool in tools} == {"reply_peer", "publish_artifact"}
                    assert event["references"] == [] or event["references"][0]["name"] == "sources"
                    if last["role"] == "tool":
                        receipt = json.loads(last["content"])
                        assert receipt["state"] == "answered" and receipt["duplicate"] is False
                        return ModelReply("Citation delivered", cost_micro=0)
                    self.replied.set()
                    return ModelReply(
                        "Answer with citation",
                        (
                            Invocation(
                                "answer",
                                "reply_peer",
                                {
                                    "request_id": event["request_id"],
                                    "answer": "verified source v1",
                                    "references": [{"artifact_id": shared_artifact[0], "version": 1}],
                                },
                            ),
                        ),
                        cost_micro=0,
                    )
                assert event["type"] == "peer.reply"
                assert event["answer"] == "verified source v1"
                [citation] = event["references"]
                assert citation["name"] == "sources" and citation["version"] == 1 and len(citation["sha256"]) == 64
                assert {tool.name for tool in tools} == {"request_peer", "read_shared", "subscribe_artifacts"}
                return ModelReply("Cited evidence recorded", cost_micro=0)
            raise AssertionError("Unexpected worker turn")

    shared_artifact: list[str] = []
    secret_artifact: list[str] = []
    model = Model()

    original_complete = Model.complete

    async def complete_with_capture(self, messages, tools, settings):
        goal = _peer_goal_of(messages)
        if goal == "publish sources" and messages[-1]["role"] == "tool" and not shared_artifact:
            shared_artifact.append(json.loads(messages[-1]["content"])["artifact_id"])
            if secret_artifact:
                self.published.set()
        if goal == "seal secret" and messages[-1]["role"] == "tool" and not secret_artifact:
            secret_artifact.append(json.loads(messages[-1]["content"])["artifact_id"])
            if shared_artifact:
                self.published.set()
        return await original_complete(self, messages, tools, settings)

    Model.complete = complete_with_capture

    with TestClient(http_app(tmp_path, model=model, omniweave_mode="enabled")) as client:
        agent = _authorize(client, tmp_path)
        base = _model_base(client, agent)
        session_id = _select_main(client, base)
        receipt = client.post(
            base + "/turns", json={"key": "cite", "session_id": session_id, "message": "Coordinate citations"}
        ).json()
        assert model.requested.wait(15)
        client.portal.call(model.release_source.set)
        assert model.replied.wait(15)
        _wait_tasks(client, base, receipt, dict.fromkeys(("source", "keeper", "asker", "third"), "succeeded"))
        [row] = _rows(tmp_path, "SELECT request_references, reply_references, disposition FROM owa_peer_requests")
        request_citation, reply_citation, disposition = row
        assert disposition == "answered"
        [request_citation] = json.loads(request_citation) if isinstance(request_citation, str) else request_citation
        [reply_citation] = json.loads(reply_citation) if isinstance(reply_citation, str) else reply_citation
        assert request_citation["artifact_id"] == shared_artifact[0] and request_citation["version"] == 1
        assert request_citation["name"] == "sources" and len(request_citation["sha256"]) == 64
        assert reply_citation == request_citation


def test_goal_revision_retains_accepted_worker_evidence(tmp_path):
    class Model:
        async def complete(self, messages, tools, settings):
            if messages[0]["content"] == REVIEW_PROMPT:
                return ModelReply("No reusable procedure in this test turn", cost_micro=0)
            if str(messages[0]["content"]).startswith("OMNI_CONTEXT_SUMMARY_V1"):
                return ModelReply("Campus assistant baseline", cost_micro=0)
            goal = _worker_goal(messages)
            last = messages[-1]
            if goal == "publish baseline":
                if last["role"] == "tool":
                    return ModelReply("Baseline published", cost_micro=0)
                return ModelReply(
                    "Publish",
                    (
                        Invocation(
                            "publish",
                            "publish_artifact",
                            {
                                "name": "baseline",
                                "key": "baseline",
                                "expected_version": 0,
                                "readers": ["verify"],
                                "text": "baseline evidence v1",
                            },
                        ),
                    ),
                    cost_micro=0,
                )
            if goal == "verify baseline":
                if last["role"] == "tool":
                    return ModelReply("Baseline verified", cost_micro=0)
                return ModelReply(
                    "Check",
                    (Invocation("read", "read_shared", {"artifact_id": baseline_artifact[0]}),),
                    cost_micro=0,
                )
            if goal == "hold for revision":
                if last["role"] == "tool":
                    return ModelReply("Held for revision", cost_micro=0)
                return ModelReply(
                    "Hold",
                    (Invocation("hold", "files_write", {"path": "hold.txt", "content": "held"}),),
                    cost_micro=0,
                )
            if goal == "apply feedback":
                return ModelReply("Feedback applied", cost_micro=0)
            current = str(next(row["content"] for row in reversed(messages) if row["role"] == "user"))
            if not current.startswith("OmniWeave Worker."):
                if current == "Campus assistant baseline":
                    if last["role"] == "tool":
                        return ModelReply("Plan queued in the main conversation", cost_micro=0)
                    tasks = [
                        {
                            "key": "research",
                            "goal": "publish baseline",
                            "profile": "research",
                            "artifact_readers": ["verify"],
                        },
                        {
                            "key": "verify",
                            "goal": "verify baseline",
                            "profile": "verification",
                            "depends_on": ["research"],
                        },
                        {"key": "hold", "goal": "hold for revision", "profile": "coding", "tools": ["files_write"]},
                    ]
                    return ModelReply("Plan", (Invocation("plan", "owa_plan", {"tasks": tasks}),), cost_micro=0)
                assert current == "Add campus feedback", current
                if last["role"] == "tool":
                    results = [row["content"] for row in messages if row["role"] == "tool"]
                    try:
                        receipt = json.loads(results[-1])
                    except json.JSONDecodeError:
                        receipt = {}
                    if "goal_version" in receipt:
                        return ModelReply("Plan queued in the main conversation", cost_micro=0)
                    assert "has not passed acceptance" in results[0]
                    assert "must come from an accepted earlier goal version" in results[1]
                    tasks = [
                        {
                            "key": "research",
                            "goal": "publish baseline",
                            "profile": "research",
                            "reuse": {"task_key": "research", "goal_version": 1},
                        },
                        {
                            "key": "verify",
                            "goal": "verify baseline",
                            "profile": "verification",
                            "reuse": {"task_key": "verify", "goal_version": 1},
                        },
                        {"key": "apply", "goal": "apply feedback", "profile": "research"},
                    ]
                    return ModelReply("Plan", (Invocation("plan", "owa_plan", {"tasks": tasks}),), cost_micro=0)
                return ModelReply(
                    "Plan",
                    (
                        Invocation(
                            "bad-state",
                            "owa_plan",
                            {
                                "tasks": [
                                    {"key": "hold", "goal": "hold", "reuse": {"task_key": "hold", "goal_version": 1}}
                                ]
                            },
                        ),
                        Invocation(
                            "bad-version",
                            "owa_plan",
                            {
                                "tasks": [
                                    {
                                        "key": "research",
                                        "goal": "g",
                                        "reuse": {"task_key": "research", "goal_version": 5},
                                    }
                                ]
                            },
                        ),
                    ),
                    cost_micro=0,
                )
            raise AssertionError("Unexpected worker turn")

    baseline_artifact: list[str] = []
    model = Model()

    original_complete = Model.complete

    async def capture(self, messages, tools, settings):
        goal = _worker_goal(messages)
        if goal == "publish baseline" and messages[-1]["role"] == "tool" and not baseline_artifact:
            baseline_artifact.append(json.loads(messages[-1]["content"])["artifact_id"])
        return await original_complete(self, messages, tools, settings)

    Model.complete = capture

    with TestClient(http_app(tmp_path, model=model, omniweave_mode="dispatch")) as client:
        agent = _authorize(client, tmp_path)
        base = _model_base(client, agent)
        session_id = _select_main(client, base)
        root = client.post(
            base + "/turns", json={"key": "campus", "session_id": session_id, "message": "Campus assistant baseline"}
        ).json()
        ticket = _wait_tasks(
            client, base, root, {"research": "succeeded", "verify": "succeeded", "hold": "waiting_approval"}
        )
        revised = client.post(
            base + "/turns",
            json={
                "key": "revision",
                "session_id": session_id,
                "message": "Add campus feedback",
                "goal_update": {"ticket_id": ticket["ticket_id"], "expected_goal_version": 1},
            },
        ).json()
        assert revised["run_id"] != root["run_id"]
        finished = _wait_tasks(
            client, base, root, {"research": "succeeded", "verify": "succeeded", "apply": "succeeded"}
        )
        assert finished["goal_version"] == 2
        assert dict(_rows(tmp_path, "SELECT task_key, source_goal_version FROM owa_task_reuse")) == {
            "research": 1,
            "verify": 1,
        }
        reused = {row["key"]: row.get("reused_from_version") for row in finished["tasks"]}
        assert reused == {"research": 1, "verify": 1, "apply": None}
        closeout = None
        for _ in range(400):
            closeout = next(
                (
                    row
                    for row in client.get(base + f"/sessions/{session_id}/messages").json()["items"]
                    if row["role"] == "assistant" and "OmniWeave 交付 · 第 2 版" in row["content"]
                ),
                None,
            )
            if closeout is not None:
                break
            time.sleep(0.025)
        assert closeout is not None
        assert "沿用第 1 版已验收结果" in closeout["content"]
        assert "沿用证据（修订前版本产物）" in closeout["content"]
        assert baseline_artifact[0] in closeout["content"]
        links = closeout["omniweave_delivery"]
        assert links["outcome"] == "completed" and links["goal_version"] == 2
        assert [item["artifact_id"] for item in links["artifacts"]] == [baseline_artifact[0]]
        download = client.get(base + f"/omniweave/sessions/{session_id}/artifacts/{baseline_artifact[0]}/content")
        assert download.status_code == 200 and download.text == "baseline evidence v1"
    with TestClient(http_app(tmp_path, model=LocalModel(), omniweave_mode="enabled")) as client:
        _authorize(client, tmp_path)
        view = client.get(_ticket_url(base, root), params={"run_id": root["run_id"]}).json()
        assert view["goal_version"] == 2
        assert [row[0] for row in _rows(tmp_path, "SELECT task_key FROM owa_task_reuse ORDER BY task_key")] == [
            "research",
            "verify",
        ]
