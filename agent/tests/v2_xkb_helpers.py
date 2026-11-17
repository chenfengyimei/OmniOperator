"""Independent XKB 1.0 reply layouts from the Xorg protocol; no native display or input."""

import struct

from v2_x11_helpers import reply


class XkbFixture:
    def __init__(self):
        self.supported = True
        self.event_base = 80
        self.device = 3
        self.state = bytes(18)
        self.enabled = 513
        self.actions = None
        self.explicit = False
        self.fault = None
        self.hook = None
        self.reads = 0

    async def respond(self, peer, packet):
        peer.sequence += 1
        opcode, minor = packet[:2]
        body = packet[4:]
        peer.sent.append((opcode, minor, body))
        if opcode == 98:
            assert body == b"\x09\x00\x00\x00XKEYBOARD\x00\x00\x00"
            result = reply(peer.sequence, fields=bytes([1, 131, self.event_base, 0]))
        elif minor == 0:
            assert body == b"\x01\x00\x00\x00"
            result = reply(peer.sequence, detail=int(self.supported), fields=struct.pack("<HH", 1, 0))
        elif minor == 1:
            assert struct.unpack("<6H", body) == (256, 143, 0, 143, 255, 255)
            return
        elif minor in {4, 6}:
            assert body == b"\x00\x01\x00\x00"
            if minor == 4:
                result = bytearray(reply(peer.sequence, detail=self.device))
                result[8:26] = self.state
            else:
                result = bytearray(92)
                result[:32] = reply(peer.sequence, detail=self.device)
                struct.pack_into("<I", result, 4, 15)
                result[9] = 1
                struct.pack_into("<HH", result, 20, 500, 33)
                struct.pack_into("<I", result, 56, self.enabled)
                if self.fault == "controls_device":
                    result[1] = 4
                if self.fault == "internal_modifier":
                    result[11] = 2
                if self.fault == "groups":
                    result[9] = 5
                if self.fault == "controls_length":
                    result += b"four"
                    struct.pack_into("<I", result, 4, 16)
        elif minor == 8:
            assert len(body) == 24 and struct.unpack_from("<HHH", body) == (256, 0, 190)
            code = body[8]
            assert tuple(body[6:14]) == (0, 0, code, 1, code, 1, code, 1)
            assert tuple(body[14:22]) == (0, 0, code, 1, code, 1, code, 1)
            self.reads += 1
            symbols = [peer.rows[code][0]]
            if self.fault == "different_symbol":
                symbols = [0xFF1B]
            if self.fault == "missing_symbol":
                symbols = [0]
            count = len(symbols)
            actions = [] if self.actions is None else self.actions
            result = bytearray(40)
            result[:32] = reply(peer.sequence, detail=self.device)
            result[10:18] = bytes([8, 255, 190, 0, 0, 0, 4, code])
            struct.pack_into("<H", result, 18, count)
            result[20:22] = bytes([1, code])
            struct.pack_into("<H", result, 22, len(actions))
            result[24:31] = bytes([1, code, 1, 0, code, 1, int(self.explicit)])
            result[31:37] = bytes([code, 1, 0, code, 1, 0])
            # Vary only unused fields/padding on each reply. A stable proof
            # must ignore those, while retaining every requested component.
            result += bytes([0, self.reads, 0, 0, 1, 1]) + struct.pack("<H", count)
            result += struct.pack("<" + "I" * count, *symbols)
            result += bytes([len(actions), self.reads, 0, 0])
            for kind in actions:
                result += bytes([kind, self.reads, 0, 0, 0, 0, 0, 0])
            if self.explicit:
                result += bytes([code, 128, self.reads, 0])
            if self.fault == "parts":
                result[12] = 174
            elif self.fault == "range":
                result[20] = 2
            elif self.fault == "bounds":
                result[10] = 9
            elif self.fault == "behavior":
                result[27] = 1
            elif self.fault == "modifier":
                result[33] = 1
            elif self.fault == "virtual_modifier":
                result[36] = 1
            elif self.fault == "action_count":
                result[52] = 1
            elif self.fault == "oversized_symbols":
                struct.pack_into("<H", result, 18, 65)
            elif self.fault == "trailing":
                result += b"four"
            struct.pack_into("<I", result, 4, (len(result) - 32) // 4)
        else:
            raise AssertionError((opcode, minor, body))
        if peer.event is not None:
            peer.incoming.extend(peer.event)
            peer.event = None
        peer.incoming.extend(result)
        if self.hook is not None:
            self.hook(peer, minor)
