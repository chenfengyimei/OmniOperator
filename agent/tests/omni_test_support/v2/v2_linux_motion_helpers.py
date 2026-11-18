"""Independent native RandR layouts and complete existing pointer proof peers; no display or input."""

import struct

from omni_test_support.v2.v2_linux_pointer_config_helpers import ConfigurationPeer, configuration_context
from omni_test_support.v2.v2_linux_pointer_helpers import ROOT
from omni_test_support.v2.v2_x11_helpers import reply


class MotionPeer(ConfigurationPeer):
    def __init__(self):
        super().__init__()
        for identity in (2, 4):
            for axis in self.axes[identity]:
                axis["low"] = (-1, 0)
        self.refresh_topology()
        self.rr_version = (1, 6)
        self.rr_event = 90
        self.rr_missing = False
        self.rr_padding = 0
        self.rr_selected = False
        self.layout_fault = None
        self.resource_times = (10, 11)
        self.mode_name = b"1080p"
        self.mode_clock = 148500000
        self.crtcs = {700: (0, 0, 1920, 1080, 900, (800,))}
        self.on_randr = None
        self.rr_reads = []

    def resources(self):
        crtcs = tuple(self.crtcs)
        outputs = tuple(output for info in self.crtcs.values() for output in info[5])
        mode = struct.pack(
            "<IHHI8HI", 900, 1920, 1080, self.mode_clock, 2008, 2052, 2200, 0, 1084, 1089, 1125, len(self.mode_name), 0
        )
        payload = struct.pack("<" + "I" * (len(crtcs) + len(outputs)), *crtcs, *outputs) + mode + self.mode_name
        payload += bytes([self.rr_padding]) * (-len(payload) % 4)
        fields = struct.pack("<II4H", *self.resource_times, len(crtcs), len(outputs), 1, len(self.mode_name))
        return bytearray(reply(self.sequence, fields=fields + bytes([self.rr_padding]) * 8, extra=payload))

    async def sock_sendall(self, connection, packet):
        if not self.authenticated or not (packet[0] == 134 or packet[0] == 98 and b"RANDR" in packet):
            return await super().sock_sendall(connection, packet)
        opcode, minor, words = struct.unpack_from("<BBH", packet)
        body = packet[4:]
        assert words * 4 == len(packet)
        self.sequence += 1
        self.sent.append((opcode, minor, body))
        identity = None
        if opcode == 98:
            assert body == b"\5\0\0\0RANDR\0\0\0"
            result = reply(self.sequence, fields=bytes([int(not self.rr_missing), 134, self.rr_event, 0]))
        elif minor == 0:
            assert body == struct.pack("<II", 1, 6)
            result = reply(self.sequence, fields=struct.pack("<II", *self.rr_version))
        elif minor == 4:
            assert struct.unpack("<IHxx", body) == (ROOT, 127 if self.rr_version[1] >= 4 else 15)
            self.rr_selected = True
            if self.on_randr is not None:
                await self.on_randr(minor, identity)
            return
        elif minor == 25:
            assert body == struct.pack("<I", ROOT)
            self.rr_reads.append((minor, None))
            result = self.resources()
            if self.layout_fault == "empty":
                result[16:24] = bytes(8)
            elif self.layout_fault == "name_count":
                result[22] += 1
            elif self.layout_fault == "duplicate":
                struct.pack_into("<I", result, 36, 700)
            elif self.layout_fault == "resource_trailing":
                result += b"four"
                struct.pack_into("<I", result, 4, (len(result) - 32) // 4)
        else:
            assert minor == 20
            identity, config = struct.unpack("<II", body)
            assert config == self.resource_times[1]
            self.rr_reads.append((minor, identity))
            x, y, width, height, mode, outputs = self.crtcs[identity]
            possible = tuple(output for item in self.crtcs.values() for output in item[5])
            payload = struct.pack("<" + "I" * (len(outputs) + len(possible)), *outputs, *possible)
            fields = struct.pack("<IhhHHI4H", 10, x, y, width, height, mode, 1, 63, len(outputs), len(possible))
            result = bytearray(reply(self.sequence, fields=fields, extra=payload))
            if self.layout_fault == "status":
                result[1] = 1
            elif self.layout_fault == "rotation":
                result[24] = 3
            elif self.layout_fault == "foreign_mode":
                struct.pack_into("<I", result, 20, 901)
        if self.on_randr is not None and opcode == 134:
            await self.on_randr(minor, identity)
        if self.event is not None:
            self.incoming.extend(self.event)
            self.event = None
        self.incoming.extend(result)


async def motion_context(peer=None):
    context = await configuration_context(peer or MotionPeer())
    await context[-1].prepare_pointer_configuration()
    return context


def randr_event(kind=0):
    event = bytearray(32)
    event[0] = 90 + kind
    struct.pack_into("<II", event, 12, ROOT, ROOT)
    return bytes(event)
