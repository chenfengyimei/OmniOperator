"""Owner API routing configuration, admission identity and scoped persistent reports."""

import time
import uuid

from fastapi.testclient import TestClient
from omni_test_support.v2.routing import Model

from omni.composition.http import http_app
from omni.domain.routing import POLICY_DEFAULT


def test_policy_and_run_reports_are_authenticated_scoped_and_no_store(tmp_path):
    model = Model()
    with TestClient(http_app(tmp_path, model=model)) as client:
        assert client.get(f"/api/v1/agents/{uuid.uuid4()}/routing").status_code == 401
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text()
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        base = f"/api/v1/agents/{agent}"
        small = client.post(
            base + "/configurations",
            json={"kind": "model", "name": "Small", "enabled": True, "value": {"model": "small"}},
        ).json()
        policy = {**POLICY_DEFAULT, "chat_model_id": small["id"], "chat_reservation_micro": 123}
        assert client.put(base + "/routing", json={"revision": 0, "value": policy}).status_code == 200
        assert client.put(base + "/routing", json={"revision": 0, "value": policy}).status_code == 409
        assert (
            client.post(base + "/turns", json={"key": "bad", "message": "hello", "mode": "unknown"}).status_code == 422
        )
        receipt = client.post(base + "/turns", json={"key": "http", "message": "hello", "mode": "auto"})
        assert receipt.status_code == 202, receipt.text
        receipt = receipt.json()
        deadline = time.monotonic() + 10
        while client.get(base + f"/runs/{receipt['run_id']}").json()["state"] not in {"succeeded", "failed"}:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        report = client.get(base + f"/runs/{receipt['run_id']}/routing")
        assert report.status_code == 200 and report.headers["cache-control"] == "no-store"
        data = report.json()
        assert data["route"]["mode"] == "chat" and data["route"]["model"] == "small"
        assert data["route"]["reservation_micro"] == 123 and data["routing_model_calls"] == 0
        assert client.get(f"/api/v1/agents/{uuid.uuid4()}/runs/{receipt['run_id']}/routing").status_code == 404
        assert (
            client.post(base + "/turns", json={"key": "http", "message": "hello", "mode": "agent"}).status_code == 409
        )
        assert len(model.requests) == 1
