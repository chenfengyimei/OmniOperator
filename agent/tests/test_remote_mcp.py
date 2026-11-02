"""远程MCP真实回环协议与假OAuth端；不访问公网或真实凭据。"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from omni.config import MCPConfig, MCPServerConfig
from omni.memory.mcp_schemas import MCPSchemaStore
from omni.memory.state import StateStore
from omni.tools.mcp.client import MCPManager


@pytest.fixture
def remote_server():
    state = {"oauth": False, "requests": [], "writes": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, data, status=200, **headers):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for key, value in headers.items():
                self.send_header(key.replace("_", "-"), value)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            base = state["url"]
            if "oauth-protected-resource" in self.path or self.path == "/resource":
                self.reply({"resource": base + "/mcp", "authorization_servers": [base], "scopes_supported": ["tools"]})
            elif ".well-known" in self.path:
                self.reply(
                    {
                        "issuer": base,
                        "authorization_endpoint": base + "/authorize",
                        "token_endpoint": base + "/token",
                        "registration_endpoint": base + "/register",
                        "response_types_supported": ["code"],
                        "grant_types_supported": ["authorization_code", "refresh_token"],
                        "code_challenge_methods_supported": ["S256"],
                        "token_endpoint_auth_methods_supported": ["none"],
                    }
                )
            else:
                self.reply({}, 405)

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state["requests"].append(self.path)
            if self.path == "/register":
                self.reply({**json.loads(raw), "client_id": "fixture-client"})
                return
            if self.path == "/token":
                values = parse_qs(raw.decode())
                assert values.get("code") == ["fixture-code"]
                self.reply(
                    {
                        "access_token": "fixture-access-token",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                        "refresh_token": "fixture-refresh-token",
                        "scope": "tools",
                    }
                )
                return
            if state["oauth"] and self.headers.get("Authorization") != "Bearer fixture-access-token":
                self.reply({}, 401, WWW_Authenticate=f'Bearer resource_metadata="{state["url"]}/resource"')
                return
            request = json.loads(raw)
            if "id" not in request:
                self.reply({}, 202)
                return
            if request["method"] == "initialize":
                result = {
                    "protocolVersion": request["params"]["protocolVersion"],
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "loopback-fixture", "version": "1"},
                }
            elif request["method"] == "tools/list":
                result = {"tools": [{"name": "echo", "description": "fixture", "inputSchema": {"type": "object"}}]}
            else:
                state["writes"] += 1
                result = {"content": [{"type": "text", "text": "loopback-result"}]}
            self.reply({"jsonrpc": "2.0", "id": request["id"], "result": result})

        def do_DELETE(self):
            self.reply({})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state["url"] = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_streamable_http_keeps_discovery_passive_and_enforces_catalog_trust(tmp_path, remote_server):
    state = StateStore(tmp_path / "state.db")
    schemas = MCPSchemaStore(state)
    config = MCPConfig(
        servers={
            "remote": MCPServerConfig(
                transport="streamable_http", url=remote_server["url"] + "/mcp", read_only_tools=["echo"]
            )
        }
    )
    manager = MCPManager(config, tmp_path / "logs", schema_store=schemas)
    try:
        assert manager.status()[0]["state"] == "idle"
        assert not remote_server["requests"]
        assert manager.list_tools("remote")[0]["name"] == "echo"
        current = schemas.status("remote")
        assert schemas.confirm("remote", current["fingerprint"])
        assert "loopback-result" in manager.call("remote", "echo", {}, readonly=True)
    finally:
        assert manager.close()
        state.close()


def test_oauth_requires_explicit_entry_and_consumes_only_matching_callback(tmp_path, remote_server, monkeypatch):
    from omni.system import protected_json

    # 假保护器只用于协议测试；独立测试检查真实Windows DPAPI。
    monkeypatch.setattr(protected_json, "_crypt", lambda data, **kwargs: data[::-1])
    remote_server["oauth"] = True
    state = StateStore(tmp_path / "state.db")
    schemas = MCPSchemaStore(state)
    schemas.observe("remote", "earlier-authorization", 1)
    manager = MCPManager(
        MCPConfig(
            servers={
                "remote": MCPServerConfig(
                    transport="streamable_http",
                    url=remote_server["url"] + "/mcp",
                    auth="oauth",
                    read_only_tools=["echo"],
                )
            }
        ),
        tmp_path / "logs",
        schema_store=schemas,
    )
    try:
        from omni.tools.mcp.client import MCPError

        with pytest.raises(MCPError):
            manager.list_tools("remote")
        assert "/register" not in remote_server["requests"]
        assert "/token" not in remote_server["requests"]
        assert manager.authorize("remote")["state"] == "starting"
        assert not schemas.status("remote")["confirmed_at"]
        connection = manager._connections["remote"]
        deadline = time.monotonic() + 15
        while not connection.remote.status()["authorization_url"] and time.monotonic() < deadline:
            if connection.remote.error:
                pytest.fail(connection.remote.error)
            time.sleep(0.05)
        url = connection.remote.status()["authorization_url"]
        assert url
        values = parse_qs(urlsplit(url).query)
        assert values["code_challenge_method"] == ["S256"]
        with pytest.raises(ValueError):
            manager.authorization_callback("remote", "fixture-code", "wrong-state")
        manager.authorization_callback("remote", "fixture-code", values["state"][0])
        while connection.status()["state"] not in {"ready", "failed"} and time.monotonic() < deadline:
            time.sleep(0.05)
        assert connection.status()["state"] == "ready"
        assert not schemas.status("remote")["confirmed_at"]
        with pytest.raises(MCPError):
            manager.call("remote", "echo", {}, readonly=True)
        assert schemas.confirm("remote", schemas.status("remote")["fingerprint"])
        assert "loopback-result" in manager.call("remote", "echo", {}, readonly=True)
        tokens = list((tmp_path / "mcp_credentials").glob("*.credential"))
        assert tokens and b"fixture-access-token" not in tokens[0].read_bytes()
        with pytest.raises(ValueError):
            manager.authorization_callback("remote", "fixture-code", values["state"][0])
    finally:
        assert manager.close()
        state.close()


@pytest.mark.parametrize("url", ["http://example.com/mcp", "https://user:secret@example.com/mcp", "file:///x"])
def test_remote_endpoint_rejects_insecure_or_credential_urls(url):
    with pytest.raises(ValueError):
        MCPServerConfig(transport="streamable_http", url=url)
