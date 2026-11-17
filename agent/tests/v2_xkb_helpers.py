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
        self.clone_fault = None
        self.clone_version = 0
        self.full_hook = None
        self.full_reads = []
        self.copy_fault = None
        self.copy_reads = []
        self.copy_common = None
        self.geometry_present = True

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
            selected, *masks = struct.unpack("<6H", body)
            assert selected in {256, 5} and masks == [239, 0, 239, 255, 255]
            return
        elif minor in {4, 6}:
            selected = struct.unpack("<Hxx", body)[0]
            assert selected in {256, 5}
            device = self.device if selected == 256 else selected
            if minor == 4:
                result = bytearray(reply(peer.sequence, detail=device))
                result[8:26] = self.state
                if selected == 5 and self.clone_fault == "state":
                    result[8] = 2
            else:
                result = bytearray(92)
                result[:32] = reply(peer.sequence, detail=device)
                struct.pack_into("<I", result, 4, 15)
                result[9] = 1
                struct.pack_into("<HH", result, 20, 500, 33)
                struct.pack_into("<I", result, 56, self.enabled)
                if selected == 5 and self.clone_fault == "controls":
                    struct.pack_into("<H", result, 20, 501)
                if self.fault == "controls_device":
                    result[1] = 4
                if self.fault == "internal_modifier":
                    result[11] = 2
                if self.fault == "groups":
                    result[9] = 5
                if self.fault == "controls_length":
                    result += b"four"
                    struct.pack_into("<I", result, 4, 16)
        elif minor in {10, 13, 17, 19}:
            from v2_xkb_copy_helpers import copied_reply

            selected = struct.unpack_from("<H", body)[0]
            assert selected in {256, 5}
            expected = (
                struct.pack("<HBBHH", selected, 15, 1, 0, 0)
                if minor == 10
                else struct.pack("<HxxI", selected, {13: 0xFFFFFFFF, 17: 0x3FFF, 19: 0}[minor])
            )
            assert body == expected
            self.reads += 1
            self.copy_reads.append((selected, minor))
            result = bytearray(
                copied_reply(
                    minor,
                    peer.sequence,
                    self.device if selected == 256 else selected,
                    padding=self.reads % 256,
                    found=self.geometry_present,
                )
            )
            if selected == 5 and self.copy_fault == minor:
                result[32] ^= 1
            if self.copy_common == minor:
                result[8 if minor == 19 else 32] ^= 2
        elif minor == 8 and struct.unpack_from("<H", body, 2)[0] == 255:
            from v2_xkb_map_helpers import complete_map

            selected, full, partial = struct.unpack_from("<HHH", body)
            assert selected in {256, 5} and full == 255 and partial == 0 and body[6:] == b"\0" * 18
            self.reads += 1
            self.full_reads.append(selected)
            rows = dict(peer.rows)
            if selected == 5 and self.clone_fault == "symbol":
                rows[100] = (ord("x"),)
            result = bytearray(
                complete_map(
                    peer.sequence, self.device if selected == 256 else selected, rows, padding=self.reads % 256
                )
            )
            result[40] = self.clone_version
            if selected == 5 and self.clone_fault == "device":
                result[1] = 6
            if selected == 5 and self.clone_fault == "type":
                result[40] = 2
            if selected == 5 and self.clone_fault == "modifier":
                result[-7] = 2
            if self.full_hook is not None:
                self.full_hook(peer, selected)
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
