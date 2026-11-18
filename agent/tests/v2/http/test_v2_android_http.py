"""Owner HTTP exercises the real managed Android chain against an isolated server."""

import asyncio
import uuid

from fastapi.testclient import TestClient
from omni_test_support.v2.android import SERIAL, fake_server

from omni.composition.http import http_app


def test_http_device_scope_strict_consent_capture_and_revocation(tmp_path):
    async def scenario():
        async with fake_server() as (fake, port):

            def exercise():
                with TestClient(http_app(tmp_path)) as client:
                    assert client.get("/api/v1/agents/" + str(uuid.uuid4()) + "/android/grants").status_code == 401
                    client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text()
                    agent = client.get("/api/v1/agents").json()["items"][0]["id"]
                    base = f"/api/v1/agents/{agent}"
                    config = client.post(
                        base + "/configurations",
                        json={
                            "kind": "channel",
                            "name": "Android HTTP fixture",
                            "enabled": True,
                            "value": {"driver": "android", "endpoint": f"http://127.0.0.1:{port}", "account": SERIAL},
                        },
                    )
                    assert config.status_code == 201, config.text
                    cfg = config.json()
                    assert (
                        client.post(base + "/android/probe", json={"config_id": cfg["id"], "consent": 1}).status_code
                        == 422
                    )
                    assert not fake.services
                    probe = client.post(base + "/android/probe", json={"config_id": cfg["id"], "consent": True})
                    assert probe.status_code == 200, probe.text
                    grant = client.post(
                        base + "/android/grants", json={"probe_id": probe.json()["probe_id"], "consent": True}
                    )
                    assert grant.status_code == 201, grant.text
                    identity = grant.json()["id"]
                    helper_url = base + f"/android/grants/{identity}/unicode"
                    empty = client.get(helper_url)
                    assert empty.status_code == 200 and empty.json()["revision"] == 0
                    helper = {"enabled": False, "credential_ref": "", "apk_sha256": "", "certificate_sha256": ""}
                    assert client.put(helper_url, json={"revision": True, "profile": helper}).status_code == 409
                    saved = client.put(helper_url, json={"revision": 0, "profile": helper})
                    assert saved.status_code == 200 and saved.json()["revision"] == 1
                    assert client.put(helper_url, json={"revision": 0, "profile": helper}).status_code == 409
                    assert (
                        client.put(
                            helper_url, json={"revision": 1, "profile": {**helper, "pairing_key": "do-not-store-here"}}
                        ).status_code
                        == 422
                    )
                    assert client.get(base + "/android/environments").json()["items"] == []
                    assert (
                        client.post(
                            base + f"/android/environments/{uuid.uuid4()}/restore",
                            json={
                                "consent": 1,
                                "session_id": str(uuid.uuid4()),
                                "request_id": str(uuid.uuid4()),
                                "revision": 1,
                            },
                        ).status_code
                        == 422
                    )
                    apps = client.post(base + f"/android/grants/{identity}/apps", json={"consent": True})
                    assert apps.status_code == 200 and apps.json()["items"] == sorted(fake.packages)
                    assert (
                        client.post(
                            base + f"/android/grants/{identity}/apps", json={"consent": True, "command": "arbitrary"}
                        ).status_code
                        == 422
                    )
                    assert client.get(base + "/android/access").json()["items"] == []
                    assert (
                        client.post(
                            base + f"/android/grants/{identity}/observe", json={"consent": True, "serial": "injected"}
                        ).status_code
                        == 422
                    )
                    view = client.post(base + f"/android/grants/{identity}/observe", json={"consent": True})
                    assert view.status_code == 200 and "private-password" not in view.text
                    assert client.post(base + "/android/actions", content=b"x" * 4097).status_code == 413
                    assert (
                        client.delete(base + "/android/observations/" + view.json()["observation_id"]).status_code
                        == 200
                    )
                    assert (
                        client.post(base + f"/android/grants/{identity}/revoke", json={"revision": True}).status_code
                        == 409
                    )
                    assert (
                        client.post(base + f"/android/grants/{identity}/revoke", json={"revision": 1}).status_code
                        == 200
                    )
                    assert (
                        client.post(base + f"/android/grants/{identity}/observe", json={"consent": True}).status_code
                        >= 400
                    )
                    assert not fake.writes

            await asyncio.to_thread(exercise)

    asyncio.run(scenario())
