"""Owner-managed Agent spaces initialize independently and rename without changing scoped history or authority."""

import asyncio
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from omni.composition.http import http_app
from omni.composition.instance import open_instance
from omni.domain.agents import MAX_AGENTS, MAX_REVISION, name_value, revision_value
from omni.domain.contracts import BusinessError, ModelReply


class Model:
    async def complete(self, messages, tools, settings):
        return ModelReply("isolated fixture", cost_micro=0)


@pytest.mark.parametrize("value", [None, "", "  ", "a" * 101, "name\n", "\x7f", "bad\ud800"])
def test_invalid_owner_names_are_rejected_before_any_transaction(value):
    with pytest.raises(BusinessError) as failure:
        name_value(value)
    assert failure.value.code == "invalid_agent_name" and failure.value.status == 422


def test_unicode_names_keep_exact_bytes_and_revision_bounds_do_not_overflow_sqlite():
    assert name_value("🦊" * 100) == "🦊" * 100
    assert name_value("  工作空间 e\u0301  ") == "  工作空间 e\u0301  "
    assert revision_value(MAX_REVISION) == MAX_REVISION
    for value in (False, 0, -1, "1", MAX_REVISION + 1):
        with pytest.raises(BusinessError) as failure:
            revision_value(value)
        assert failure.value.code == "invalid_agent_revision"


def owner(client, home):
    client.headers["Authorization"] = "Bearer " + (home / "run/owner.token").read_text(encoding="utf-8")
    return client.get("/api/v1/agents").json()["items"][0]


def test_authenticated_creation_initializes_a_fresh_space_without_copying_the_selected_space(tmp_path):
    with TestClient(http_app(tmp_path, model=Model())) as client:
        assert client.post("/api/v1/agents", json={"name": "unauthorized"}).status_code == 401
        original = owner(client, tmp_path)
        before = client.get(f"/api/v1/agents/{original['id']}/settings").json()
        changed = client.put(
            f"/api/v1/agents/{original['id']}/settings",
            json={"revision": before["revision"], "value": {**before["value"], "persona": "private-source-persona"}},
        )
        assert changed.status_code == 200
        created = client.post("/api/v1/agents", json={"name": "研究 🦊"})
        assert created.status_code == 201
        row = created.json()
        assert row["id"] != original["id"] and row["name"] == "研究 🦊" and row["revision"] == 1
        assert row["enabled"] is True
        assert client.get(f"/api/v1/agents/{row['id']}").json() == row
        fresh = client.get(f"/api/v1/agents/{row['id']}/settings").json()
        assert fresh["value"]["model_profile_id"] != before["value"]["model_profile_id"]
        assert fresh["value"]["persona"] != "private-source-persona" and fresh["value"]["workspace"] == ""
        assert client.get(f"/api/v1/agents/{row['id']}/sessions").json()["items"] == []
        assert len(client.get("/api/v1/agents").json()["items"]) == 2
        # Display names are not identities and do not accidentally merge spaces.
        duplicate_name = client.post("/api/v1/agents", json={"name": row["name"]}).json()
        assert duplicate_name["id"] not in {row["id"], original["id"]}
        audit = client.get(f"/api/v1/agents/{row['id']}/audit").json()["items"]
        creation = [item for item in audit if item["kind"] == "agent.created"]
        assert len(creation) == 1 and creation[0]["data"] == {"revision": 1}


def test_renaming_uses_compare_and_swap_preserves_settings_and_keeps_audit_names_private(tmp_path):
    with TestClient(http_app(tmp_path, model=Model())) as client:
        original = owner(client, tmp_path)
        base = f"/api/v1/agents/{original['id']}"
        before = client.get(base + "/settings").json()
        renamed = client.put(base, json={"name": "private-display-canary", "revision": original["revision"]})
        assert renamed.status_code == 200 and renamed.json()["revision"] == original["revision"] + 1
        assert renamed.json()["created_at"] == original["created_at"]
        assert client.put(base, json={"name": "overwrite", "revision": original["revision"]}).status_code == 409
        assert client.get(base).json() == renamed.json()
        assert client.get(base + "/settings").json() == before
        assert (
            client.put(base, json={"name": "private-display-canary", "revision": renamed.json()["revision"]}).json()
            == renamed.json()
        )
        audit = client.get(base + "/audit").json()["items"]
        assert len([row for row in audit if row["kind"] == "agent.renamed"]) == 1
        assert all("private-display-canary" not in json.dumps(row["data"]) for row in audit)
        missing = f"/api/v1/agents/{uuid.uuid4()}"
        assert client.get(missing).status_code == 404
        assert client.put(missing, json={"name": "missing", "revision": 1}).status_code == 404


def test_agent_http_bounds_are_strict_and_reject_unknown_authority_fields(tmp_path):
    with TestClient(http_app(tmp_path, model=Model())) as client:
        original = owner(client, tmp_path)
        for value in (
            {"name": ""},
            {"name": " "},
            {"name": "a\n"},
            {"name": "a" * 101},
            {"name": "\ud800"},
            {"name": "valid", "enabled": False},
        ):
            rejected = client.post(
                "/api/v1/agents", content=json.dumps(value).encode(), headers={"Content-Type": "application/json"}
            )
            assert rejected.status_code == 422
        base = f"/api/v1/agents/{original['id']}"
        for revision in (True, 0, "1", MAX_REVISION + 1):
            assert client.put(base, json={"name": "valid", "revision": revision}).status_code == 422
        assert client.post("/api/v1/agents", content=b" " * 2049).status_code == 413
        assert len(client.get("/api/v1/agents").json()["items"]) == 1


def test_concurrent_owner_creation_cannot_race_past_the_space_capacity(tmp_path):
    async def scenario():
        instance = await open_instance(tmp_path, model=Model(), capabilities=())
        try:
            await instance._database.write(lambda u: [u.create_agent(f"fixture-{i}") for i in range(MAX_AGENTS - 2)])
            replies = await asyncio.gather(
                instance.application.create_agent("one"),
                instance.application.create_agent("two"),
                return_exceptions=True,
            )
            successes = [value for value in replies if isinstance(value, dict)]
            rejected = [value for value in replies if isinstance(value, BusinessError)]
            assert len(successes) == len(rejected) == 1 and rejected[0].code == "agent_capacity"
            assert len(await instance.application.query("agents", "")) == MAX_AGENTS
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_shutdown_rejects_new_space_mutations_but_keeps_existing_readable(tmp_path):
    with TestClient(http_app(tmp_path, model=Model())) as client:
        original = owner(client, tmp_path)
        client.portal.call(client.app.state.runtime.application.stop_accepting)
        assert client.post("/api/v1/agents", json={"name": "late"}).status_code == 503
        assert client.put(f"/api/v1/agents/{original['id']}", json={"name": "late", "revision": 1}).status_code == 503
        assert client.get(f"/api/v1/agents/{original['id']}").json() == original
