"""Configuration API boundaries, credential separation and immutable run provenance."""

import uuid

from fastapi.testclient import TestClient

from omni.composition.http import http_app


def test_configuration_http_import_limits_revisions_and_passive_diagnosis(tmp_path):
    with TestClient(http_app(tmp_path)) as client:
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text()
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        base = f"/api/v1/agents/{agent}"
        cfg = client.get(base + "/settings").json()
        model_id = cfg["value"]["model_profile_id"]
        template = client.get(base + "/configurations/template?kind=mcp").json()
        assert template["value"]["transport"] == "stdio"
        document = {
            **template,
            "name": "Remote",
            "value": {"transport": "streamable_http", "endpoint": "https://mcp.example.test"},
        }
        preview = client.post(base + "/configurations/preview", json=document)
        assert preview.status_code == 200
        assert client.get(base + "/configurations?kind=mcp").json()["items"] == []
        payload = {key: preview.json()[key] for key in ("kind", "name", "enabled", "value")}
        created = client.post(base + "/configurations", json=payload)
        assert created.status_code == 201
        identity = created.json()["id"]
        target = base + "/configurations/" + identity
        diagnosis = client.get(target + "/diagnosis").json()
        assert diagnosis["state"] == "adapter_not_connected" and not diagnosis["available"]
        updated = client.put(
            target, json={"name": "Remote edited", "enabled": False, "value": payload["value"], "revision": 1}
        )
        assert updated.status_code == 200 and updated.json()["revision"] == 2
        assert (
            client.put(target, json={"name": "stale", "enabled": False, "value": {}, "revision": 1}).status_code == 409
        )
        assert client.delete(target + "?revision=2").status_code == 200
        assert client.get(target).status_code == 410
        assert len(client.get(target + "/revisions").json()["items"]) == 2
        assert client.get(base + "/configurations?kind=model&limit=201").status_code == 422
        other = str(uuid.uuid4())
        assert client.get(f"/api/v1/agents/{other}/configurations/{model_id}").status_code == 404
        for value in (
            {"api_key": "secret-canary"},
            {"base_url": "https://user:secret-canary@example.test"},
            {"base_url": "https://example.test?token=secret-canary"},
        ):
            response = client.post(
                base + "/configurations", json={"kind": "model", "name": "Invalid", "enabled": True, "value": value}
            )
            assert response.status_code == 422 and "secret-canary" not in response.text
        response = client.put(
            base + "/settings", json={"revision": True, "value": cfg["value"], "secret": "secret-canary"}
        )
        assert response.status_code == 422 and "secret-canary" not in response.text
        for suffix in ("/settings", "/configurations/preview"):
            response = client.request(
                "PUT" if suffix == "/settings" else "POST", base + suffix, content=b"{" + b" " * 262144
            )
            assert response.status_code == 413
        response = client.post(base + "/configurations/preview", content=b'{"bad":"\xff"}')
        assert response.status_code == 422
        receipt = client.post(base + "/turns", json={"key": "provenance", "message": "no model request"}).json()
        saved = client.get(base + f"/runs/{receipt['run_id']}/configuration").json()
        assert saved["model_profile_id"] == model_id and saved["model_revision_id"]
        assert len(client.get(base + "/settings/revisions").json()["items"]) == 1
