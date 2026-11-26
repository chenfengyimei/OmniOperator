"""OWA observes admissions at the production owner Web entry point."""

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from fastapi.testclient import TestClient
from omni_test_support.v2.http import LocalModel
from omni_test_support.v2.mcp_flow import wait_run

from omni.adapters.persistence.delegation_repository import DelegationRepositories
from omni.adapters.persistence.omniweave_repository import OmniWeaveRepositories
from omni.adapters.persistence.sqlite_access import sqlite_connection
from omni.adapters.workers.client import WorkerClient
from omni.composition.http import http_app
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
        response = client.post(base + "/turns", json={**new, "key": "same-session"})
        assert response.status_code == 202 and response.json()["session_id"] == old["session_id"]
        assert _counts(tmp_path, *OWA_TABLES, "owa_executions", "owa_tasks") == [1, 2, 2, 1, 0]


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
                tasks = [{"key": key, "goal": key} for key in ("alpha", "beta", "gamma")]
                tasks.append({"key": "join", "goal": "join", "depends_on": ["alpha", "beta", "gamma"]})
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
        accepted = client.post(base + "/turns", json={"key": "plan", "message": "Plan independent work"})
        assert accepted.status_code == 202, accepted.text
        root = accepted.json()
        assert wait_run(client, base, root["run_id"])["state"] == "succeeded"
        assert model.started.wait(10), model.waiting
        states = {"alpha": "running", "beta": "running", "gamma": "running", "join": "queued"}
        states.update(dict.fromkeys(extras, "queued"))
        ticket = _wait_tasks(client, base, root, states)
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
                return ModelReply("No plan was admitted", cost_micro=0)
            tasks = [{"key": "ok", "goal": "task"}, {"key": "unoffered", "goal": "write", "tools": ["files_write"]}]
            cycle = [{"key": "cycle", "goal": "retry", "depends_on": ["cycle"]}]
            overflow = [{"key": f"worker-{index}", "goal": "overflow"} for index in range(9)]
            return ModelReply(
                "Plan",
                (
                    Invocation("cyclic", "owa_plan", {"tasks": cycle}),
                    Invocation("denied", "owa_plan", {"tasks": tasks}),
                    Invocation("overflow", "owa_plan", {"tasks": overflow}),
                    Invocation("forged", "files_read", {"path": "private.txt"}),
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
        receipt = client.post(base + "/turns", json={"key": "denied", "message": "Build a plan"}).json()
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
        receipt = client.post(base + "/turns", json={"key": "write", "message": "Plan a write"}).json()
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
        receipt = client.post(base + "/turns", json={"key": "cancel", "message": "Start cancellable work"}).json()
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
        receipt = client.post(base + "/turns", json={"key": "small-cap", "message": "Run within budget"}).json()
        ticket = _wait_tasks(client, base, receipt, {"bounded": "failed"})
        assert ticket["tasks"][0]["error_code"] == "delegation_budget_exceeded"
        assert wait_run(client, base, receipt["run_id"])["state"] == "succeeded"
        assert _rows(tmp_path, "SELECT id FROM model_calls WHERE run_id IN (SELECT run_id FROM owa_tasks)") == []


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
            body = {"key": "restart", "message": "Plan recoverable work"}
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
