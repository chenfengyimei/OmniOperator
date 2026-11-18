"""MCP business ownership spans database admission, transport work and final commit; all external ports are isolated."""

import asyncio
import sys
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from omni_test_support.v2.mcp import target
from omni_test_support.v2.mcp_flow import SeededSecrets
from omni_test_support.v2.media import NoModel
from omni_test_support.v2.v2_mcp_helpers import TOOLS, stdio_server

from omni.adapters.mcp.hosts import MCPHosts
from omni.adapters.primitives import SystemClock
from omni.application.mcp import MAX_PENDING_OPERATIONS, MCPService
from omni.composition.http import http_app
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError, Effect, InvocationContext, Outcome
from omni.domain.mcp import argument_digest


class Transport:
    def __init__(self):
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.closes = 0
        self.closing = False

    async def connect(self, target):
        self.entered.set()
        await self.release.wait()
        return {}

    async def catalog(self, target):
        return TOOLS

    async def stop(self, *args):
        return True

    def health(self):
        return {"pending": 0, "resident": 0, "sessions": []}

    def stop_accepting(self):
        self.closing = True

    async def close(self, timeout_s):
        self.closes += 1
        return True


@pytest.mark.parametrize("cancel_waiter", [False, True])
def test_business_connect_keeps_database_and_session_before_transport_reports_pending(tmp_path, cancel_waiter):
    async def scenario():
        transport = Transport()
        instance = await open_instance(
            tmp_path / "home", model=NoModel(), credential_backend=SeededSecrets(), mcp_transport=transport
        )
        caller = None
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            definition, _ = stdio_server(tmp_path / "server", "ok", tmp_path / "calls.log")
            config = await instance.configurations.create(agent, "mcp", "notes", True, definition)
            caller = asyncio.create_task(instance.mcp.connect(agent, config["id"], config["revision"]))
            await transport.entered.wait()
            owned = tuple(instance.mcp._pending)
            if cancel_waiter:
                caller.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await caller
            assert not await instance.mcp.close(0.01)
            assert transport.closes == 0 and not instance.database.metrics["closed"]
            assert not instance.mcp.quiescent()
            transport.release.set()
            assert (await asyncio.gather(*owned))[0]["connected"]
            assert await instance.mcp.close() and transport.closes == 1 and instance.mcp.quiescent()
        finally:
            transport.release.set()
            if caller is not None:
                await asyncio.gather(caller, return_exceptions=True)
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


class Database:
    def __init__(self, pause=""):
        self.pause, self.current = pause, ""
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.calls, self.finished, self.state = [], [], None

    async def execute(self, operation):
        self.current = ""
        value = operation(self)
        if self.current == self.pause:
            self.entered.set()
            await self.release.wait()
        return value

    read = write = execute

    def note(self, name):
        self.current = name
        self.calls.append(name)

    def mcp_server_get(self, *args):
        self.note("server")
        return {"id": "server"}

    def mcp_dispatch(self, *args):
        self.note("dispatch")
        return {
            "arguments_sha256": argument_digest({"q": "owned"}),
            "config_id": "server",
            "tool_name": "search",
            "authority_epoch": 1,
            "read_only": True,
        }

    def mcp_configuration(self, *args):
        self.note("configuration")
        return {
            "kind": "mcp",
            "enabled": True,
            "id": "server",
            "revision_id": "revision",
            "agent_id": "agent",
            "name": "notes",
            "value": {"transport": "stdio", "command": sys.executable, "arguments": [], "auth_mode": "none"},
        }

    def mcp_call_begin(self, *args):
        self.note("begin")
        self.state = "prepared"
        return {"id": "call"}

    def mcp_call_dispatched(self, *args):
        self.note("dispatched")
        self.state = "dispatched"

    def mcp_call_get(self, *args):
        self.note("get_call")
        return {"id": "call", "state": self.state}

    def mcp_call_finish(self, agent, call, state, code="", *args, **kwargs):
        self.note("finish")
        self.finished.append((state, code))
        self.state = state
        return {"id": "call", "state": state}


