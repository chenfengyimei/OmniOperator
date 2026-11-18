"""Actual pointer/source readers over independent Xorg mapping replies; no sockets, maps or devices changed."""

import struct

from omni_test_support.v2.linux_foreground import prepared
from omni_test_support.v2.linux_xtest import SourcePeer, topology_bytes
from omni_test_support.v2.v2_linux_pointer_helpers import HIT, ROOT, PointerPeer
from omni_test_support.v2.v2_x11_helpers import reply


def button_topology(master=3, source=3):
    original = topology_bytes()
    result, position = bytearray(original[:32]), 32
    for _ in range(struct.unpack_from("<H", original, 8)[0]):
        identity, _kind, _attachment, count, size = struct.unpack_from("<HHHHH", original, position)
        end = position + 12 + (size + 3) // 4 * 4
        result.extend(original[position:end])
        position = end
        for _ in range(count):
            category, words = struct.unpack_from("<HH", original, position)
            data = original[position : position + words * 4]
            if category == 1:
                buttons = master if identity == 2 else source
                masks = (buttons + 31) // 32
                data = struct.pack("<HHHH", 1, 2 + masks + buttons, identity, buttons) + bytes(masks * 4)
                data += b"".join(struct.pack("<I", 90 + i) for i in range(buttons))
            result.extend(data)
            position += words * 4
    assert position == len(original)
    struct.pack_into("<I", result, 4, (len(result) - 32) // 4)
    return bytes(result)


class ButtonPeer(PointerPeer, SourcePeer):
    def __init__(self, source=(1, 2, 3), master=(1, 2, 3)):
        super().__init__()
        self.maps = {2: bytes(master), 4: bytes(source)}
        self.core = None
        self.topology = button_topology(len(master), len(source))
        self.client_set, self.client_pointer = 1, 2
        self.event_base = 64
        self.logical_mask = 0
        self.mapping_padding = 0
        self.mapping_fault = None
        self.map_reads = []
        self.on_mapping = None
        self.on_subscribe = None
        self.on_client = None

    async def sock_sendall(self, connection, packet):
        if not self.authenticated:
            return await super().sock_sendall(connection, packet)
        opcode, minor, words = struct.unpack_from("<BBH", packet)
        body = packet[4:]
        if not (
            opcode in {38, 117} or opcode == 98 and b"XInputExtension" in body or opcode == 130 and minor in {6, 28, 45}
        ):
            return await super().sock_sendall(connection, packet)
        assert words * 4 == len(packet)
        self.sequence += 1
        self.sent.append((opcode, minor, body))
        fields, extra, detail = b"", b"", 0
        if opcode == 98:
            fields = bytes([1, 130, self.event_base, 0])
        elif opcode == 38:
            assert body == struct.pack("<I", ROOT)
            detail = 1
            fields = struct.pack("<IIhhhhH", ROOT, HIT, 110, 212, 5, 5, self.logical_mask)
        elif opcode == 130 and minor == 6:
            assert struct.unpack("<IHxxII", body) == (
                ROOT,
                2,
                2 << 8 | self.event_base + 11,
                4 << 8 | self.event_base + 11,
            )
            if self.on_subscribe is not None:
                await self.on_subscribe()
            return
        elif opcode == 130 and minor == 45:
            assert body == bytes(4)
            fields = struct.pack("<BxH", self.client_set, self.client_pointer)
            if self.on_client is not None:
                await self.on_client()
        else:
            identity = None if opcode == 117 else struct.unpack("<Bxxx", body)[0]
            mapping = (self.maps[2] if self.core is None else self.core) if identity is None else self.maps[identity]
            size = len(mapping)
            self.map_reads.append(identity)
            detail = size if identity is None else 28
            fields = b"" if identity is None else bytes([size])
            extra = mapping + bytes([self.mapping_padding]) * (-size % 4)
            if self.mapping_fault == "size":
                if identity is None:
                    detail = size - 1
                else:
                    fields = bytes([size - 1])
            elif self.mapping_fault == "reply_type" and identity is not None:
                detail = 29
            elif self.mapping_fault == "trailing":
                extra += bytes(4)
            elif self.mapping_fault == "truncated":
                extra = extra[:-4]
            if self.on_mapping is not None:
                await self.on_mapping(identity)
        if self.event is not None:
            self.incoming.extend(self.event)
            self.event = None
        self.incoming.extend(reply(self.sequence, detail=detail, fields=fields, extra=extra))


async def button_context(peer=None):
    peer = peer or ButtonPeer()
    values = await prepared(peer)
    focus = values[-1]
    await focus.prepare(values[3])
    peer.pointer_mode = True
    await focus.prepare_pointer((10, 12))
    await focus.prepare_sources()
    return peer, *values


def mapping_event(*, device=4, core=False, request=2):
    header = bytearray(32)
    header[:2] = bytes([34 if core else 75, 0 if core else device])
    header[4] = request
    return bytes(header)
