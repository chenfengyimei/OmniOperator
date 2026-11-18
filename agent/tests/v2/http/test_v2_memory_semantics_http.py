"""Real owner HTTP, scoped semantic sources and explicit related-fact decisions."""

import uuid

from fastapi.testclient import TestClient
from omni_test_support.v2.memory_organization import decision, request
from omni_test_support.v2.memory_semantics import NoModel, Vectors

from omni.composition.http import http_app


def test_owner_memory_index_search_reviews_and_scope(tmp_path):
    with TestClient(http_app(tmp_path, model=NoModel(), vectorizer=Vectors())) as client:
        assert client.get(f"/api/v1/agents/{uuid.uuid4()}/memory/semantics").status_code == 401
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text()
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        base = f"/api/v1/agents/{agent}/memory"
        rows = [client.post(base, json={"content": t}).json() for t in ("I drink tea", "I prefer tea")]
        assert client.post(base + "/semantics", json=request(rows)).status_code == 422
        indexed = client.post(base + "/index?limit=32")
        assert indexed.status_code == 200 and len(indexed.json()["items"]) == 2
        recall = client.get(base, params={"q": "茗"})
        assert recall.status_code == 200 and recall.headers["cache-control"] == "no-store"
        assert recall.json()["mode"] == "hybrid" and len(recall.json()["items"]) == 2
        created = client.post(base + "/semantics", json=request(rows))
        assert created.status_code == 201
        batch = created.json()
        link = batch["links"][0]
        decided = client.post(base + f"/semantic-links/{link['id']}/decision", json=decision(batch, link))
        assert decided.status_code == 200 and decided.json()["links"][0]["state"] == "accepted"
        assert len(client.get(base, params={"q": "茗"}).json()["items"]) == 2
        assert client.get(f"/api/v1/agents/{uuid.uuid4()}/memory/semantics/{batch['id']}").status_code == 404
        assert client.post(base + "/index?limit=33").status_code == 422
        assert client.post(base + "/index?after=../secret").status_code == 422
        assert client.delete(base + f"/{rows[0]['id']}?revision=1").status_code == 200
        stale = client.get(base + f"/semantics/{batch['id']}").json()
        assert not stale["valid"] and not any("content" in row for row in stale["sources"])
