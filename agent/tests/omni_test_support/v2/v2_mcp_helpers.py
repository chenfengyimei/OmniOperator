"""Fake MCP servers and model fixtures for isolated transport and workbench tests."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl

from omni.domain.contracts import Invocation, ModelReply

TOOLS = [
    {
        "name": "search",
        "description": "Search notes (untrusted external description)",
        "inputSchema": {
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
            "additionalProperties": False,
        },
    },
    {
        "name": "write",
        "description": "Write a note",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "echo",
        "description": "Echo the given value",
        "inputSchema": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
    },
]

STDIO_SERVER = r"""
import json, os, sys, time

MODE = sys.argv[1] if len(sys.argv) > 1 else "ok"
TOOLS = json.loads(sys.argv[2]) if len(sys.argv) > 2 else []
LOG = sys.argv[3] if len(sys.argv) > 3 else ""

def note(text):
    if LOG:
        with open(LOG, "a", encoding="utf-8") as stream:
            stream.write(text + "\n")

def send(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=False) + "\n")
    sys.stdout.flush()

if MODE == "crash_at_start":
    os._exit(7)

for line in sys.stdin:
    request = json.loads(line)
    method, identity = request.get("method"), request.get("id")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": identity, "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "fixture", "version": "1"}}})
    elif method == "notifications/initialized":
        continue
    elif method == "tools/list":
        cursor = (request.get("params") or {}).get("cursor", "")
        tools = json.loads(json.dumps(TOOLS))
        if MODE == "drift" and os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "drift-enabled")):
            tools[0]["inputSchema"]["properties"]["more"] = {"type": "integer"}
        if not cursor:
            send({"jsonrpc": "2.0", "id": identity, "result": {"tools": tools[:2], "nextCursor": "page-2"}})
        else:
            send({"jsonrpc": "2.0", "id": identity, "result": {"tools": tools[2:]}})
    elif method == "tools/call":
        params = request.get("params") or {}
        name, arguments = params.get("name"), params.get("arguments") or {}
        note("call:" + str(name))
        if MODE == "crash":
            os._exit(9)
        if MODE == "slow":
            time.sleep(30)
        if MODE == "huge":
            text = "巨" * 200000
        else:
            text = "结果：" + json.dumps(arguments, ensure_ascii=False)
        is_error = MODE == "error"
        send({"jsonrpc": "2.0", "id": identity, "result": {"content": [{"type": "text", "text": text}], "isError": is_error}})
    else:
        send({"jsonrpc": "2.0", "id": identity, "error": {"code": -32601, "message": "unsupported"}})
