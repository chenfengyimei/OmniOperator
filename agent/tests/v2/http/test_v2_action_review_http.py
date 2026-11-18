"""Owner-only reconciliation preserves raw execution truth, isolation and independent uncertainty."""

import uuid

from fastapi.testclient import TestClient
from omni_test_support.v2.v2_action_review_helpers import review_request, seed_unknown

from omni.composition.http import http_app


def opened_owner(client, home):
    client.headers["Authorization"] = "Bearer " + (home / "run/owner.token").read_text()
    agent = client.get("/api/v1/agents").json()["items"][0]["id"]
    db = client.app.state.runtime.application._db
    seed = client.portal.call(db.write, lambda u: seed_unknown(u, agent))
    base = f"/api/v1/agents/{agent}"
    return agent, base, seed, db


def test_owner_reconciliation_keeps_unknown_and_cas_receipt_without_dispatch(tmp_path):
    home = tmp_path / "home"
    with TestClient(http_app(home)) as client:
        assert client.get(f"/api/v1/agents/{uuid.uuid4()}/action-reviews").status_code == 401
        agent, base, seed, db = opened_owner(client, home)
        url = base + f"/actions/{seed['action_id']}/review"
        inspected = client.get(url)
        assert inspected.status_code == 200, inspected.text
        detail = inspected.json()
        assert detail["eligible"] and detail["state"] == detail["effect"] == "unknown"
        assert "private result canary" not in inspected.text and "argument canary" not in inspected.text
        assert detail["proof"]["source"] == {"kind": "builtin_unversioned"}
        before = client.portal.call(db.read, lambda u: u.action(agent, seed["action_id"]))
        request = review_request(detail, "applied", "Owner verified the target independently")
        response = client.post(url, json=request)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["review_valid"] and result["resolved"] and result["resolution"] == "applied"
        assert result["state"] == result["effect"] == "unknown"
        assert client.portal.call(db.read, lambda u: u.action(agent, seed["action_id"])) == before
        replayed = client.post(url, json=request)
        assert replayed.status_code == 200 and replayed.json()["replayed"]
        assert client.post(url, json={**request, "evidence": "Different intent"}).status_code == 409
        assert client.post(url, json={**request, "review_id": str(uuid.uuid4())}).status_code == 409
        history = client.get(base + f"/actions/{seed['action_id']}/reviews?limit=1").json()
        assert len(history["items"]) == 1 and history["next_after"] is not None
        assert (
            client.get(base + f"/actions/{seed['action_id']}/reviews?after={history['next_after']}").json()["items"]
            == []
        )
        assert client.post(url + "/retry", json={}).status_code == 404


def test_unknown_action_api_bounds_scope_and_source_erasure_reject_stale_form(tmp_path):
    home = tmp_path / "home"
    with TestClient(http_app(home)) as client:
        _agent, base, seed, db = opened_owner(client, home)
        url = base + f"/actions/{seed['action_id']}/review"
        detail = client.get(url).json()
        request = review_request(detail, evidence="owner evidence canary")
        assert client.post(url, json=request).status_code == 200
        second = client.get(url).json()
        late = review_request(second)
        assert client.delete(base + f"/messages/{seed['message_id']}").status_code == 200
        response = client.get(url)
        assert response.status_code == 200, response.text
        after = response.json()
        assert not after["review_valid"] and not after["resolved"]
        assert "owner evidence canary" not in response.text
        assert client.post(url, json=late).status_code == 409
        fresh = review_request(after, "still_unknown", "The remaining evidence is inconclusive")
        assert client.post(url, json=fresh).status_code == 200
        assert not client.get(url).json()["resolved"]
        for query in ("?limit=0", "?limit=51", "?after=-1", "?after=9223372036854775808"):
            assert client.get(base + "/action-reviews" + query).status_code == 422
        for extra in (
            {"revision": True},
            {"policy": "wrong"},
            {"history_epoch": "bad"},
            {"evidence": " "},
            {"extra": 1},
        ):
            assert client.post(url, json={**review_request(client.get(url).json()), **extra}).status_code == 422
        other = client.portal.call(db.write, lambda u: u.create_agent("Other"))["id"]
        wrong = f"/api/v1/agents/{other}/actions/{seed['action_id']}"
        assert client.get(wrong + "/review").status_code == 404
        assert client.get(wrong + "/reviews").status_code == 404
        assert client.post(wrong + "/review", json=fresh).status_code == 404


def test_review_blocks_actual_run_owner_even_after_database_terminal(tmp_path):
    import asyncio

    home = tmp_path / "home"
    with TestClient(http_app(home)) as client:
        _, base, seed, _ = opened_owner(client, home)
        url = base + f"/actions/{seed['action_id']}/review"
        request = review_request(client.get(url).json())
        app = client.app.state.runtime.application

        async def hold():
            task = asyncio.create_task(asyncio.Event().wait())
            app._active[seed["run_id"]] = (seed["session_id"], task)
            return task

        task = client.portal.call(hold)
        try:
            detail = client.get(url).json()
            assert not detail["eligible"] and "action_run_active" in detail["protected_reasons"]
            assert client.post(url, json=request).status_code == 409
        finally:

            async def release():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                app._active.pop(seed["run_id"], None)

            client.portal.call(release)
        assert client.post(url, json=request).status_code == 200
