"""Real V2 APIs and lifecycle with injected terminal processes; no shell or native input."""

import asyncio
import json
import threading
import uuid

import pytest
from fastapi.testclient import TestClient
from test_v2_terminals_http import owner
from v2_terminal_helpers import FixtureTerminalBackend, open_request
from v2_terminal_review_helpers import request, unknown

from omni.adapters.persistence.terminal_review_repository import TerminalReviewRepositories
from omni.adapters.platform.runtime.paths import InstanceLock
from omni.composition.http import http_app
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError


def test_owner_review_requires_native_quiescence_and_cannot_replay_unknown_input(tmp_path):
    home, workspace = tmp_path / "home", tmp_path / "workspace"
    workspace.mkdir()
    backend = FixtureTerminalBackend()
    with TestClient(http_app(home, terminal_backend=backend)) as client:
        assert client.get(f"/api/v1/agents/{uuid.uuid4()}/terminal-reviews").status_code == 401
        agent, base, db = owner(client, home, workspace)
        operation = client.portal.call(unknown, db, agent, workspace)
        path = base + "/terminal-reviews/" + operation["id"]
        detail = client.get(path).json()
        assert detail["reviewable"] and detail["operation"]["state"] == "unknown"
        session = client.post(base + "/terminals", json=open_request()).json()
        data = request(detail)
        assert not client.get(path).json()["reviewable"]
        assert client.post(path, json=data).json()["error"]["code"] == "terminal_review_busy"
        current = client.get(base + "/terminals/" + session["id"]).json()
        assert (
            client.post(
                base + "/terminals/" + session["id"] + "/close",
                json={"operation_id": str(uuid.uuid4()), "revision": current["revision"]},
            ).status_code
            == 200
        )
        response = client.post(path, json=data)
        assert response.status_code == 201, response.text
        assert response.json()["revision"] == 1
        assert client.post(path, json=data).json()["replayed"]
        assert client.get(base + "/terminal-operations/" + operation["id"]).json()["operation"]["state"] == "unknown"
        rows = client.get(base + "/terminal-reviews?limit=1").json()
        assert rows["items"][0]["latest_matches_source"] and rows["next_after"] == operation["id"]
        history = client.get(path + "/history").json()
        assert len(history["items"]) == 1
        review_id = history["items"][0]["id"]
        erased = client.request("DELETE", path + "/history/" + review_id + "/evidence", json={})
        assert erased.status_code == 200 and erased.json()["evidence"] is None
        assert client.post(path, json=data).json()["evidence"] is None
        other = client.portal.call(db.write, lambda u: u.create_agent("Other"))["id"]
        assert client.get(path.replace(agent, other)).status_code == 404
        assert client.post(path.replace(agent, other), json=data).status_code == 404
        assert all(not process.writes for process in backend.processes)
        audit = client.portal.call(db.read, lambda u: u.query("audit", agent))
        assert "private-review-evidence" not in json.dumps(audit)
    for file in home.rglob("*.jsonl"):
        assert "private-review-evidence" not in file.read_text(encoding="utf-8")


def test_cancelled_review_commit_keeps_database_and_instance_ownership_until_finished(tmp_path, monkeypatch):
    started, release = threading.Event(), threading.Event()
    original = TerminalReviewRepositories.terminal_review_commit

    def blocked(self, *args):
        started.set()
        assert release.wait(10)
        return original(self, *args)

    monkeypatch.setattr(TerminalReviewRepositories, "terminal_review_commit", blocked)

    async def scenario():
        instance = await open_instance(tmp_path / "home", capabilities=(), terminal_backend=FixtureTerminalBackend())
        agent = (await instance.application.query("agents", ""))[0]["id"]
        operation = await unknown(instance._database, agent, tmp_path / "workspace")
        detail = await instance.terminal_reviews.get(agent, operation["id"])
        caller = asyncio.create_task(instance.terminal_reviews.record(agent, operation["id"], request(detail)))
        try:
            async with asyncio.timeout(5):
                while not started.is_set():
                    await asyncio.sleep(0.01)
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert not (await instance.close(0.02))["complete"]
            assert not instance._database.metrics["closed"]
            with pytest.raises(BusinessError):
                InstanceLock(instance.paths).acquire()
            release.set()
            async with asyncio.timeout(5):
                while instance.terminal_reviews.health()["pending"]:
                    await asyncio.sleep(0.01)
            assert len((await instance.terminal_reviews.history(agent, operation["id"]))["items"]) == 1
            assert (await instance.close(5))["complete"]
        finally:
            release.set()
            await asyncio.gather(caller, return_exceptions=True)
            await instance.close(5)

    asyncio.run(scenario())
