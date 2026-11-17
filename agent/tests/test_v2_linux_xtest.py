"""Xorg source identities on injected native wire peers; no real server, device or synthesized input."""

import asyncio
import struct

import pytest
from test_v2_linux_foreground import FocusPeer, prepared
from v2_x11_helpers import reply

from omni.adapters.platform.linux_input import device_topology, devices
from omni.adapters.platform.linux_xtest import Sources, XTestSources
from omni.domain.contracts import BusinessError


def topology_bytes(*, disabled=False, detached=False, missing_class=False, padding=0):
    records = []
    for identity, kind, attachment, name in (
        (2, 1, 3, b"master pointer"),
        (3, 2, 2, b"master keyboard"),
        (4, 3, 2, b"arbitrary server pointer name"),
        (5, 4, 3, b"arbitrary server keyboard name"),
        (6, 4, 3, b"Virtual core XTEST keyboard"),
    ):
        category = 1 if kind in {1, 3} else 0
        info = (
            struct.pack("<HHHHI", 1, 4, identity, 1, 0) + struct.pack("<I", 99)
            if category
            else struct.pack("<HHHHI", 0, 3, identity, 1, 40)
        )
        classes = int(not (identity == 5 and missing_class))
        records.append(
            struct.pack(
                "<HHHHHBx",
                identity,
                kind,
                0 if identity == 5 and detached else attachment,
                classes,
                len(name),
                int(not (identity == 5 and disabled)),
            )
            + name
            + bytes([padding]) * (-len(name) % 4)
            + (info if classes else b"")
        )
    header = bytearray(32)
    struct.pack_into("<H", header, 8, len(records))
    return bytes(header) + b"".join(records)


class SourcePeer(FocusPeer):
    def __init__(self):
        super().__init__()
        self.topology = topology_bytes()
        self.marked = {4, 5}
        self.version = (2, 2)
        self.opcode = 132
        self.atom = 300
        self.property_fault = None
        self.property_padding = 0
        self.property_reads = []
        self.on_property = None

    async def sock_sendall(self, connection, packet):
        if not self.authenticated:
            return await super().sock_sendall(connection, packet)
        opcode, minor, length = struct.unpack_from("<BBH", packet)
        body = packet[4:]
        if not (opcode in {16, 132} or opcode == 98 and b"XTEST" in body or opcode == 130 and minor == 59):
            return await super().sock_sendall(connection, packet)
        self.sequence += 1
        self.sent.append((opcode, minor, body))
        assert length * 4 == len(packet)
        fields, extra, detail = b"", b"", 0
        if opcode == 98:
            assert b"XTEST" in body
            fields = bytes([1, self.opcode, 0, 0])
        elif opcode == 132:
            assert minor == 0 and body == bytes([2, 0, 2, 0])
            detail, minor_version = self.version
            fields = struct.pack("<H", minor_version)
        elif opcode == 16:
            assert minor == 1 and body == struct.pack("<Hxx", 12) + b"XTEST Device"
            fields = struct.pack("<I", self.atom)
        else:
            identity, delete, atom, kind, offset, count = struct.unpack("<HBxIIII", body)
            assert (delete, atom, kind, offset, count) == (0, 300, 0, 0, 1)
            self.property_reads.append(identity)
            if identity in self.marked:
                prop_type, remaining, items, format, value = 19, 0, 1, 8, 1
                fault = self.property_fault
                if fault == "type":
                    prop_type = 4
                elif fault == "remaining":
                    remaining = 1
                elif fault == "count":
                    items = 2
                elif fault == "format":
                    format = 32
                elif fault == "value":
                    value = 0
                fields = struct.pack("<IIIB", prop_type, remaining, items, format)
                extra = bytes([value]) + bytes([self.property_padding]) * 3
                if fault == "length":
                    extra += b"more"
            if self.on_property is not None:
                self.on_property(identity)
        if self.event is not None:
            self.incoming.extend(self.event)
            self.event = None
        self.incoming.extend(reply(self.sequence, fields=fields, extra=extra, detail=detail))


async def close(inspector, leases, focus):
    assert await focus.close()
    assert not leases.desktop.connection.closed
    assert await leases.desktop.connection.close() and await inspector.close()