class CallTransport(Transport):
    def __init__(self):
        super().__init__()
        self.calls = self.cancellations = 0
        self.sends = 0
        self.cleanup_entered, self.cleanup_release = asyncio.Event(), asyncio.Event()
        self.pause = False

    async def call(self, target, name, arguments, timeout_s, *, before_send):
        self.calls += 1
        await before_send()
        self.sends += 1
        if self.pause:
            self.entered.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancellations += 1
                self.cleanup_entered.set()
                await self.cleanup_release.wait()
                raise
        return {"content": [{"type": "text", "text": "actual response"}]}


@pytest.mark.parametrize("pause", ["dispatch", "configuration", "begin", "finish"])
def test_cancelled_invocation_keeps_original_database_delivery_and_known_call_settlement(pause):
    async def scenario():
        database, transport = Database(pause), CallTransport()
        service = MCPService(database, transport, SystemClock())
        caller = asyncio.create_task(
            service.invoke(
                "mcp__notes__search",
                {"q": "owned"},
                operation_id="action",
                context=InvocationContext("agent", "run", "session", 1),
            )
        )
        try:
            await database.entered.wait()
            owned = tuple(service._pending)
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert not await service.close(0.01) and not service.quiescent() and transport.closes == 0
            assert not owned[0].done()
            database.release.set()
            await asyncio.gather(*owned, return_exceptions=True)
            assert await service.close() and service.quiescent()
            if pause in {"dispatch", "configuration"}:
                assert database.state is None and not database.finished and transport.calls == 0
            elif pause == "begin":
                assert database.finished == [("cancelled", "mcp_cancelled")] and transport.calls == 0
            else:
                assert database.finished == [("done", "")] and transport.calls == 1
        finally:
            database.release.set()
            transport.cleanup_release.set()
            transport.release.set()
            await asyncio.gather(caller, return_exceptions=True)
            await service.close()

    asyncio.run(scenario())


def test_repeated_close_cannot_cancel_original_call_release_or_settle_dispatched_as_not_sent():
    async def scenario():
        database, transport = Database(), CallTransport()
        transport.pause = True
        service = MCPService(database, transport, SystemClock())
        caller = asyncio.create_task(
            service.invoke(
                "mcp__notes__search",
                {"q": "owned"},
                operation_id="action",
                context=InvocationContext("agent", "run", "session", 1),
            )
        )
        try:
            await transport.entered.wait()
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            await transport.cleanup_entered.wait()
            assert not await service.close(0.01) and not await service.close(0.01)
            assert transport.cancellations == 1 and not service.quiescent() and database.state == "dispatched"
            owned = tuple(service._pending)
            transport.cleanup_release.set()
            await asyncio.gather(*owned, return_exceptions=True)
            assert database.finished == [("unknown", "mcp_cancelled")] and transport.calls == 1
            assert await service.close() and service.quiescent()
        finally:
            transport.cleanup_release.set()
            transport.release.set()
            await asyncio.gather(caller, return_exceptions=True)
            await service.close()

    asyncio.run(scenario())


ENTRIES = {
    "servers": lambda s: s.servers("a"),
    "server": lambda s: s.server("a", "c"),
    "connect": lambda s: s.connect("a", "c", 1),
    "disconnect": lambda s: s.disconnect("a", "c"),
    "reset": lambda s: s.reset("a", "c"),
    "catalog": lambda s: s.catalog("a", "c"),
    "authorize": lambda s: s.authorize("a", "c", {}),
    "authorize_tool": lambda s: s.authorize_tool(
        "a", "c", "t", authorized=True, read_only=True, catalog_fingerprint="f", evidence="e"
    ),
    "revoke": lambda s: s.revoke("a", "c"),
    "calls": lambda s: s.calls("a", "c"),
    "oauth_start": lambda s: s.oauth_start("a", "c", "uri"),
    "oauth_callback": lambda s: s.oauth_callback("state", "code"),
    "tools_for_run": lambda s: s.tools_for_run("a", "r"),
    "recover": lambda s: s.recover(),
}


