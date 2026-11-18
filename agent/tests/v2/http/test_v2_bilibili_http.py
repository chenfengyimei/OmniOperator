"""Authenticated, bounded Bilibili setup/preparation APIs; no real account or publication."""

import uuid

from fastapi.testclient import TestClient
from omni_test_support.v2.bilibili import FORM, URL
from omni_test_support.v2.delivery_http import seed

from omni.composition.http import http_app


def test_profile_cas_scope_body_limits_and_prepare_without_sending(tmp_path):
    with TestClient(http_app(tmp_path)) as client:
        assert client.get(f"/api/v1/agents/{uuid.uuid4()}/bilibili/publications").status_code == 401
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text()
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        base = f"/api/v1/agents/{agent}"
        config = client.post(
            base + "/configurations",
            json={
                "kind": "channel",
                "name": "Bili fixture",
                "enabled": False,
                "value": {"driver": "bilibili", "account": "456", "credential_ref": "fixture/bili"},
            },
        ).json()
        path = base + "/bilibili/profiles/" + config["id"]
        empty = client.get(path).json()
        assert empty["revision"] == 0 and not empty["enabled"]
        value = {"enabled": True, "forms": {"comment": FORM}, "headless": True, "revision": 0}
        saved = client.put(path, json=value)
        assert saved.status_code == 200 and saved.json()["revision"] == 1 and saved.json()["headless"]
        assert client.put(path, json=value).status_code == 409
        assert client.put(path, json={**value, "revision": 1, "headless": "yes"}).status_code == 422
        assert client.put(path, content=b'{"canary":"' + b"x" * 65536 + b'"}').status_code == 413
        assert (
            client.post(base + "/bilibili/read", json={"config_id": config["id"], "operation": "account"}).status_code
            == 409
        )
        assert client.get(f"/api/v1/agents/{uuid.uuid4()}/bilibili/profiles/{config['id']}").status_code == 404
        assert client.get(base + "/bilibili/publications?limit=101").status_code == 422
        # No model/account is invoked by editing a reviewed profile.
        assert client.get(base + "/bilibili/publications").json()["items"] == []


def test_publication_http_exposes_real_target_and_requires_standard_delivery_approval(tmp_path):
    from omni_test_support.v2.http import LocalModel

    with TestClient(http_app(tmp_path, model=LocalModel())) as client:
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text()
        base, _, message, _ = seed(client)
        config = client.post(
            base + "/configurations",
            json={
                "kind": "channel",
                "name": "Bili fixture",
                "enabled": True,
                "value": {"driver": "bilibili", "account": "456", "credential_ref": "fixture/bili"},
            },
        ).json()
        assert (
            client.put(
                base + "/bilibili/profiles/" + config["id"],
                json={"enabled": True, "forms": {"comment": FORM}, "revision": 0},
            ).status_code
            == 200
        )
        body = {
            "config_id": config["id"],
            "message_id": message["id"],
            "kind": "comment",
            "target": URL,
            "mentions": [],
            "key": "publish-key",
        }
        response = client.post(base + "/bilibili/publications", json=body)
        assert response.status_code == 201
        publication = response.json()
        delivery = publication["deliveries"][0]
        assert delivery["state"] == "awaiting_approval" and delivery["publication"]["target"] == URL
        assert client.get(base + "/bilibili/publications/" + publication["id"]).json()["text"] == message["content"]
        assert (
            client.post(base + "/bilibili/publications", json={**body, "key": "another-key"}).json()["id"]
            == publication["id"]
        )
        assert len(client.get(base + "/deliveries").json()["items"]) == 1
        assert (
            client.post(
                base + "/bilibili/publications", json={**body, "target": URL + "?token=COOKIE_CANARY"}
            ).status_code
            == 422
        )
        assert "COOKIE_CANARY" not in client.get("/api/v1/system/logs").text