def test_real_pair_uses_read_only_properties_instead_of_names_and_rechecks_every_device():
    async def scenario():
        peer = SourcePeer()
        _factory, inspector, leases, alias, monitor, _connections, focus = await prepared(peer)
        try:
            await focus.prepare(alias)
            assert await focus.prepare_sources() == Sources(2, 3, 4, 5)
            peer.property_padding = 237
            assert await focus.verify_sources() == Sources(2, 3, 4, 5)
            assert peer.property_reads == [2, 3, 4, 5, 6] * 3
            assert monitor.topology == devices(peer.topology)
            assert device_topology(peer.topology).entries[4].identity == 6
            assert device_topology(peer.topology).fingerprint == devices(topology_bytes(padding=233))
            assert all(opcode in {2, 15, 16, 43, 44, 98, 128, 130, 132} for opcode, _, _ in peer.sent)
            assert not any(opcode == 132 and minor != 0 for opcode, minor, _ in peer.sent)
            assert not any(opcode == 130 and minor not in {46, 47, 48, 59} for opcode, minor, _ in peer.sent)
            before = len(peer.sent)
            with pytest.raises(BusinessError):
                await focus.prepare_sources()
            assert len(peer.sent) == before
        finally:
            await close(inspector, leases, focus)
        with pytest.raises(BusinessError):
            await focus.verify_sources()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault",
    [
        "major_version",
        "minor_version",
        "opcode_collision",
        "missing_atom",
        "missing_pair",
        "duplicate",
        "master_marked",
        "disabled",
        "detached",
        "missing_class",
        "type",
        "remaining",
        "count",
        "format",
        "value",
        "length",
    ],
)
def test_unsupported_or_forged_sources_fail_before_ready_and_cannot_revive(fault):
    async def scenario():
        peer = SourcePeer()
        if fault == "major_version":
            peer.version = (1, 2)
        elif fault == "minor_version":
            peer.version = (2, 1)
        elif fault == "opcode_collision":
            peer.opcode = 130
        elif fault == "missing_atom":
            peer.atom = 0
        elif fault == "missing_pair":
            peer.marked = {4}
        elif fault == "duplicate":
            peer.marked.add(6)
        elif fault == "master_marked":
            peer.marked.add(3)
        elif fault in {"disabled", "detached", "missing_class"}:
            peer.topology = topology_bytes(**{fault: True})
        else:
            peer.property_fault = fault
        _factory, inspector, leases, alias, _monitor, _connections, focus = await prepared(peer)
        try:
            await focus.prepare(alias)
            with pytest.raises(BusinessError):
                await focus.prepare_sources()
            peer.version, peer.opcode, peer.atom, peer.marked = (2, 2), 132, 300, {4, 5}
            peer.topology, peer.property_fault = topology_bytes(), None
            before = len(peer.sent)
            with pytest.raises(BusinessError):
                await focus.verify_sources()
            with pytest.raises(BusinessError):
                await focus.check()
            assert len(peer.sent) == before
        finally:
            await close(inspector, leases, focus)

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["property_event", "raw_virtual_key", "duplicate", "topology", "focus"])
def test_prepared_pair_does_not_exempt_other_xtest_clients_and_latches_later_changes(fault):
    async def scenario():
        peer = SourcePeer()
        _factory, inspector, leases, alias, monitor, _connections, focus = await prepared(peer)
        try:
            await focus.prepare(alias)
            await focus.prepare_sources()
            if fault in {"property_event", "raw_virtual_key"}:
                event = bytearray(32)
                event[0], event[1] = 35, 130
                struct.pack_into("<H", event, 2, peer.sequence)
                struct.pack_into("<HHI", event, 8, 12 if fault == "property_event" else 13, 3, 1000)
                if fault == "property_event":
                    struct.pack_into("<IB", event, 16, 300, 1)
                else:
                    struct.pack_into("<IHHI", event, 16, 40, 5, 0, 0)
                peer.event = bytes(event)
            elif fault == "duplicate":
                peer.marked.add(6)
            elif fault == "topology":
                peer.topology = peer.topology.replace(b"arbitrary", b"different")
            else:
                peer.focus += 10
            with pytest.raises(BusinessError):
                await focus.verify_sources()
            peer.topology, peer.focus, peer.marked = topology_bytes(), focus._lineage[0], {4, 5}
            before = len(peer.sent)
            with pytest.raises(BusinessError):
                await focus.verify_sources()
            assert len(peer.sent) == before
            if fault in {"property_event", "raw_virtual_key"}:
                assert monitor.wire.changed
        finally:
            await close(inspector, leases, focus)

    asyncio.run(scenario())


def test_property_race_inside_a_snapshot_cannot_publish_a_ready_pair():
    async def scenario():
        peer = SourcePeer()
        _factory, inspector, leases, alias, _monitor, _connections, focus = await prepared(peer)
        try:
            await focus.prepare(alias)

            def change(identity):
                if identity == 6:
                    peer.marked.discard(5)

            peer.on_property = change
            with pytest.raises(BusinessError):
                await focus.prepare_sources()
            peer.marked, peer.on_property = {4, 5}, None
            with pytest.raises(BusinessError):
                await focus.verify_sources()
        finally:
            await close(inspector, leases, focus)

    asyncio.run(scenario())


def test_unprepared_source_proof_has_no_native_queries_or_connection_ownership():
    async def scenario():
        peer = SourcePeer()
        _factory, inspector, leases, _alias, monitor, _connections, focus = await prepared(peer)
        try:
            sources = XTestSources(monitor)
            with pytest.raises(BusinessError):
                await sources.verify()
            with pytest.raises(BusinessError):
                await sources.prepare()
            with pytest.raises(BusinessError):
                await focus.prepare_sources()
            assert not peer.sent
        finally:
            await close(inspector, leases, focus)

    asyncio.run(scenario())