@pytest.mark.parametrize("entry", ENTRIES)
def test_closed_business_entrypoints_refuse_before_first_database_or_transport_operation(entry):
    async def scenario():
        database, transport = Database(), Transport()
        service = MCPService(database, transport, SystemClock())
        service.stop_accepting()
        with pytest.raises(BusinessError) as caught:
            await ENTRIES[entry](service)
        assert caught.value.code == "mcp_closing" and not database.calls and not transport.closes
        assert await service.close()

    asyncio.run(scenario())


def test_exact_business_capacity_survives_cancelled_waiters_until_original_reads_finish():
    async def scenario():
        database, transport = Database("server"), Transport()
        service = MCPService(database, transport, SystemClock())
        callers = [asyncio.create_task(service.server("a", "c")) for _ in range(MAX_PENDING_OPERATIONS)]
        try:
            while len(database.calls) < MAX_PENDING_OPERATIONS:
                await asyncio.sleep(0)
            with pytest.raises(BusinessError) as caught:
                await service.server("a", "c")
            assert caught.value.code == "mcp_busy" and len(database.calls) == MAX_PENDING_OPERATIONS
            result = await service.invoke(
                "mcp__notes__search",
                {"q": "owned"},
                operation_id="action",
                context=InvocationContext("agent", "run", "session", 1),
            )
            assert result.status == Outcome.ERROR and result.error_code == "mcp_busy"
            assert len(database.calls) == MAX_PENDING_OPERATIONS
            for caller in callers:
                caller.cancel()
            await asyncio.gather(*callers, return_exceptions=True)
            assert service.health()["pending"] == MAX_PENDING_OPERATIONS and not await service.close(0.01)
            owned = tuple(service._pending)
            database.release.set()
            await asyncio.gather(*owned)
            assert await service.close() and service.health()["pending"] == 0 and transport.closes == 1
        finally:
            database.release.set()
            await asyncio.gather(*callers, return_exceptions=True)
            await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["start", "stale"])
@pytest.mark.parametrize("cleanup", ["false", "error"])
def test_unreaped_failed_or_stale_session_stays_owned_and_blocks_replacement(tmp_path, phase, cleanup):
    class Session:
        def __init__(self):
            self.reclaimed = False
            self.starts = self.closes = 0

        async def start(self):
            self.starts += 1
            if phase == "start":
                raise BusinessError("fixture_start_failed", "Injected failure")
            return {}

        async def close(self, timeout_s=5):
            self.closes += 1
            if cleanup == "error" and not self.reclaimed:
                raise BusinessError("fixture_close_failed", "Injected close error")
            return self.reclaimed

        def health(self):
            # Retention is based on the failed close, even when the port omits it.
            return {"cleanup_pending": False}

    async def scenario():
        sessions = []

        def factory(value):
            item = Session()
            sessions.append(item)
            return item

        async def authorization(value):
            return ""

        hosts = MCPHosts(authorization, cwd=str(tmp_path), session_factory=factory)
        first = target()
        try:
            if phase == "start":
                with pytest.raises(BusinessError):
                    await hosts.connect(first)
                requested = first
            else:
                await hosts.connect(first)
                requested = replace(first, authority_epoch=2)
                with pytest.raises(BusinessError):
                    await hosts.connect(requested)
            assert hosts.health()["resident"] == 1 and hosts.health()["sessions"][0]["authority_epoch"] == 1
            assert hosts.health()["sessions"][0]["cleanup_pending"]
            assert not hosts.health()["sessions"][0]["ready"]
            with pytest.raises(BusinessError) as caught:
                await hosts.catalog(first)
            assert caught.value.code == "mcp_cleanup_pending"
            assert not MCPService(Database(), hosts, SystemClock()).quiescent()
            with pytest.raises(BusinessError):
                await hosts.connect(requested)
            assert len(sessions) == 1 and sessions[0].starts == 1
            sessions[0].reclaimed = True
            assert await hosts.close() and hosts.health()["resident"] == 0
        finally:
            for item in sessions:
                item.reclaimed = True
            assert await hosts.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["dispatch", "dispatched"])
