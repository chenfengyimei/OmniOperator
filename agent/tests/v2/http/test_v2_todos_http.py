"""The owner-only task-plan view exposes the same scoped state as the production model control."""

import time
import uuid

from fastapi.testclient import TestClient

from omni.composition.http import http_app
from omni.domain.contracts import Invocation, ModelReply


def test_owner_reads_live_session_plan_and_other_spaces_cannot_read_it(tmp_path):
    class Planner:
        calls = 0

        async def complete(self, messages, tools, settings):
            self.calls += 1
            if self.calls == 1:
                return ModelReply(
                    "plan",
                    (
                        Invocation(
                            "plan",
                            "todo_list",
                            {"revision": 0, "todos": [{"id": "one", "content": "HTTP task", "status": "in_progress"}]},
                        ),
                    ),
                    cost_micro=7,
                )
            return ModelReply("Tracked", cost_micro=7)

    with TestClient(http_app(tmp_path, model=Planner())) as client:
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text()
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        base = f"/api/v1/agents/{agent}"
        settings = client.get(base + "/settings").json()
        profile = client.get(base + "/configurations/" + settings["value"]["model_profile_id"]).json()
        saved = client.put(
            base + "/configurations/" + profile["id"],
            json={
                "name": profile["name"],
                "enabled": True,
                "revision": profile["revision"],
                "value": {**profile["value"], "model": "fake"},
            },
        )
        assert saved.status_code == 200
        response = client.post(base + "/turns", json={"key": "todo", "message": "track work", "mode": "agent"})
        assert response.status_code == 202
        receipt = response.json()
        for _ in range(100):
            run = client.get(base + "/runs/" + receipt["run_id"]).json()
            if run["state"] in {"succeeded", "failed"}:
                break
            time.sleep(0.05)
        assert run["state"] == "succeeded"
        target = base + "/sessions/" + receipt["session_id"] + "/todos"
        state = client.get(target)
        assert state.status_code == 200 and state.json()["revision"] == 1
        assert state.json()["todos"] == [{"id": "one", "content": "HTTP task", "status": "in_progress"}]
        assert client.get(f"/api/v1/agents/{uuid.uuid4()}/sessions/{receipt['session_id']}/todos").status_code == 404
        assert client.put(target, json={"revision": 1, "todos": []}).status_code == 405
        client.headers.pop("Authorization")
        assert client.get(target).status_code == 401
