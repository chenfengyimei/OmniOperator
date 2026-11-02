"""Authenticated V2 HTTP use cases use a fresh database and injected model."""

import time

from fastapi.testclient import TestClient

from omni.composition.http import http_app
from omni.domain.contracts import ModelReply


class LocalModel:
    async def complete(self, messages, tools, settings):
        return ModelReply("verified reply", cost_micro=0)


def test_auth_chat_and_revision_contract(tmp_path):
    with TestClient(http_app(tmp_path, model=LocalModel())) as client:
        assert client.get("/health/ready").status_code == 200
        assert client.get("/api/v1/agents").status_code == 401
        token = (tmp_path / "run/owner.token").read_text()
        client.headers["Authorization"] = "Bearer " + token
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        base = f"/api/v1/agents/{agent}"
        assert client.get("/").status_code == 200
        assert client.get("/assets/v2/main.js").status_code == 200
        cfg = client.get(base + "/settings").json()
        cfg["value"]["model"] = "fake"
        assert client.put(base + "/settings", json=cfg).status_code == 200
        assert client.put(base + "/settings", json=cfg).status_code == 409
        receipt = client.post(base + "/turns", json={"key": "test", "message": "hello"}).json()
        duplicate = client.post(base + "/turns", json={"key": "test", "message": "hello"}).json()
        assert duplicate["duplicate"] and receipt["run_id"] == duplicate["run_id"]
        for _ in range(100):
            run = client.get(base + "/runs/" + receipt["run_id"]).json()
            if run["state"] == "succeeded":
                break
            time.sleep(0.02)
        assert run["state"] == "succeeded", run
        messages = client.get(base + "/sessions/" + receipt["session_id"] + "/messages").json()["items"]
        assert [m["content"] for m in messages] == ["hello", "verified reply"]
        events = client.get(base + "/runs/" + receipt["run_id"] + "/events").json()
        assert events["next_after"] > 0
        assert (
            client.get(base + "/runs/" + receipt["run_id"] + "/events", params={"after": events["next_after"]}).json()[
                "items"
            ]
            == []
        )
        assert client.get("/api/chat").status_code == 404
        assert client.get("/api/v1/agents", headers={"Origin": "https://untrusted.example"}).status_code == 403
        assert "verified reply" not in client.get("/api/v1/system/logs").text
