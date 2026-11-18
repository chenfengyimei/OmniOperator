"""Reusable linux_keystroke fixtures, extracted without collecting test cases."""

import asyncio
import struct

import pytest

from omni.adapters.platform.desktop.linux.linux_input import KeyEvents
from omni.domain.contracts import BusinessError
from omni_test_support.v2.linux_foreground import prepared
from omni_test_support.v2.linux_keyboard import KeyboardPeer
from omni_test_support.v2.linux_xtest import SourcePeer


def raw(sequence, category, device, *, source=5, code=40, timestamp=1000, words=2, flags=0, values=None):
    data = bytearray(32)
    data[0:2] = bytes([35, 130])
    struct.pack_into("<HIHHIIHHI", data, 2, sequence, words, category, device, timestamp, code, source, words, flags)
    return bytes(data) + (b"\0" * (words * 4) if values is None else values)


class KeyPeer(KeyboardPeer, SourcePeer):
    def __init__(self):
        super().__init__()
        self.input_requests = []
        self.stream = bytearray()
        self.write_plan = []
        self.raw_fault = None
        self.block_query = False
        self.query_started = asyncio.Event()
        self.query_release = asyncio.Event()
        self.query_count = 0
        self.owner = None

    @property
    def closed(self):
        return self.owner is not None and self.owner.closed

    def send(self, data):
        assert not self.closed
        if self.write_plan:
            choice = self.write_plan.pop(0)
            if isinstance(choice, BaseException):
                raise choice
            size = min(choice, len(data))
        else:
            size = len(data)
        self.stream.extend(data[:size])
        while len(self.stream) >= 36:
            request = bytes(self.stream[:36])
            del self.stream[:36]
            # Independently encoded public XTEST FakeInput layout, including
            # zero time/root/coordinates/core-device and exact request length.
            kind = request[4]
            assert request == bytes([132, 2, 9, 0, kind, 40, 0, 0]) + b"\0" * 28
            assert kind in {2, 3}
            self.sequence += 1
            self.input_requests.append(request)
            category = 13 if kind == 2 else 14
            slave = raw(self.sequence, category, 5)
            master = raw(self.sequence, category, 3)
            fault = self.raw_fault
            if fault == "source":
                slave = raw(self.sequence, category, 5, source=6)
            elif fault == "sequence":
                slave = raw(self.sequence - 1, category, 5)
            elif fault == "flags":
                slave = raw(self.sequence, category, 5, flags=1 << 16)
            elif fault == "valuators":
                slave = raw(self.sequence, category, 5, values=b"\1" + b"\0" * 7)
            elif fault == "order":
                slave, master = master, slave
            if fault != "missing":
                self.incoming.extend(slave + master)
            if fault == "other_client" and kind == 2:
                self.incoming.extend(slave + master)
            if fault == "device_change":
                event = bytearray(32)
                event[0:2] = bytes([35, 130])
                struct.pack_into("<H", event, 8, 1)
                self.incoming.extend(event)
        return size

    async def sock_sendall(self, connection, packet):
        if self.block_query and packet[0] == 44:
            self.query_count += 1
            self.query_started.set()
            await self.query_release.wait()
        return await super().sock_sendall(connection, packet)


async def packet_context(peer):
    _factory, inspector, leases, alias, monitor, _connections, focus = await prepared(peer)
    await focus.prepare(alias)
    await focus.prepare_key("Enter")
    await focus.prepare_sources()
    packet = await focus.key_packet()
    peer.owner = monitor.connection
    packet.wire.connection = peer
    return inspector, leases, monitor, focus, packet


async def cleanup(inspector, leases, monitor):
    # A negative settlement assertion must not be turned into success by test
    # cleanup; close the injected resources directly after recording it.
    assert await monitor.connection.close()
    assert await leases.desktop.connection.close()
    assert await inspector.close()


__all__ = [
    "BusinessError",
    "KeyEvents",
    "KeyPeer",
    "KeyboardPeer",
    "SourcePeer",
    "asyncio",
    "cleanup",
    "packet_context",
    "prepared",
    "pytest",
    "raw",
    "struct",
]