def test_cancel_during_before_send_retains_authority_read_and_dispatch_commit(phase):
    class BeforeSendDatabase(Database):
        async def execute(self, operation):
            self.current = ""
            value = operation(self)
            if self.current == phase and (phase != "dispatch" or self.calls.count("dispatch") == 2):
                self.entered.set()
                await self.release.wait()
            return value

        read = write = execute

    async def scenario():
        database, transport = BeforeSendDatabase(), CallTransport()
        service = MCPService(database, transport, SystemClock())
        caller = asyncio.create_task(
            service.invoke(
                "mcp__notes__search",
                {"q": "owned"},
                operation_id="action",
                context=InvocationContext("agent", "run", "session", 1),
            )
        )
        try:
            await database.entered.wait()
            owned = tuple(service._pending)
            caller.cancel()
            await asyncio.gather(caller, return_exceptions=True)
            assert not await service.close(0.01) and not owned[0].done()
            assert not await service.close(0.01) and transport.sends == transport.closes == 0
            database.release.set()
            await asyncio.gather(*owned, return_exceptions=True)
            expected = "unknown" if phase == "dispatched" else "cancelled"
            assert database.finished == [(expected, "mcp_cancelled")] and transport.sends == 0
            assert await service.close() and service.quiescent()
        finally:
            database.release.set()
            await asyncio.gather(caller, return_exceptions=True)
            await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("result", ["true", "false", "error"])
def test_transport_close_waiter_cancellation_retains_original_cleanup_and_explicit_retry(result):
    class ClosingTransport(Transport):
        async def close(self, timeout_s):
            self.closes += 1
            self.entered.set()
            await self.release.wait()
            if result == "error" and self.closes == 1:
                raise BusinessError("fixture_close_failed", "Injected cleanup failure")
            return result != "false" or self.closes > 1

    async def scenario():
        transport = ClosingTransport()
        service = MCPService(Database(), transport, SystemClock())
        caller = asyncio.create_task(service.close())
        try:
            await transport.entered.wait()
            original = service._transport_close
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert not await service.close(0.01) and service._transport_close is original
            assert transport.closes == 1 and service.health()["cleanup_pending"] and not service.quiescent()
            transport.release.set()
            await asyncio.gather(original, return_exceptions=True)
            assert service.quiescent() is (result == "true")
            assert await service.close() and service.quiescent()
            assert transport.closes == (1 if result == "true" else 2)
        finally:
            transport.release.set()
            await asyncio.gather(caller, return_exceptions=True)
            await service.close()

    asyncio.run(scenario())


def test_oauth_callback_cancelled_waiter_keeps_original_exchange_and_authority_commit():
    class OAuthDatabase(Database):
        def mcp_oauth_authorized(self, agent, server):
            self.note("oauth_commit")
            return {"name": "remote", "state": "idle", "authority_epoch": 2}

    class OAuthTransport(Transport):
        async def oauth_callback(self, state, code):
            self.exchanges += 1
            return {"agent_id": "agent", "config_id": "server", "scope": "mcp.read"}

    async def scenario():
        database, transport = OAuthDatabase("oauth_commit"), OAuthTransport()
        transport.exchanges = 0
        service = MCPService(database, transport, SystemClock())
        caller = asyncio.create_task(service.oauth_callback("state", "code"))
        try:
            await database.entered.wait()
            owned = tuple(service._pending)
            caller.cancel()
            await asyncio.gather(caller, return_exceptions=True)
            assert not await service.close(0.01) and transport.exchanges == 1 and transport.closes == 0
            database.release.set()
            assert (await asyncio.gather(*owned))[0]["authority_epoch"] == 2
            assert database.calls == ["oauth_commit"] and transport.exchanges == 1
            assert await service.close()
        finally:
            database.release.set()
            await asyncio.gather(caller, return_exceptions=True)
            await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["lock", "stale_cleanup"])
