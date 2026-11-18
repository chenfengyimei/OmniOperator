"""Real owner HTTP for review-bound text skill versions and scoped exports."""

import uuid

from fastapi.testclient import TestClient
from omni_test_support.v2.routing import Model
from omni_test_support.v2.skills import markdown

from omni.composition.http import http_app


def test_skill_api_revision_review_history_exports_and_scope(tmp_path):
    with TestClient(http_app(tmp_path, model=Model())) as client:
        assert client.get(f"/api/v1/agents/{uuid.uuid4()}/skills").status_code == 401
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text()
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        base = f"/api/v1/agents/{agent}/skills"
        created = client.post(base, json={"content": markdown()})
        assert created.status_code == 201, created.text
        row = created.json()
        assert row["state"] == "disabled" and not client.get(base + "/available").json()["items"]
        target = base + "/" + row["id"]
        wrong = {
            "revision": row["revision"],
            "state": "enabled",
            "revision_id": str(uuid.uuid4()),
            "manifest_sha256": row["manifest_sha256"],
        }
        assert client.put(target + "/state", json=wrong).status_code == 409
        enabled = client.put(target + "/state", json={**wrong, "revision_id": row["revision_id"]})
        assert enabled.status_code == 200 and enabled.json()["state"] == "enabled"
        assert len(client.get(base + "/available").json()["items"]) == 1
        content = client.get(target)
        assert content.status_code == 200 and content.headers["cache-control"] == "no-store"
        assert "Procedure" in content.json()["content"]
        assert client.get(target, params={"path": "../.env"}).status_code == 422
        assert client.get(f"/api/v1/agents/{uuid.uuid4()}/skills/{row['id']}").status_code == 404
        exported = client.post(target + "/export").json()
        payload = client.get(f"/api/v1/agents/{agent}/objects/{exported['object_id']}/content")
        assert payload.status_code == 200 and payload.content.startswith(b"PK")
        enabled = enabled.json()
        edited = client.post(
            base,
            json={
                "content": markdown(text="A new explicitly reviewed procedure."),
                "skill_id": row["id"],
                "revision": enabled["revision"],
            },
        )
        assert edited.status_code == 201 and edited.json()["state"] == "disabled"
        assert len(client.get(target + "/history").json()["items"]) == 2
        assert (
            client.post(
                base, json={"content": markdown(), "skill_id": row["id"], "revision": enabled["revision"]}
            ).status_code
            == 409
        )
        edited = edited.json()
        rolled = client.post(
            target + "/rollback", json={"revision": edited["revision"], "revision_id": row["revision_id"]}
        )
        assert rolled.status_code == 200 and rolled.json()["content_revision"] == 3