"""


def stdio_server(folder, mode="ok", log=None):
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / "server.py"
    script.write_text(STDIO_SERVER, encoding="utf-8")
    return (
        {
            "transport": "stdio",
            "command": __import__("sys").executable,
            "arguments": [str(script), mode, json.dumps(TOOLS, ensure_ascii=False), str(log or "")],
            "endpoint": "",
            "auth_mode": "none",
            "credential_ref": "",
        },
        {},
    )


class FixtureHTTP:
    """Loopback Streamable HTTP MCP endpoint with optional SSE, bearer check and OAuth provider."""

    def __init__(self, *, sse=False, require_token="", mode="ok", oauth=False, issued_token="fixture-access"):
        self.sse, self.require_token, self.mode = sse, require_token, mode
        self.oauth, self.issued_token = oauth, issued_token
        self.requests = []
        self.session_ids = []
        self.authorization_headers = []
        self.token_requests = []
        self.authorizations = []
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _send(self, status, body=b"", headers=None):
                self.send_response(status)
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                base = f"http://127.0.0.1:{fixture.server.server_address[1]}"
                if fixture.oauth and self.path == "/.well-known/oauth-protected-resource":
                    body = json.dumps({"authorization_servers": [base]}).encode()
                    self._send(200, body, {"Content-Type": "application/json"})
                    return
                if fixture.oauth and self.path == "/.well-known/oauth-authorization-server":
                    body = json.dumps(
                        {
                            "issuer": base,
                            "authorization_endpoint": base + "/authorize",
                            "token_endpoint": base + "/token",
                            "registration_endpoint": base + "/register",
                            "scopes_supported": ["mcp.read"],
                        }
                    ).encode()
                    self._send(200, body, {"Content-Type": "application/json"})
                    return
                self._send(404, b"not found")

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                if fixture.oauth and self.path == "/register":
                    document = json.loads(raw or b"{}")
                    fixture.authorizations.append(document)
                    body = json.dumps({"client_id": "fixture-client", "client_secret": "fixture-secret"}).encode()
                    self._send(200, body, {"Content-Type": "application/json"})
                    return
                if fixture.oauth and self.path == "/token":
                    form = dict(parse_qsl(raw.decode("utf-8")))
                    fixture.token_requests.append(form)
                    if form.get("grant_type") == "authorization_code":
                        if form.get("code") != "fixture-code" or not 40 <= len(form.get("code_verifier", "")) <= 128:
                            self._send(400, b'{"error":"invalid_grant"}', {"Content-Type": "application/json"})
                            return
                        value = {
                            "access_token": fixture.issued_token,
                            "refresh_token": "fixture-refresh",
                            "token_type": "Bearer",
                            "expires_in": 3600,
                            "scope": "mcp.read",
                        }
                    elif form.get("grant_type") == "refresh_token":
                        value = {
                            "access_token": fixture.issued_token + "-refreshed",
                            "token_type": "Bearer",
                            "expires_in": 3600,
                        }
                    else:
                        self._send(400, b'{"error":"unsupported_grant_type"}', {"Content-Type": "application/json"})
                        return
                    self._send(200, json.dumps(value).encode(), {"Content-Type": "application/json"})
                    return
                payload = json.loads(raw or b"{}")
                fixture.requests.append(payload)
                fixture.session_ids.append(self.headers.get("Mcp-Session-Id", ""))
                fixture.authorization_headers.append(self.headers.get("Authorization", ""))
                if fixture.require_token and self.headers.get("Authorization") != "Bearer " + fixture.require_token:
                    self._send(401, b'{"error":"unauthorized"}', {"Content-Type": "application/json"})
                    return
                method, identity = payload.get("method"), payload.get("id")
                if method == "notifications/initialized":
                    self._send(202)
                    return
                if method == "initialize":
                    result = {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "http-fixture", "version": "1"},
                    }
                    headers = {"Content-Type": "application/json", "Mcp-Session-Id": "fixture-session"}
                elif method == "tools/list":
                    cursor = (payload.get("params") or {}).get("cursor", "")
                    result = {"tools": TOOLS[:2], "nextCursor": "page-2"} if not cursor else {"tools": TOOLS[2:]}
                    headers = {"Content-Type": "application/json"}
                elif method == "tools/call":
                    if fixture.mode == "slow":
                        import time

                        time.sleep(30)
                    if fixture.mode == "status500":
                        self._send(500, b"server exploded", {"Content-Type": "text/plain"})
                        return
                    params = payload.get("params") or {}
                    text = "远程结果：" + json.dumps(params.get("arguments") or {}, ensure_ascii=False)
                    result = {"content": [{"type": "text", "text": text}], "isError": fixture.mode == "error"}
                    headers = {"Content-Type": "application/json"}
                else:
                    result = {"error": "unsupported"}
                    headers = {"Content-Type": "application/json"}
                body = json.dumps({"jsonrpc": "2.0", "id": identity, "result": result}, ensure_ascii=False).encode()
                if fixture.sse and method == "tools/call":
                    event = b"event: message\ndata: " + body + b"\n\n"
                    self._send(200, event, {"Content-Type": "text/event-stream"})
                    return
                self._send(200, body, headers)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    @property
    def endpoint(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}/mcp"

    def configuration(self, *, auth_mode="none", credential_ref=""):
        return {
            "transport": "streamable_http",
            "command": "",
            "arguments": [],
            "endpoint": self.endpoint,
            "auth_mode": auth_mode,
            "credential_ref": credential_ref,
        }


class SeededSecrets:
    """In-memory SecretStore for injected credentials; never reads a real vault."""

    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, reference):
        return self.values.get(reference)

    def set(self, reference, value):
        self.values[reference] = value

    def delete(self, reference):
        self.values.pop(reference, None)


class CallModel:
    """One scripted tool call, then a final reply; records the advertised catalog."""

    def __init__(self, tool, arguments):
        self.tool, self.arguments = tool, arguments
        self.catalogs = []

    async def complete(self, messages, tools, settings):
        self.catalogs.append(tuple(spec.name for spec in tools))
        if self.tool in self.catalogs[-1] and not any(message["role"] == "tool" for message in messages):
            return ModelReply("", (Invocation("mcp-call", self.tool, self.arguments),), cost_micro=2)
        return ModelReply("done", cost_micro=2)


__all__ = ["FixtureHTTP", "CallModel", "SeededSecrets", "TOOLS", "stdio_server"]
