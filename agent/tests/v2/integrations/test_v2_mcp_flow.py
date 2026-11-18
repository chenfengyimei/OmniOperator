"""Owner MCP production chain: explicit connect, per-tool authorization, model call, drift and unknown."""

from omni_test_support.v2.mcp_flow import (
    CallModel as CallModel,
)
from omni_test_support.v2.mcp_flow import (
    FixtureHTTP as FixtureHTTP,
)
from omni_test_support.v2.mcp_flow import (
    SeededSecrets as SeededSecrets,
)
from omni_test_support.v2.mcp_flow import (
    TestClient as TestClient,
)
from omni_test_support.v2.mcp_flow import (
    authorize_search as authorize_search,
)
from omni_test_support.v2.mcp_flow import (
    configure as configure,
)
from omni_test_support.v2.mcp_flow import (
    connect as connect,
)
from omni_test_support.v2.mcp_flow import (
    http_app as http_app,
)
from omni_test_support.v2.mcp_flow import (
    owner_client as owner_client,
)
from omni_test_support.v2.mcp_flow import (
    stdio_server as stdio_server,
)
from omni_test_support.v2.mcp_flow import (
    time as time,
)
from omni_test_support.v2.mcp_flow import (
    wait_run as wait_run,
)


def test_stdio_owner_chain_connects_authorizes_and_model_calls_tool(tmp_path):
    definition, _ = stdio_server(tmp_path / "server", "ok", tmp_path / "calls.log")
    model = CallModel("mcp__notes__search", {"q": "日报"})
    client, _agent, base = owner_client(tmp_path, model)
    try:
        config = configure(client, base, "notes", definition)
        listing = client.get(base + "/mcp/servers").json()
        assert listing["items"][0]["state"] == "idle" and listing["items"][0]["authorized_tools"] == 0
        connected = connect(client, base, config)
        assert connected["connected"] is True and connected["tool_count"] == 3
        assert connected["authority_epoch"] == 1 and connected["drift"] is False
        catalog = client.get(base + f"/mcp/servers/{config['id']}/catalog", params={"limit": 2}).json()
        assert len(catalog["items"]) == 2 and catalog["next_after"] == 1
        second = client.get(base + f"/mcp/servers/{config['id']}/catalog", params={"after": 1, "limit": 2}).json()
        assert [row["name"] for row in second["items"]] == ["echo"]
        search = next(row for row in catalog["items"] if row["name"] == "search")
        decision = authorize_search(client, base, config, search)
        assert decision["authorized"] == ["search"] and decision["read_only"] == ["search"]
        assert decision["server"]["needs_confirmation"] is False

        receipt = client.post(
            base + "/turns", json={"key": "mcp-read", "message": "search notes", "mode": "agent"}
        ).json()
        run = wait_run(client, base, receipt["run_id"])
        assert run["state"] == "succeeded", run
        assert "mcp__notes__search" in model.catalogs[0]
        assert "mcp__notes__write" not in model.catalogs[0]
        calls = client.get(base + f"/mcp/servers/{config['id']}/calls").json()
        assert len(calls["items"]) == 1
        call = calls["items"][0]
        assert call["state"] == "done" and call["name"] == "mcp__notes__search" and call["writes"] is False
        assert call["truncated"] is False
        assert (tmp_path / "calls.log").read_text(encoding="utf-8") == "call:search\n"
        audit = client.get(base + "/audit").json()["items"]
        assert any(row["kind"] == "action.ok" for row in audit)
        assert any(row["kind"] == "mcp.authorized" for row in audit)
    finally:
        client.__exit__(None, None, None)


def test_stdio_is_error_is_a_result_not_a_disconnect(tmp_path):
    definition, _ = stdio_server(tmp_path / "server", "error", tmp_path / "calls.log")
    model = CallModel("mcp__notes__search", {"q": "x"})
    client, _agent, base = owner_client(tmp_path, model)
    try:
        config = configure(client, base, "notes", definition)
        assert connect(client, base, config)["connected"] is True
        catalog = client.get(base + f"/mcp/servers/{config['id']}/catalog").json()
        search = next(row for row in catalog["items"] if row["name"] == "search")
        authorize_search(client, base, config, search)
        receipt = client.post(base + "/turns", json={"key": "mcp-error", "message": "call", "mode": "agent"}).json()
        run = wait_run(client, base, receipt["run_id"])
        assert run["state"] == "succeeded", run
        calls = client.get(base + f"/mcp/servers/{config['id']}/calls").json()["items"]
        assert calls[0]["state"] == "failed" and calls[0]["error_code"] == "mcp_tool_error"
        assert (tmp_path / "calls.log").read_text(encoding="utf-8") == "call:search\n"
        assert client.get(base + "/mcp/servers").json()["items"][0]["state"] == "ready"
    finally:
        client.__exit__(None, None, None)


