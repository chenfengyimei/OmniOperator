"""Owner-only persona growth reader through the production HTTP app."""

from __future__ import annotations

import asyncio
import uuid

from fastapi.testclient import TestClient
from omni_test_support.v2.reflection import seed_persona

from omni.composition.http import http_app


def credentials(client: TestClient, home) -> str:
    client.headers["Authorization"] = "Bearer " + (home / "run/owner.token").read_text()
    return "/api/v1/agents/" + client.get("/api/v1/agents").json()["items"][0]["id"]


def test_owner_reads_rebuilds_and_never_sees_retracted_content(tmp_path):
    seed = asyncio.run(seed_persona(tmp_path))
    with TestClient(http_app(tmp_path)) as client:
        base = credentials(client, tmp_path) + "/persona-growth"
        assert client.get(base).status_code == 200
        client.headers["Authorization"] = "Bearer invalid"
        assert client.get(base).status_code == 401
    with TestClient(http_app(tmp_path)) as client:
        base = credentials(client, tmp_path) + "/persona-growth"
        ready = client.get(base).json()
        assert ready["state"] == "ready" and ready["approved_count"] == 1
        assert seed["sentence"] in ready["content"] and ready["sha256"] == seed["sha256"]
        assert not ready["truncated"]

        (tmp_path / "persona" / seed["agent"] / "growth.md").unlink()
        stale = client.get(base)
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "persona_growth_stale"
        rebuilt = client.post(base + "/rebuild")
        assert rebuilt.status_code == 200 and rebuilt.json()["sha256"] == seed["sha256"]

        decision = client.post(
            f"/api/v1/agents/{seed['agent']}/reflections/candidates/{seed['candidate']}/decision",
            json={"decision": "retract", "revision": seed["revision"], "digest": seed["digest"]},
        )
        assert decision.status_code == 200 and decision.json()["state"] == "retracted"
        after = client.get(base)
        assert after.status_code == 409 and after.json()["error"]["code"] == "persona_growth_stale"
        empty = client.post(base + "/rebuild").json()
        assert empty["approved_count"] == 0 and seed["sentence"] not in empty["content"]


def test_unknown_agent_is_not_found(tmp_path):
    asyncio.run(seed_persona(tmp_path))
    with TestClient(http_app(tmp_path)) as client:
        base = credentials(client, tmp_path)
        assert client.get(base + "/persona-growth").status_code == 200
        missing = uuid.uuid4()
        assert client.get(f"/api/v1/agents/{missing}/persona-growth").status_code == 404
        assert client.post(f"/api/v1/agents/{missing}/persona-growth/rebuild").status_code == 404
