"""Owner document APIs, idempotent creation, revision provenance and bounded input."""

import uuid

from fastapi.testclient import TestClient

from omni.composition.http import http_app


def test_document_http_revision_import_rebuild_erasure_and_request_limits(tmp_path):
    with TestClient(http_app(tmp_path)) as client:
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text()
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        base = f"/api/v1/agents/{agent}"
        creation = str(uuid.uuid4())
        body = {"document_id": creation, "title": "Source", "content": "first exact evidence", "revision": 0}
        response = client.post(base + "/documents", json=body)
        assert response.status_code == 201, response.text
        original = response.json()
        assert original["id"] == creation
        repeated = client.post(base + "/documents", json=body)
        assert repeated.json()["revision_id"] == original["revision_id"]
        assert client.post(base + "/documents", json={**body, "content": "different"}).status_code == 409
        target = base + "/documents/" + creation
        changed = client.put(target, json={"title": "Edited", "content": "new exact evidence", "revision": 1})
        assert changed.status_code == 200 and changed.json()["revision"] == 2
        assert client.put(target, json={"title": "stale", "content": "lost update", "revision": 1}).status_code == 409
        assert client.get(target, params={"revision_id": original["revision_id"]}).json()["content"] == body["content"]
        assert len(client.get(target + "/revisions").json()["items"]) == 2
        search = client.get(base + "/documents/search", params={"q": "evidence"}).json()
        assert (
            search["mode"] == "full_text"
            and search["items"][0]["document_revision_id"] == changed.json()["revision_id"]
        )
        assert client.post(target + "/indexes", json={"kind": "full_text"}).json()["state"] == "ready"
        assert client.post(target + "/indexes", json={"kind": "vector"}).status_code == 503
        assert not client.get(target + "/indexes").json()["vector"]["available"]
        memory = client.post(
            base + "/memory",
            json={
                "content": "anchored fact",
                "document_revision_id": original["revision_id"],
                "quote": "exact evidence",
            },
        )
        assert memory.status_code == 201, memory.text
        assert (
            client.post(
                base + "/memory",
                json={"content": "forged", "document_revision_id": original["revision_id"], "quote": "forged quote"},
            ).status_code
            == 422
        )
        upload = client.post(base + "/objects", content=b"imported source", headers={"Content-Type": "text/plain"})
        assert upload.status_code == 201, upload.text
        imported = client.post(base + "/documents/import", json={"title": "Imported", "object_id": upload.json()["id"]})
        assert (
            imported.status_code == 201
            and client.get(base + "/documents/" + imported.json()["id"]).json()["content"] == "imported source"
        )
        oversized = client.post(base + "/documents", content=b"{" + b" " * 1048576)
        assert oversized.status_code == 413
        invalid = client.post(
            base + "/documents", json={"title": "Invalid", "content": "private-canary", "revision": True}
        )
        assert invalid.status_code == 422 and "private-canary" not in invalid.text
        assert client.get(base + "/documents?limit=201").status_code == 422
        assert client.get(f"/api/v1/agents/{uuid.uuid4()}/documents/{creation}").status_code == 404
        assert client.delete(target + "?revision=2").status_code == 200
        assert client.delete(target + "?revision=2").status_code == 200
        assert client.get(target).status_code == 410
        assert all(
            row["object_id"] is None and row["redacted_at"] for row in client.get(target + "/revisions").json()["items"]
        )
        assert client.get(target + "/indexes").json()["erasure_job"]["state"] == "done"
        assert not any(row["id"] == memory.json()["id"] for row in client.get(base + "/memory").json()["items"])