def test_connect_rechecks_closing_after_waiting_for_lock_or_original_cleanup(tmp_path, phase):
    class Session:
        def __init__(self):
            self.entered, self.release = asyncio.Event(), asyncio.Event()

        async def start(self):
            return {}

        async def close(self, timeout_s):
            self.entered.set()
            await self.release.wait()
            return True

        def health(self):
            return {}

    async def scenario():
        sessions = []

        def factory(value):
            item = Session()
            sessions.append(item)
            return item

        async def authorization(value):
            return ""

        hosts = MCPHosts(authorization, cwd=str(tmp_path), session_factory=factory)
        caller = None
        original_target = target()
        try:
            if phase == "lock":
                await hosts._lock.acquire()
                caller = asyncio.create_task(hosts.connect(original_target))
                await asyncio.sleep(0)
                hosts.stop_accepting()
                hosts._lock.release()
            else:
                await hosts.connect(original_target)
                caller = asyncio.create_task(hosts.connect(replace(original_target, authority_epoch=2)))
                await sessions[0].entered.wait()
                hosts.stop_accepting()
                sessions[0].release.set()
            with pytest.raises(BusinessError) as caught:
                await caller
            assert caught.value.code == "mcp_closing" and len(sessions) == (0 if phase == "lock" else 1)
            assert hosts.health()["resident"] == 0
        finally:
            for item in sessions:
                item.release.set()
            if caller is not None:
                await asyncio.gather(caller, return_exceptions=True)
            assert await hosts.close()

    asyncio.run(scenario())


def test_result_processing_failure_after_response_remains_unknown(monkeypatch):
    def invalid_result(*args):
        raise BusinessError("fixture_result_invalid", "Injected response processing failure")

    monkeypatch.setattr("omni.application.mcp.result_text", invalid_result)

    async def scenario():
        database, transport = Database(), CallTransport()
        service = MCPService(database, transport, SystemClock())
        result = await service.invoke(
            "mcp__notes__search",
            {"q": "owned"},
            operation_id="action",
            context=InvocationContext("agent", "run", "session", 1),
        )
        assert result.status == Outcome.UNKNOWN and result.effect == Effect.UNKNOWN
        assert database.finished == [("unknown", "fixture_result_invalid")] and transport.sends == 1
        assert await service.close()

    asyncio.run(scenario())


def test_closed_model_invocation_returns_action_error_before_database_or_transport():
    async def scenario():
        database, transport = Database(), CallTransport()
        service = MCPService(database, transport, SystemClock())
        service.stop_accepting()
        result = await service.invoke(
            "mcp__notes__search",
            {"q": "owned"},
            operation_id="action",
            context=InvocationContext("agent", "run", "session", 1),
        )
        assert result.status == Outcome.ERROR and result.error_code == "mcp_closing"
        assert not database.calls and transport.calls == 0
        assert await service.close()

    asyncio.run(scenario())


def test_owner_http_closing_responses_are_503_no_store_without_mcp_database_io(tmp_path, monkeypatch):
    with TestClient(http_app(tmp_path, model=NoModel(), credential_backend=SeededSecrets())) as client:
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text(encoding="utf-8")
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        service = client.app.state.runtime.mcp
        database = Database()
        client.portal.call(service.stop_accepting)
        with monkeypatch.context() as patch:
            patch.setattr(service, "_db", database)
            base = f"/api/v1/agents/{agent}/mcp/servers"
            server = base + "/11111111-1111-1111-1111-111111111111"
            for path in (base, server, server + "/catalog", server + "/calls"):
                response = client.get(path)
                assert response.status_code == 503 and response.headers["cache-control"] == "no-store"
                assert response.json()["error"]["code"] == "mcp_closing"
            assert not database.calls
