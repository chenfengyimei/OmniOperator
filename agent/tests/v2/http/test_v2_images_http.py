"""Authenticated image upload, settings and atomic image-only chat over the production HTTP adapter."""

import time
import uuid

from fastapi.testclient import TestClient
from omni_test_support.v2.images import Model, png

from omni.composition.http import http_app


def test_upload_configure_attach_report_and_download_are_scoped(tmp_path):
    model = Model()
    with TestClient(http_app(tmp_path, model=model)) as client:
        assert client.post(f"/api/v1/agents/{uuid.uuid4()}/images", content=png()).status_code == 401
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text()
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        base = f"/api/v1/agents/{agent}"
        invalid = client.post(base + "/images", content=b"<svg>private</svg>", headers={"Content-Type": "image/png"})
        assert invalid.status_code == 422 and "private" not in invalid.text
        image = client.post(base + "/images", content=png(), headers={"Content-Type": "text/plain"})
        assert image.status_code == 201, image.text
        image = image.json()
        settings = client.get(base + "/settings").json()
        config = client.get(base + "/configurations/" + settings["value"]["model_profile_id"]).json()
        edited = client.put(
            base + "/configurations/" + config["id"],
            json={
                "name": config["name"],
                "enabled": True,
                "revision": config["revision"],
                "value": {**config["value"], "model": "gpt-4o"},
                "image_policy": {"protocol": "openai_low", "per_call_limit": 2, "run_budget": 4},
            },
        )
        assert edited.status_code == 200, edited.text
        edited = edited.json()
        assert edited["image_policy"]["protocol"] == "openai_low"
        history = client.get(base + f"/configurations/{config['id']}/revisions")
        assert history.status_code == 200 and history.json()["items"][-1]["image_policy"]["run_budget"] == 4
        receipt = client.post(base + "/turns", json={"key": "image", "image_ids": [image["object_id"]]})
        assert receipt.status_code == 202, receipt.text
        receipt = receipt.json()
        deadline = time.monotonic() + 10
        while client.get(base + f"/runs/{receipt['run_id']}").json()["state"] not in {"succeeded", "failed"}:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        report = client.get(base + f"/runs/{receipt['run_id']}/images")
        assert report.status_code == 200 and report.json()["used"] == 1
        assert report.headers["cache-control"] == "no-store"
        assert client.get(f"/api/v1/agents/{uuid.uuid4()}/runs/{receipt['run_id']}/images").status_code == 404
        assert client.post(base + f"/images/{image['object_id']}/prepare").status_code == 200
        assert client.post(base + "/turns", json={"key": "image", "message": "changed"}).status_code == 409
        rows = client.get(base + f"/sessions/{receipt['session_id']}/messages").json()["items"]
        assert rows[0]["images"][0]["object_id"] == image["object_id"] and len(model.requests) == 1
