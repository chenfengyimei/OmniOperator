"""Owner plugin APIs install inert snapshots and authorize fixed catalogs; no direct invoke endpoint exists."""

import uuid

from fastapi.testclient import TestClient
from v2_extension_helpers import authorization, installation, plugin_source

from omni.adapters.persistence.migration_config import head as migration_head
from omni.composition.http import http_app


def owner(client, home):
    client.headers["Authorization"] = "Bearer " + (home / "run/owner.token").read_text()
    agent = client.get("/api/v1/agents").json()["items"][0]["id"]
    return agent, f"/api/v1/agents/{agent}/extensions"


def test_owner_installs_static_snapshot_trusts_selected_tools_and_disables_without_loading_code(tmp_path):
    marker = tmp_path / "import-must-wait-for-action"
    source = plugin_source(tmp_path / "source", marker=marker)
    home = tmp_path / "instance"
    with TestClient(http_app(home)) as client:
        assert client.get(f"/api/v1/agents/{uuid.uuid4()}/extensions").status_code == 401
        _, base = owner(client, home)
        response = client.post(base, json=installation(source))
        assert response.status_code == 201, response.text
        installed = response.json()
        assert not installed["enabled"] and not marker.exists()
        assert "object_id" not in installed and str(source) not in response.text
        assert installed["requires_action_approval"] and installed["trust_model"] == "trusted_python_not_os_sandbox"
        target = base + "/" + installed["id"]
        assert (
            client.post(base, json=installation(source)).json()["extension_revision_id"]
            == installed["extension_revision_id"]
        )
        assert (
            client.put(target + "/authorization", json={**authorization(installed), "trust": False}).status_code == 422
        )
        enabled = client.put(target + "/authorization", json=authorization(installed))
        assert enabled.status_code == 200, enabled.text
        assert enabled.json()["enabled"] and not marker.exists()
        assert all(tool["granted"] and tool["writes"] for tool in enabled.json()["tools"])
        assert client.put(target + "/authorization", json=authorization(installed)).status_code == 409
        assert client.post(target + "/invoke", json={"tool": "echo"}).status_code == 404
        disabled = client.post(target + "/disable", json={"revision": enabled.json()["revision"]})
        assert disabled.status_code == 200 and not disabled.json()["enabled"]
        assert not any(tool["granted"] for tool in disabled.json()["tools"]) and not marker.exists()
        assert (
            client.post(target + "/disable", json={"revision": disabled.json()["revision"]}).json()["revision"]
            == disabled.json()["revision"]
        )
        assert client.get("/api/v1/version").json()["schema_version"] == migration_head()


def test_plugin_api_scope_bounds_and_immutable_update_replay_do_not_follow_mutable_source(tmp_path):
    source = plugin_source(tmp_path / "source")
    newer = plugin_source(tmp_path / "new-source", version="2")
    home = tmp_path / "instance"
    with TestClient(http_app(home)) as client:
        agent, base = owner(client, home)
        installed = client.post(base, json=installation(source)).json()
        target = base + "/" + installed["id"]
        update = installation(newer, "version-two", installed["id"], installed["revision"])
        changed = client.post(base, json=update)
        assert changed.status_code == 201, changed.text
        assert changed.json()["version"] == "2" and not changed.json()["enabled"]
        repeated = client.post(base, json=installation(source)).json()
        assert (
            repeated["extension_revision_id"] == installed["extension_revision_id"]
            and not repeated["is_current_version"]
        )
        assert repeated["replayed"] and not repeated["enabled"]
        assert client.get(target).json()["extension_revision_id"] == changed.json()["extension_revision_id"]
        assert client.post(base, json={**installation(source), "key": ""}).status_code == 422
        assert (
            client.post(
                base, json={**installation(source), "extension_id": installed["id"], "revision": True}
            ).status_code
            == 422
        )
        page = client.get(base + "?limit=1").json()
        assert len(page["items"]) == 1
        assert client.get(base + f"?after={page['next_after']}").json()["items"] == []
        for query in ("?limit=51", "?after=-1", "?after=9223372036854775808"):
            assert client.get(base + query).status_code == 422
        other = client.portal.call(client.app.state.runtime.application._db.write, lambda u: u.create_agent("Other"))[
            "id"
        ]
        wrong = f"/api/v1/agents/{other}/extensions/" + installed["id"]
        assert client.get(wrong).status_code == 404
        assert client.put(wrong + "/authorization", json=authorization(changed.json())).status_code == 404
        assert client.get(f"/api/v1/agents/{other}/extensions").json()["items"] == []
        assert client.get(base).json()["items"][0]["agent_id"] == agent
