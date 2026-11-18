"""Owner-only durable jobs, bounded strict inputs, private attempts and real ready receipts."""

import time
import uuid

from fastapi.testclient import TestClient

from omni.adapters.persistence.migration_config import head as migration_head
from omni.application.maintenance import MaintenanceService
from omni.composition.http import http_app


def test_maintenance_http_strict_admission_paging_scope_and_revision_cancel(tmp_path, monkeypatch):
    async def idle(self):
        return 0

    monkeypatch.setattr(MaintenanceService, "tick", idle)
    with TestClient(http_app(tmp_path)) as client:
        assert client.get("/api/v1/agents/" + str(uuid.uuid4()) + "/maintenance").status_code == 401
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text()
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        base = f"/api/v1/agents/{agent}"
        source = client.post(base + "/documents", json={"title": "Source", "content": "evidence"}).json()
        body = {
            "kind": "document_index_rebuild",
            "key": "private-idempotency-key",
            "document_id": source["id"],
            "revision": 1,
        }
        response = client.post(base + "/maintenance", json=body)
        assert response.status_code == 202, response.text
        job = response.json()
        assert job["state"] == "queued" and "private-idempotency-key" not in response.text
        assert client.post(base + "/maintenance", json=body).json()["id"] == job["id"]
        assert client.post(base + "/maintenance", json={**body, "revision": 2}).status_code == 409
        for changed in ({"extra": True}, {"revision": True}, {"key": ""}, {"kind": "vector"}):
            assert client.post(base + "/maintenance", json={**body, **changed}).status_code == 422
        for query in ("?after=-1", "?limit=101", "?after=9223372036854775808"):
            assert client.get(base + "/maintenance" + query).status_code == 422
        page = client.get(base + "/maintenance?limit=1").json()
        assert [row["id"] for row in page["items"]] == [job["id"]]
        assert client.get(base + f"/maintenance?after={page['next_after']}").json()["items"] == []
        target = base + "/maintenance/" + job["id"]
        assert client.post(target + "/cancel", json={"revision": True}).status_code == 422
        assert client.post(target + "/cancel", json={"revision": 2}).status_code == 409
        cancelled = client.post(target + "/cancel", json={"revision": 1}).json()
        assert cancelled["state"] == "cancelled" and cancelled["attempt"] is None
        assert client.post(target + "/cancel", json={"revision": 1}).json()["state"] == "cancelled"

        async def seed_other():
            return await client.app.state.runtime.application._db.write(lambda u: u.create_agent("Other"))

        other = client.portal.call(seed_other)["id"]
        assert client.get(f"/api/v1/agents/{other}/maintenance/" + job["id"]).status_code == 404
        assert client.get(f"/api/v1/agents/{other}/maintenance").json()["items"] == []


def test_maintenance_http_reconnect_reads_persisted_receipt_and_hides_claim_secrets(tmp_path):
    with TestClient(http_app(tmp_path)) as client:
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text()
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        base = f"/api/v1/agents/{agent}"
        response = client.post(base + "/documents", json={"title": "Source", "content": "durable UI source"})
        assert response.status_code == 201, response.text
        source = response.json()
        response = client.post(
            base + "/maintenance",
            json={"kind": "document_index_rebuild", "key": "receipt", "document_id": source["id"], "revision": 1},
        )
        assert response.status_code == 202, response.text
        target = base + "/maintenance/" + response.json()["id"]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            final = client.get(target).json()
            if final["state"] in {"succeeded", "failed"}:
                break
            time.sleep(0.01)
        assert final["state"] == "succeeded"
        assert final["attempt"]["build_id"] == final["result_build_id"]
        assert not {"token", "boot_id", "fence", "lease_until"}.intersection(final["attempt"])
        version = client.get("/api/v1/version").json()
        assert version["schema_version"] == migration_head()
        assert client.get("/health/ready").status_code == 200