def test_catalog_drift_revokes_authorization_and_old_fingerprint_cannot_confirm(tmp_path):
    definition, _ = stdio_server(tmp_path / "server", "drift")
    model = CallModel("mcp__notes__search", {"q": "x"})
    client, _agent, base = owner_client(tmp_path, model)
    try:
        config = configure(client, base, "notes", definition)
        first = connect(client, base, config)
        assert first["connected"] is True
        catalog = client.get(base + f"/mcp/servers/{config['id']}/catalog").json()
        search = next(row for row in catalog["items"] if row["name"] == "search")
        authorize_search(client, base, config, search)
        old_fingerprint = catalog["catalog"]["fingerprint"]
        # The running server changes its inputSchema while the configuration revision stays identical.
        (tmp_path / "server/drift-enabled").write_text("1", encoding="utf-8")
        second = connect(client, base, config)
        assert second["connected"] is True and second["drift"] is True and second["revoked"] == 1
        assert second["authority_epoch"] == 2
        after = client.get(base + f"/mcp/servers/{config['id']}/catalog").json()
        assert after["authorized"] == [] and after["server"]["needs_confirmation"] is True
        stale = client.put(
            base + f"/mcp/servers/{config['id']}/tools/{search['tool_id']}/authorization",
            json={
                "catalog_fingerprint": old_fingerprint,
                "read_only": True,
                "authorized": True,
                "evidence": "old page",
            },
        )
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "mcp_catalog_changed"
        receipt = client.post(base + "/turns", json={"key": "drift", "message": "search", "mode": "agent"}).json()
        run = wait_run(client, base, receipt["run_id"])
        assert run["state"] == "succeeded"
        assert all("mcp__" not in name for name in model.catalogs[0])
        assert client.get(base + f"/mcp/servers/{config['id']}/calls").json()["items"] == []
    finally:
        client.__exit__(None, None, None)


def test_crashed_server_keeps_unknown_and_never_replays(tmp_path):
    definition, _ = stdio_server(tmp_path / "server", "crash")
    model = CallModel("mcp__notes__search", {"q": "x"})
    client, _agent, base = owner_client(tmp_path, model)
    try:
        config = configure(client, base, "notes", definition)
        assert connect(client, base, config)["connected"] is True
        catalog = client.get(base + f"/mcp/servers/{config['id']}/catalog").json()
        search = next(row for row in catalog["items"] if row["name"] == "search")
        authorize_search(client, base, config, search)
        receipt = client.post(base + "/turns", json={"key": "crash", "message": "call", "mode": "agent"}).json()
        run = wait_run(client, base, receipt["run_id"])
        assert run["state"] == "failed", run
        action = client.get(base + "/audit").json()["items"]
        assert any(row["kind"] == "action.unknown" for row in action)
        calls = client.get(base + f"/mcp/servers/{config['id']}/calls").json()["items"]
        assert calls[0]["state"] == "unknown" and calls[0]["error_code"] == "mcp_connection_lost"
        repeat = client.post(base + "/turns", json={"key": "crash", "message": "call", "mode": "agent"}).json()
        assert repeat["run_id"] == receipt["run_id"] and repeat["duplicate"] is True
        assert len(client.get(base + f"/mcp/servers/{config['id']}/calls").json()["items"]) == 1
    finally:
        client.__exit__(None, None, None)


def test_remote_bearer_token_is_used_but_never_exposed(tmp_path):
    with FixtureHTTP(require_token="remote-secret") as fixture:
        model = CallModel("mcp__remote__search", {"q": "远程"})
        secrets = SeededSecrets({"mcp.remote.token": "remote-secret"})
        client, _agent, base = owner_client(tmp_path, model, secrets)
        try:
            config = configure(
                client,
                base,
                "remote",
                fixture.configuration(auth_mode="bearer", credential_ref="mcp.remote.token"),
            )
            connected = connect(client, base, config)
            assert connected["connected"] is True, connected
            catalog = client.get(base + f"/mcp/servers/{config['id']}/catalog").json()
            search = next(row for row in catalog["items"] if row["name"] == "search")
            authorize_search(client, base, config, search)
            receipt = client.post(base + "/turns", json={"key": "remote", "message": "call", "mode": "agent"}).json()
            run = wait_run(client, base, receipt["run_id"])
            assert run["state"] == "succeeded", run
            calls = client.get(base + f"/mcp/servers/{config['id']}/calls").json()["items"]
            assert calls[0]["state"] == "done"
            history = client.get(base + "/audit").text
            assert "remote-secret" not in history
            assert "remote-secret" not in client.get("/api/v1/system/logs").text
        finally:
            client.__exit__(None, None, None)


def test_missing_remote_token_fails_connect_without_starting_oauth(tmp_path):
    with FixtureHTTP(require_token="remote-secret") as fixture:
        model = CallModel("mcp__remote__search", {"q": "x"})
        client, _agent, base = owner_client(tmp_path, model, SeededSecrets())
        try:
            config = configure(
                client,
                base,
                "remote",
                fixture.configuration(auth_mode="bearer", credential_ref="mcp.remote.missing"),
            )
            failed = connect(client, base, config)
            assert failed["connected"] is False and failed["state"] == "failed"
            assert failed["error_code"] == "mcp_unauthorized"
            assert len(fixture.requests) == 1 and fixture.requests[0]["method"] == "initialize"
        finally:
            client.__exit__(None, None, None)
