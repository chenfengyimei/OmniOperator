"""A missing response cannot prove that an MCP write did not leave the process."""

import asyncio
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import httpx
import pytest
from test_v2_mcp import target
from test_v2_mcp_flow import configure, connect, owner_client, wait_run
from v2_action_review_helpers import review_request
from v2_mcp_helpers import CallModel, FixtureHTTP

from omni.adapters.mcp.http import HttpSession
from omni.adapters.mcp.stdio import StdioSession
from omni.domain.mcp import MCPDispatchError, MCPUncertain


@pytest.mark.parametrize("failure", [BrokenPipeError, ConnectionResetError, asyncio.CancelledError])
def test_stdio_queued_frame_with_failed_drain_is_unknown(tmp_path, monkeypatch, failure):
    async def scenario():
        frames, reclaimed = [], []

        class Writer:
            def write(self, frame):
                frames.append(frame)

            async def drain(self):
                raise failure()

        session = StdioSession(target(), cwd=str(tmp_path))
        session._owned = SimpleNamespace(
            reclaimed=False,
            process=SimpleNamespace(returncode=None, stdin=Writer(), stdout=asyncio.StreamReader()),
        )

        async def reclaim(timeout):
            reclaimed.append(timeout)
            session._owned = None
            return True

        monkeypatch.setattr(session, "_reclaim", reclaim)
        with pytest.raises(MCPUncertain) as caught:
            await session.call("write", {"text": "one write"}, 60)
        assert caught.value.sent and len(frames) == 1 and reclaimed == [5]
        assert json.loads(frames[0])["method"] == "tools/call"
        assert caught.value.code == ("mcp_cancelled" if failure is asyncio.CancelledError else "mcp_connection_lost")

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ReadError,
        httpx.ReadTimeout,
        httpx.WriteError,
        httpx.WriteTimeout,
        httpx.RemoteProtocolError,
        httpx.CloseError,
    ],
)
def test_http_missing_headers_after_transport_handoff_are_unknown(failure):
    async def scenario():
        requests = []

        async def handler(request):
            requests.append(json.loads(request.content))
            raise failure("isolated transport failure", request=request)

        session = HttpSession(
            target(transport="streamable_http", endpoint="http://127.0.0.1:1/mcp"),
            lambda: "",
            client_factory=lambda **options: httpx.AsyncClient(transport=httpx.MockTransport(handler), **options),
        )
        try:
            with pytest.raises(MCPUncertain) as caught:
                await session.call("write", {"text": "one write"}, 60)
            assert caught.value.sent and len(requests) == 1 and caught.value.code == "mcp_connection_lost"
        finally:
            assert await session.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout])
def test_http_proven_connection_or_pool_failure_stays_not_sent(failure):
    async def scenario():
        async def handler(request):
            # Only classify the documented failure type; this is an injected
            # connection/pool failure, not proof of real TCP connection timing.
            raise failure("isolated connection failure", request=request)

        session = HttpSession(
            target(transport="streamable_http", endpoint="http://127.0.0.1:1/mcp"),
            lambda: "",
            client_factory=lambda **options: httpx.AsyncClient(transport=httpx.MockTransport(handler), **options),
        )
        try:
            with pytest.raises(MCPDispatchError) as caught:
                await session.call("write", {}, 60)
            assert not isinstance(caught.value, MCPUncertain) and caught.value.sent is False
        finally:
            assert await session.close()

    asyncio.run(scenario())


def test_http_cancellation_after_handoff_without_headers_is_unknown():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        requests = []

        async def handler(request):
            requests.append(json.loads(request.content))
            entered.set()
            await release.wait()
            return httpx.Response(200, json={})

        session = HttpSession(
            target(transport="streamable_http", endpoint="http://127.0.0.1:1/mcp"),
            lambda: "",
            client_factory=lambda **options: httpx.AsyncClient(transport=httpx.MockTransport(handler), **options),
        )
        caller = asyncio.create_task(session.call("write", {}, 60))
        try:
            await entered.wait()
            caller.cancel()
            with pytest.raises(MCPUncertain) as caught:
                await caller
            assert caught.value.sent and caught.value.code == "mcp_cancelled" and len(requests) == 1
        finally:
            release.set()
            await asyncio.gather(caller, return_exceptions=True)
            assert await session.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("response", ["disconnect", "invalid_status_line"])
def test_real_loopback_server_received_complete_call_before_missing_headers(response):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append(json.loads(raw))
            self.close_connection = True
            if response == "invalid_status_line":
                self.wfile.write(b"this is not an HTTP status line\r\n\r\n")
                self.wfile.flush()
            self.connection.shutdown(socket.SHUT_RDWR)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    async def scenario():
        session = HttpSession(
            target(transport="streamable_http", endpoint=f"http://127.0.0.1:{server.server_port}/mcp"), lambda: ""
        )
        try:
            with pytest.raises(MCPUncertain) as caught:
                await session.call("write", {"text": "received once"}, 60)
            assert caught.value.sent and caught.value.code == "mcp_connection_lost"
            assert len(requests) == 1 and requests[0]["params"]["arguments"] == {"text": "received once"}
        finally:
            assert await session.close()

    try:
        asyncio.run(scenario())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


