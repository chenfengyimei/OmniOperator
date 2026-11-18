"""Reusable linux_xtest fixtures, extracted without collecting test cases."""

import asyncio
import struct

import pytest

from omni.adapters.platform.desktop.linux.linux_input import device_topology, devices
from omni.adapters.platform.desktop.linux.linux_xtest import Sources, XTestSources
from omni.domain.contracts import BusinessError
from omni_test_support.v2.linux_foreground import FocusPeer, prepared
from omni_test_support.v2.v2_x11_helpers import reply


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


__all__ = [
    "BusinessError",
    "FocusPeer",
    "SourcePeer",
    "Sources",
    "XTestSources",
    "asyncio",
    "close",
    "device_topology",
    "devices",
    "prepared",
    "pytest",
    "reply",
    "struct",
    "topology_bytes",
]
