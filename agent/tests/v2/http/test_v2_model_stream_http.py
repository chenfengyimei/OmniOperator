"""Owner HTTP serves guarded in-memory previews; disconnecting clients never own model generation."""

import time
import uuid

from fastapi.testclient import TestClient
from omni_test_support.v2.model_stream import StreamingModel

from omni.composition.http import http_app


def test_live_output_auth_scope_cache_and_final_calibration(tmp_path):
    model = StreamingModel()
    model.release.clear()
    with TestClient(http_app(tmp_path, model=model, model_streamer=model)) as client:
        try:
            assert client.get(f"/api/v1/agents/{uuid.uuid4()}/runs/{uuid.uuid4()}/live-output").status_code == 401
            client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text()
            agent = client.get("/api/v1/agents").json()["items"][0]["id"]
            base = f"/api/v1/agents/{agent}"
            settings = client.get(base + "/settings").json()
            config = client.get(base + "/configurations/" + settings["value"]["model_profile_id"]).json()
            edited = client.put(
                base + "/configurations/" + config["id"],
                json={
                    "name": config["name"],
                    "enabled": True,
                    "revision": config["revision"],
                    "value": {**config["value"], "model": "fixture"},
                },
            )
            assert edited.status_code == 200, edited.text
            receipt = client.post(base + "/turns", json={"key": "http-stream", "message": "request"}).json()
            live_url = base + f"/runs/{receipt['run_id']}/live-output"
            deadline = time.monotonic() + 10
            while True:
                live = client.get(live_url)
                if live.json()["state"] == "streaming" and live.json()["text"]:
                    break
                assert time.monotonic() < deadline
                time.sleep(0.01)
            assert live.headers["cache-control"] == "no-store"
            assert live.json()["text"] == "partial中文<unsafe>" and "credential" not in live.text
            assert client.get(f"/api/v1/agents/{uuid.uuid4()}/runs/{receipt['run_id']}/live-output").status_code == 404
            # Polling stops without cancelling or replaying the model call.
            assert model.send_count == 1
            client.portal.call(model.release.set)
            while client.get(base + f"/runs/{receipt['run_id']}").json()["state"] != "succeeded":
                assert time.monotonic() < deadline
                time.sleep(0.01)
            final = client.get(live_url).json()
            assert final["state"] == "unavailable" and final["text"] == "" and final["persisted_state"] == "succeeded"
            rows = client.get(base + f"/sessions/{receipt['session_id']}/messages").json()["items"]
            assert rows[-1]["content"] == "partial中文<unsafe> complete" and len(rows) == 2
        finally:
            client.portal.call(model.release.set)