@pytest.mark.parametrize("failure", [BrokenPipeError, ConnectionResetError])
def test_stdio_failure_before_write_returns_stays_not_sent(tmp_path, monkeypatch, failure):
    async def scenario():
        class Writer:
            def write(self, frame):
                raise failure()

        session = StdioSession(target(), cwd=str(tmp_path))
        session._owned = SimpleNamespace(
            reclaimed=False, process=SimpleNamespace(returncode=None, stdin=Writer(), stdout=asyncio.StreamReader())
        )
        reclaimed = []

        async def reclaim(timeout):
            reclaimed.append(timeout)
            session._owned = None
            return True

        monkeypatch.setattr(session, "_reclaim", reclaim)
        with pytest.raises(MCPDispatchError) as caught:
            await session.call("write", {}, 60)
        assert caught.value.sent is False and not isinstance(caught.value, MCPUncertain) and reclaimed == [5]

    asyncio.run(scenario())


def test_http_cancellation_in_authorization_preflight_never_hands_request_to_transport():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        requests = []

        async def authorization():
            entered.set()
            await release.wait()
            return ""

        async def handler(request):
            requests.append(request)
            return httpx.Response(200, json={})

        session = HttpSession(
            target(transport="streamable_http", endpoint="http://127.0.0.1:1/mcp"),
            authorization,
            client_factory=lambda **options: httpx.AsyncClient(transport=httpx.MockTransport(handler), **options),
        )
        caller = asyncio.create_task(session.call("write", {}, 60))
        try:
            await entered.wait()
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert not requests
        finally:
            release.set()
            await asyncio.gather(caller, return_exceptions=True)
            assert await session.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("response", ["disconnect", "invalid_status_line"])
def test_approved_write_missing_headers_is_persisted_unknown_and_owner_review_does_not_replay(
    tmp_path, monkeypatch, response
):
    marker = tmp_path / "external-fixture-write.txt"
    with FixtureHTTP() as fixture:
        handler = fixture.server.RequestHandlerClass
        original_send = handler._send

        def send_or_disconnect(self, status, body=b"", headers=None):
            payload = fixture.requests[-1] if fixture.requests else {}
            if payload.get("method") != "tools/call":
                return original_send(self, status, body, headers)
            with marker.open("a", encoding="utf-8") as stream:
                stream.write(payload["params"]["arguments"]["text"] + "\n")
            self.close_connection = True
            if response == "invalid_status_line":
                self.wfile.write(b"invalid HTTP status line\r\n\r\n")
                self.wfile.flush()
            self.connection.shutdown(socket.SHUT_RDWR)

        monkeypatch.setattr(handler, "_send", send_or_disconnect)
        model = CallModel("mcp__remote__write", {"text": "approved once"})
        client, agent, base = owner_client(tmp_path / "instance", model)
        try:
            config = configure(client, base, "remote", fixture.configuration())
            assert connect(client, base, config)["connected"]
            catalog = client.get(base + f"/mcp/servers/{config['id']}/catalog").json()
            tool = next(item for item in catalog["items"] if item["name"] == "write")
            authorized = client.put(
                base + f"/mcp/servers/{config['id']}/tools/{tool['tool_id']}/authorization",
                json={
                    "catalog_fingerprint": catalog["catalog"]["fingerprint"],
                    "read_only": False,
                    "authorized": True,
                    "evidence": "Owner reviewed isolated write fixture",
                },
            )
            assert authorized.status_code == 200, authorized.text
            intent = {"key": "write-once", "message": "Write to isolated fixture", "mode": "agent"}
            receipt = client.post(base + "/turns", json=intent).json()
            wait_run(client, base, receipt["run_id"], states=("waiting_approval",))
            assert not marker.exists() and not any(item["method"] == "tools/call" for item in fixture.requests)
            approval = client.get(base + "/approvals").json()["items"][0]
            approved = client.post(
                base + "/approvals/" + approval["id"],
                json={"allowed": True, "policy_revision": approval["policy_revision"]},
            )
            assert approved.status_code == 200, approved.text
            assert wait_run(client, base, receipt["run_id"])["state"] == "failed"

            async def settled_action():
                application = client.app.state.runtime.application
                async with asyncio.timeout(5):
                    while application.health()["active_runs"]:
                        await asyncio.sleep(0.01)
                return (await application.query("actions", agent, run_id=receipt["run_id"]))[0]

            action = client.portal.call(settled_action)
            assert action["state"] == action["effect"] == "unknown"
            calls = client.get(base + f"/mcp/servers/{config['id']}/calls").json()["items"]
            assert len(calls) == 1 and calls[0]["state"] == "unknown" and calls[0]["writes"]
            assert calls[0]["error_code"] == "mcp_connection_lost"
            detail = client.get(base + f"/actions/{action['id']}/review").json()
            assert detail["eligible"], detail
            reviewed = client.post(
                base + f"/actions/{action['id']}/review",
                json=review_request(detail, "applied", "Inspected the isolated fixture file"),
            )
            assert reviewed.status_code == 200, reviewed.text
            assert reviewed.json()["resolved"] and reviewed.json()["state"] == reviewed.json()["effect"] == "unknown"
            duplicate = client.post(base + "/turns", json=intent).json()
            assert duplicate["duplicate"] and duplicate["run_id"] == receipt["run_id"]
            assert marker.read_text(encoding="utf-8") == "approved once\n"
            assert len([item for item in fixture.requests if item["method"] == "tools/call"]) == 1
        finally:
            client.__exit__(None, None, None)
