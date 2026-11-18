"""Pinned administrator transport using injected streams; no root process, Unix endpoint or native command."""

from __future__ import annotations

import asyncio
import json

import pytest
from omni_test_support.v2.service_admin import SHA, operation

from omni.adapters.platform.services.service_admin_client import PrivilegedServiceClient
from omni.domain.contracts import BusinessError
from omni.domain.service_admin import decode, encode, response, socket_path


class Writer:
    def __init__(self, reader):
        self.reader = reader
        self.requests = []
        self.hold = False
        self.corrupt = False
        self.cleanup = True
        self.half_closed = self.closed = False
        self.current = None

    def write(self, data):
        value = decode(data)
        self.requests.append(value)
        self.current = value
        if not self.hold:
            self.reply()

    def reply(self):
        value = self.current
        if value["command"] == "close":
            report = {"closed": self.cleanup, "cleanup_pending": not self.cleanup}
        elif value["command"] == "change":
            report = {"effect": "applied", "error_code": ""}
        else:
            report = {
                "available": True,
                "installed": False,
                "definition_present": False,
                "definition_matches": None,
                "enabled": False,
                "state": "stopped",
                "pid": None,
                "error_code": "",
            }
        result = response(value, report)
        if self.corrupt:
            result["broker_sha256"] = "b" * 64
        self.reader.feed_data(encode(result))

    async def drain(self):
        pass

    def can_write_eof(self):
        return True

    def write_eof(self):
        self.half_closed = True

    def close(self):
        self.closed = True
        self.reader.feed_eof()

    async def wait_closed(self):
        pass


def client(monkeypatch, value=None):
    value = operation() if value is None else value
    native = PrivilegedServiceClient(value, socket_path(value), SHA)
    reader = asyncio.StreamReader()
    writer = Writer(reader)
    connections = []

    async def connect():
        connections.append(True)
        native._reader, native._writer = reader, writer

    monkeypatch.setattr(native, "_connect", connect)
    return native, writer, connections


def test_transport_binds_deployment_action_broker_and_one_connection_without_retry(monkeypatch):
    async def scenario():
        value = operation()
        native, writer, connections = client(monkeypatch, value)
        report = await native.inspect(value["deployment"])
        assert report["installed"] is False and report["admin_broker_sha256"] == SHA
        assert (await native.change(value["deployment"], "install"))["effect"] == "applied"
        with pytest.raises(BusinessError, match="approved administrator action"):
            await native.change(value["deployment"], "uninstall")
        with pytest.raises(BusinessError, match="another deployment"):
            await native.inspect({})
        assert await native.close(1)
        assert len(connections) == 1 and writer.closed
        assert [value["command"] for value in writer.requests] == ["inspect", "change", "close"]
        assert [value["sequence"] for value in writer.requests] == [0, 1, 2]

    asyncio.run(scenario())


def test_cancelled_waiter_retains_actual_request_and_close_ownership(monkeypatch):
    async def scenario():
        value = operation()
        native, writer, _ = client(monkeypatch, value)
        writer.hold = True
        caller = asyncio.create_task(native.change(value["deployment"], "install"))
        for _ in range(100):
            if writer.requests:
                break
            await asyncio.sleep(0)
        assert len(writer.requests) == 1
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert not await native.close(0) and not writer.closed
        writer.hold = False
        writer.reply()
        assert await native.close(1)
        assert [value["command"] for value in writer.requests] == ["change", "close"]

    asyncio.run(scenario())


def test_cleanup_residual_and_bad_reply_cannot_claim_a_closed_root_backend(monkeypatch):
    async def scenario():
        value = operation()
        native, writer, _ = client(monkeypatch, value)
        await native.inspect(value["deployment"])
        writer.cleanup = False
        assert not await native.close(1) and not writer.closed
        writer.cleanup = True
        assert await native.close(1)
        native, writer, _ = client(monkeypatch, value)
        writer.corrupt = True
        report = await native.change(value["deployment"], "install")
        assert report["effect"] == "unknown" and len(writer.requests) == 1
        assert not await native.close(1) and not writer.closed and writer.half_closed
        # Fixture-only resource disposal. Production retains ownership until
        # explicit reconciliation; this is not an automatic cleanup success.
        writer.close()

    asyncio.run(scenario())


def test_wrong_or_missing_endpoint_never_falls_back_to_owner_code_or_elevation(monkeypatch):
    async def scenario():
        value = operation()
        with pytest.raises(BusinessError):
            PrivilegedServiceClient(value, "/tmp/arbitrary.sock", SHA)
        native = PrivilegedServiceClient(value, socket_path(value), SHA)

        async def missing():
            raise OSError("credential=never expose")

        monkeypatch.setattr(native, "_connect", missing)
        report = await native.inspect(value["deployment"])
        assert report["available"] is False and report["installed"] is None
        assert "never expose" not in json.dumps(report)
        assert await native.close(1)

    asyncio.run(scenario())
