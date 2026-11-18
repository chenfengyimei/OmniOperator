"""Owner commands bind exact fact versions and review receipts; public metadata excludes request keys and text."""

import time
import uuid

from fastapi.testclient import TestClient

from omni.adapters.persistence.migration_config import head as migration_head
from omni.application.maintenance import MaintenanceService
from omni.composition.http import http_app


def connect(client, home):
    client.headers["Authorization"] = "Bearer " + (home / "run/owner.token").read_text()
    agent = client.get("/api/v1/agents").json()["items"][0]["id"]
    base = f"/api/v1/agents/{agent}"
    facts = [
        client.post(base + "/memory", json={"content": "exact-private-organization-body"}).json() for _ in range(2)
    ]
    return agent, base, facts


def prepared(client, target):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(target)
        assert response.status_code == 200, response.text
        result = response.json()
        if result["state"] != "queued":
            return result
        time.sleep(0.01)
    raise AssertionError("Isolated local proposal did not settle")


def body(facts, key="private-organization-request-key"):
    return {"key": key, "sources": [{"memory_id": row["id"], "revision": row["revision"]} for row in facts]}


def review(batch, candidate, decision="apply"):
    return {
        "revision": candidate["revision"],
        "fingerprint": candidate["fingerprint"],
        "epoch": batch["history_epoch"],
        "decision": decision,
    }


def test_owner_api_proposal_apply_cas_undo_and_private_metadata(tmp_path):
    with TestClient(http_app(tmp_path)) as client:
        assert client.get(f"/api/v1/agents/{uuid.uuid4()}/memory/organizations").status_code == 401
        _, base, facts = connect(client, tmp_path)
        organizations = base + "/memory/organizations"
        response = client.post(organizations, json=body(facts))
        assert response.status_code == 202, response.text
        job = response.json()
        assert "private-organization-request-key" not in response.text
        assert client.post(organizations, json=body(list(reversed(facts)))).json()["id"] == job["id"]
        target = organizations + "/" + job["id"]
        batch = prepared(client, target)
        assert batch["state"] == "ready" and batch["budget_micro"] == 0
        page = client.get(organizations + "?limit=1")
        assert page.status_code == 200 and page.json()["items"][0]["id"] == job["id"]
        assert "exact-private-organization-body" not in page.text
        assert "private-organization-request-key" not in page.text
        assert client.get(organizations + f"?after={page.json()['next_after']}").json()["items"] == []
        for query in ("?limit=51", "?after=-1", "?after=9223372036854775808"):
            assert client.get(organizations + query).status_code == 422
        candidate = batch["candidates"][0]
        action = organizations + "/candidates/" + candidate["id"] + "/decision"
        assert client.post(action, json={**review(batch, candidate), "fingerprint": "0" * 64}).status_code == 409
        assert client.post(action, json={**review(batch, candidate), "extra": True}).status_code == 422
        applied = client.post(action, json=review(batch, candidate))
        assert applied.status_code == 200, applied.text
        assert applied.json()["candidates"][0]["state"] == "applied"
        assert client.post(action, json=review(batch, candidate)).status_code == 409
        assert len(client.get(base + "/memory?q=organization").json()["items"]) == 1
        assert len(client.get(base + "/memory").json()["items"]) == 2
        updated = applied.json()
        undone = client.post(action, json=review(updated, updated["candidates"][0], "undo"))
        assert undone.status_code == 200, undone.text
        assert undone.json()["candidates"][0]["state"] == "revoked"
        assert len(client.get(base + "/memory?q=organization").json()["items"]) == 2
        other = client.portal.call(client.app.state.runtime.application._db.write, lambda u: u.create_agent("Other"))[
            "id"
        ]
        wrong = f"/api/v1/agents/{other}/memory/organizations"
        assert client.get(wrong + "/" + job["id"]).status_code == 404
        assert (
            client.post(
                wrong + "/candidates/" + candidate["id"] + "/decision", json=review(batch, candidate)
            ).status_code
            == 404
        )
        assert client.get(wrong).json()["items"] == []
        assert client.get("/api/v1/version").json()["schema_version"] == migration_head()


def test_queued_cancel_is_durable_and_strict_inputs_cannot_authorize_new_scope(tmp_path, monkeypatch):
    async def paused(self):
        return 0

    monkeypatch.setattr(MaintenanceService, "tick", paused)
    with TestClient(http_app(tmp_path)) as client:
        _, base, facts = connect(client, tmp_path)
        organizations = base + "/memory/organizations"
        source = body(facts)
        for changed in ({"budget_micro": 1}, {"sources": source["sources"][:1]}, {"key": ""}):
            assert client.post(organizations, json={**source, **changed}).status_code == 422
        bad_revision = [{**row, "revision": True} for row in source["sources"]]
        assert client.post(organizations, json={**source, "sources": bad_revision}).status_code == 422
        queued = client.post(organizations, json=source).json()
        assert queued["state"] == "queued"
        cancel = organizations + "/" + queued["id"] + "/cancel"
        assert client.post(cancel, json={"revision": True}).status_code == 422
        assert client.post(cancel, json={"revision": 2}).status_code == 409
        cancelled = client.post(cancel, json={"revision": queued["revision"]})
        assert cancelled.status_code == 200 and cancelled.json()["state"] == "cancelled"
        assert client.post(cancel, json={"revision": queued["revision"]}).json()["state"] == "cancelled"
        replay = client.post(organizations, json=source)
        assert replay.json()["id"] == queued["id"] and replay.json()["state"] == "cancelled"


def test_forgetting_source_removes_proposal_body_and_old_review_cannot_reapply(tmp_path):
    with TestClient(http_app(tmp_path)) as client:
        _, base, facts = connect(client, tmp_path)
        organizations = base + "/memory/organizations"
        queued = client.post(organizations, json=body(facts)).json()
        target = organizations + "/" + queued["id"]
        batch = prepared(client, target)
        candidate = batch["candidates"][0]
        assert "exact-private-organization-body" in str(batch["sources"])
        erased = client.delete(base + "/memory/" + facts[0]["id"] + "?revision=1")
        assert erased.status_code == 200
        invalid = client.get(target)
        assert invalid.json()["state"] == "invalidated" and not invalid.json()["valid"]
        assert "exact-private-organization-body" not in invalid.text
        decision = client.post(
            organizations + "/candidates/" + candidate["id"] + "/decision", json=review(batch, candidate)
        )
        assert decision.status_code == 409
