"""Independent Xorg geometry/tree/TranslateCoordinates binary replies; never moves a real pointer."""

import struct

from test_v2_linux_foreground import CHILD, FocusPeer, prepared
from v2_x11_helpers import CLIENT, ROOT, VISUAL, WINDOW, reply

FRAME, HIT, OVERLAY, FOREIGN = 0x600001, WINDOW + 2, 0x800001, 0x800002


class PointerPeer(FocusPeer):
    def __init__(self):
        super().__init__()
        self.nodes = {
            ROOT: [0, 0, 1920, 1080, 0, 24, 1, 2, 0],
            FRAME: [98, 198, 644, 484, 0, 24, 1, 2, 0],
            WINDOW: [2, 2, 640, 480, 0, 24, 1, 2, 0],
            CHILD: [100, 100, 50, 50, 0, 24, 1, 2, 0],
            HIT: [5, 7, 30, 20, 0, 0, 2, 2, 0],
            OVERLAY: [0, 0, 1920, 1080, 0, 0, 2, 2, 1],
            FOREIGN: [5, 7, 30, 20, 0, 0, 2, 2, 0],
        }
        self.parents = {ROOT: 0, FRAME: ROOT, WINDOW: FRAME, CHILD: WINDOW, HIT: WINDOW, OVERLAY: ROOT, FOREIGN: WINDOW}
        self.children = {
            ROOT: [FRAME],
            FRAME: [WINDOW],
            WINDOW: [CHILD, HIT],
            CHILD: [],
            HIT: [],
            OVERLAY: [],
            FOREIGN: [],
        }
        self.winners = {ROOT: FRAME, FRAME: WINDOW, WINDOW: HIT, CHILD: 0, HIT: 0, OVERLAY: 0, FOREIGN: 0}
        self.owners = {CLIENT: 20, 0x600000: 22, 0x800000: 21}
        self.point_fault = None
        self.pointer_mode = False
        self.on_pointer_watch = None
        self.on_translate = None
        self.translations = 0

    def origin(self, window):
        x, y = self.nodes[window][:2]
        parent = self.parents[window]
        if parent:
            px, py = self.origin(parent)
            x, y = x + px, y + py
        return x, y

    async def sock_sendall(self, connection, packet):
        if not self.authenticated:
            return await super().sock_sendall(connection, packet)
        opcode, minor = packet[:2]
        body = packet[4:]
        if opcode not in {2, 3, 14, 15, 40, 128}:
            return await super().sock_sendall(connection, packet)
        self.sequence += 1
        self.sent.append((opcode, minor, body))
        if opcode == 2:
            window, mask, value = struct.unpack("<III", body)
            assert mask == 1 << 11 and value & (1 << 17)
            assert not value & ~((1 << 17) | (1 << 19) | (1 << 21))
            assert bool(value & (1 << 21)) == (window == self.focus)
            if self.pointer_mode and self.on_pointer_watch is not None:
                await self.on_pointer_watch(window, value)
            return
        fields, extra, detail = b"", b"", 0
        fault = self.point_fault if self.pointer_mode else None
        if opcode == 128:
            if minor == 0:
                fields = struct.pack("<HH", 1, 2)
            elif minor == 1:
                fields = struct.pack("<I", len(self.owners))
                extra = b"".join(struct.pack("<II", base, 0x1FFFFF) for base in self.owners)
            else:
                count, base, mask = struct.unpack("<III", body)
                assert minor == 4 and (count, mask) == (1, 2)
                fields = struct.pack("<I", 1)
                extra = struct.pack("<IIII", base, 2, 4, self.owners[base])
        elif opcode == 14:
            window = struct.unpack("<I", body)[0]
            x, y, width, height, border, depth, *_ = self.nodes[window]
            detail = depth
            fields = struct.pack("<IhhHHH", ROOT + (fault == "geometry_root"), x, y, width, height, border)
            if fault == "geometry_length":
                extra = bytes(4)
        elif opcode == 3:
            window = struct.unpack("<I", body)[0]
            node = self.nodes[window]
            header = bytearray(reply(self.sequence, extra=bytes(12)))
            struct.pack_into("<IH", header, 8, 0 if node[6] == 2 else VISUAL, node[6])
            header[26:28] = bytes(node[7:9])
            self.incoming.extend(header)
            return
        elif opcode == 15:
            window = struct.unpack("<I", body)[0]
            children = list(self.children[window])
            if self.pointer_mode and window == WINDOW:
                if fault == "duplicate_child":
                    children.append(children[-1])
                if fault == "many_children":
                    children = list(range(0xA00000, 0xA00000 + 257))
                if fault == "zero_child":
                    children.append(0)
            fields = struct.pack(
                "<IIH", ROOT + (fault == "tree_root"), self.parents[window] + (fault == "tree_parent"), len(children)
            )
            extra = b"".join(struct.pack("<I", child) for child in children)
            if fault == "tree_length":
                extra += bytes(4)
        elif opcode == 40:
            source, destination, x, y = struct.unpack("<IIhh", body)
            self.translations += 1
            if self.on_translate is not None:
                await self.on_translate(source, destination)
            sx, sy = self.origin(source)
            dx, dy = self.origin(destination)
            x, y = x + sx - dx, y + sy - dy
            child = self.winners[destination]
            if destination == ROOT:
                if fault == "root_negative":
                    x = -1
                elif fault == "root_outside":
                    x = 1920
                elif fault == "cycle":
                    child = ROOT
                elif fault == "not_child":
                    child = FOREIGN
                elif fault == "root_only":
                    child = 0
            if destination == WINDOW and fault == "roundtrip":
                x += 1
            fields = struct.pack("<Ihh", child, x, y)
            detail = 0 if fault == "different_screen" else 1
        self.incoming.extend(reply(self.sequence, detail=detail, fields=fields, extra=extra))


async def pointer_context(peer=None):
    peer = peer or PointerPeer()
    values = await prepared(peer)
    await values[-1].prepare(values[3])
    peer.pointer_mode = True
    return peer, *values
