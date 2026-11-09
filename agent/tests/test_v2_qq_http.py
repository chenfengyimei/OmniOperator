"""Owner-only QQ grants, CAS edits and scoped diagnostics with no account or network."""

import uuid

from fastapi.testclient import TestClient

from omni.composition.http import http_app


def test_qq_http_grants_are_owner_only_scoped_and_revisioned(tmp_path):
    with TestClient(http_app(tmp_path)) as client:
        assert client.get(f"/api/v1/agents/{uuid.uuid4()}/qq/bindings").status_code == 401
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text()
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        base = f"/api/v1/agents/{agent}"
        channel = client.post(
            base + "/configurations",
            json={
                "kind": "channel",
                "name": "QQ fixture",
                "enabled": False,
                "value": {"driver": "qq", "account": "456", "endpoint": "ws://127.0.0.1:3001"},
            },
        ).json()
        value = {
            "config_id": channel["id"],
            "scene": "private",
            "peer_id": "123",
            "enabled": False,
            "persona": "QQ independent persona",
            "cooldown_s": 10,
        }
        created = client.post(base + "/qq/bindings", json=value)
        assert created.status_code == 201
        binding = created.json()
        target = base + "/qq/bindings/" + binding["id"]
        edited = client.put(target, json={**value, "revision": 1, "persona": "edited"})
        assert edited.status_code == 200 and edited.json()["revision"] == 2
        assert client.put(target, json={**value, "revision": 1}).status_code == 409
        assert client.put(target, json={**value, "revision": 2, "peer_id": "999"}).status_code == 409
        assert client.post(base + "/qq/bindings", json={**value, "cooldown_s": True}).status_code == 422
        assert client.get(base + "/qq/bindings?limit=101").status_code == 422
        rows = client.get(base + "/qq/bindings").json()["items"]
        assert len(rows) == 1 and rows[0]["persona"] == "edited"
        foreign = f"/api/v1/agents/{uuid.uuid4()}"
        assert client.get(foreign + "/qq/bindings").status_code == 404
        assert client.put(foreign + "/qq/bindings/" + binding["id"], json={**value, "revision": 2}).status_code == 404
        status = client.get(base + "/qq/status").json()
        assert status["health"]["listeners"] == 0 and status["connections"] == []
