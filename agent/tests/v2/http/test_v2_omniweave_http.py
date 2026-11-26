"""OWA observes admissions at the production owner Web entry point."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from omni_test_support.v2.http import LocalModel
from omni_test_support.v2.mcp_flow import wait_run

from omni.adapters.persistence.omniweave_repository import OmniWeaveRepositories
from omni.adapters.persistence.sqlite_access import sqlite_connection
from omni.composition.http import http_app
from omni.domain.contracts import BusinessError, Invocation, ModelReply


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
    with TestClient(http_app(tmp_path, model=LocalModel(), omniweave_mode="observe")) as client:
        _authorize(client, tmp_path)
        assert client.post(base + "/turns", json=new).json() == {**observed, "duplicate": True}
        assert _rows(tmp_path, "SELECT ticket_id, id FROM owa_triggers") == ids
        response = client.post(base + "/turns", json={**new, "key": "same-session"})
        assert response.status_code == 202 and response.json()["session_id"] == old["session_id"]
        assert _counts(tmp_path, *OWA_TABLES) == [1, 2, 2]


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
