"""Independent XKB GetDeviceInfo layouts over the existing actual window/point/map reader; no native display or input."""

import struct

from test_v2_linux_keyboard import KeyboardPeer
from v2_linux_button_helpers import ButtonPeer, button_context
from v2_x11_helpers import reply


def device_info(sequence=1, device=4, count=3, *, actions=None, trim=True, name=b"pointer", padding=0):
    first, returned = 0, [] if actions is None else list(actions)
    if actions is not None and trim:
        while returned and returned[0] == 0:
            first += 1
            returned.pop(0)
        while returned and returned[-1] == 0:
            returned.pop()
    result = bytearray(reply(sequence, detail=device))
    struct.pack_into("<HHHH6BHH", result, 8, 2, 31, 0, 0, 0, count, first, len(returned), count, 0, 0xFF00, 0xFF00)
    result[26:28] = bytes([padding, padding])
    struct.pack_into("<I", result, 28, 100 + device)
    result += struct.pack("<H", len(name)) + name
    result += bytes([padding]) * (-len(result) % 4)
    result += b"".join(bytes([kind]) + bytes([padding]) * 7 for kind in returned)
    struct.pack_into("<I", result, 4, (len(result) - 32) // 4)
    return bytes(result)


class PointerActionPeer(ButtonPeer, KeyboardPeer):
    def __init__(self, source=(1, 2, 3), master=(1, 2, 3)):
        super().__init__(source, master)
        self.pointer_actions = {2: None, 4: None}
        self.pointer_names = {2: b"master", 4: b"source"}
        self.info_padding = 0
        self.info_trim = True
        self.info_reads = []
        self.pointer_selects = []
        self.info_fault = None
        self.on_action_read = None
        self.on_pointer_select = None

    async def sock_sendall(self, connection, packet):
        if not self.authenticated or packet[0] != 131:
            return await super().sock_sendall(connection, packet)
        opcode, minor, words = struct.unpack_from("<BBH", packet)
        body = packet[4:]
        device = struct.unpack_from("<H", body)[0] if len(body) >= 2 else 0
        if minor != 24 and not (minor == 1 and device in {2, 4}):
            return await super().sock_sendall(connection, packet)
        assert words * 4 == len(packet)
        self.sequence += 1
        self.sent.append((opcode, minor, body))
        if minor == 1:
            assert struct.unpack("<6H", body) == (device, 2048, 0, 2048, 0, 0)
            self.pointer_selects.append(device)
            if self.on_pointer_select is not None:
                await self.on_pointer_select(device)
            return
        assert struct.unpack("<HHBBBBHH", body) == (device, 2, 1, 0, 0, 0, 0, 0)
        self.info_reads.append(device)
        result = bytearray(
            device_info(
                self.sequence,
                device,
                len(self.maps[device]),
                actions=self.pointer_actions[device],
                trim=self.info_trim,
                name=self.pointer_names[device],
                padding=self.info_padding,
            )
        )
        if self.info_fault == "unsupported":
            struct.pack_into("<H", result, 10, 29)
        elif self.info_fault == "not_present":
            result[8] = 0
        elif self.info_fault == "led_payload":
            result[14] = 1
        elif self.info_fault == "own_state":
            result[21] = 1
        elif self.info_fault == "total":
            result[20] = 4
        elif self.info_fault == "incomplete":
            result[17] -= 1
        elif self.info_fault == "wrong_device":
            result[1] = 9
        elif self.info_fault == "out_of_range":
            result[18] = 4
        elif self.info_fault == "name_size":
            struct.pack_into("<H", result, 32, 1025)
        elif self.info_fault == "trailing":
            result += b"four"
            struct.pack_into("<I", result, 4, (len(result) - 32) // 4)
        if self.on_action_read is not None:
            await self.on_action_read(device)
        if self.event is not None:
            self.incoming.extend(self.event)
            self.event = None
        self.incoming.extend(result)


async def action_context(peer=None):
    context = await button_context(peer or PointerActionPeer())
    await context[-1].prepare_button()
    return context


def action_event(device=4):
    result = bytearray(32)
    result[:2] = bytes([80, 11])
    result[8] = device
    struct.pack_into("<H", result, 10, 2)
    result[25] = 3
    struct.pack_into("<H", result, 26, 31)
    return bytes(result)
