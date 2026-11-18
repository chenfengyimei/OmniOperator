"""Independent paired-button/XKB receipts and retained-release peers; no native sockets or input."""

import asyncio
import struct

from omni_test_support.v2.linux_keystroke import raw
from omni_test_support.v2.v2_linux_motion_helpers import motion_context
from omni_test_support.v2.v2_linux_motion_packet_helpers import PacketPeer, copied, records, switched


def state(sequence, code, mapped, kind, *, passive=False):
    data = bytearray(32)
    data[:2] = bytes([80, 2])
    struct.pack_into("<HI", data, 2, sequence, 2000)
    data[8] = 3
    struct.pack_into("<HH", data, 24, 1 << (mapped + 7) if kind == 4 and mapped <= 5 else 0, 8192)
    data[28:32] = bytes([mapped if passive and kind == 4 else code, kind, 0, 0])
    return bytes(data)


def held(data, identity=4):
    result = bytearray(data[:32])
    for device, header, parts in records(data):
        if device == identity:
            buttons = bytearray(parts[0])
            buttons[8] = 2
            parts[0] = bytes(buttons)
        result += header + b"".join(parts)
    return bytes(result)


class ButtonPeer(PacketPeer):
    def __init__(self):
        super().__init__()
        self.button_requests = []
        self.xkb_fault = None
        self.passive = False
        self.physical_held = False
        self.logical_held = False
        self.released_topology = None
        self.button_sent = False
        self.block_device_query = False
        self.device_query_started, self.device_query_release = asyncio.Event(), asyncio.Event()
        self.device_query_count = 0

    def send(self, data):
        if not self.stream and len(data) >= 5 and data[4] == 6:
            return super().send(data)
        assert not self.closed
        choice = self.write_plan.pop(0) if self.write_plan else len(data)
        if isinstance(choice, BaseException):
            raise choice
        size = min(choice, len(data))
        self.stream.extend(data[:size])
        while len(self.stream) >= 36:
            request = bytes(self.stream[:36])
            del self.stream[:36]
            kind, physical = request[4:6]
            assert kind in {4, 5}
            assert request == bytes([132, 2, 9, 0, kind, physical, 0, 0]) + bytes(28)
            self.sequence += 1
            self.input_requests.append(request)
            self.button_requests.append(request)
            notification = b""
            if kind == 4 and not self.button_sent and self.switch:
                self.topology, source = copied(self.topology)
                self.feedbacks[2] = self.feedbacks[4] + self.feedbacks[2][len(self.feedbacks[4]) :]
                notification = switched(self.sequence, source)
            self.button_sent = True
            category = 15 if kind == 4 else 16
            source_raw = raw(self.sequence, category, 4, source=4, code=physical)
            master_raw = raw(self.sequence, category, 2, source=4, code=physical)
            fault = self.raw_fault
            if fault in {"source", "sequence", "code", "flags", "mask"}:
                source_raw = bytearray(source_raw)
                source_raw[{"source": 20, "sequence": 2, "code": 16, "flags": 24, "mask": 32}[fault]] ^= 1
            elif fault == "time":
                master_raw = bytearray(master_raw)
                master_raw[12] ^= 1
            elif fault == "order":
                source_raw, master_raw = master_raw, source_raw
            raw_pair = bytes(source_raw) + bytes(master_raw)
            if fault == "missing":
                raw_pair = bytes(source_raw)
            elif fault == "extra":
                raw_pair += bytes(source_raw)
            intermediate = self.maps[4][physical - 1]
            logical = self.maps[2][intermediate - 1]
            source_state = state(self.sequence, physical, intermediate, kind, passive=self.passive)
            master_state = state(self.sequence, intermediate, logical, kind, passive=self.passive)
            fault = self.xkb_fault
            if fault in {"sequence", "device", "mods", "changed", "mask", "code", "type", "request"}:
                source_state = bytearray(source_state)
                source_state[
                    {
                        "sequence": 2,
                        "device": 8,
                        "mods": 9,
                        "changed": 26,
                        "mask": 24,
                        "code": 28,
                        "type": 29,
                        "request": 30,
                    }[fault]
                ] ^= 1
                if fault == "code":
                    source_state[28] = 255
            elif fault == "order":
                source_state, master_state = master_state, source_state
            notifications = bytes(source_state) + bytes(master_state)
            if fault == "missing":
                notifications = bytes(source_state)
            elif fault == "extra":
                notifications += bytes(source_state)
            events = notifications + raw_pair if fault == "early" else raw_pair + notifications
            self.incoming.extend(notification + events)
            if kind == 5:
                self.released_topology = self.topology
                if self.physical_held:
                    self.topology = held(self.topology)
                if self.after_move is not None:
                    self.after_move()
        return size

    async def sock_sendall(self, connection, packet):
        if self.button_requests and packet[:2] == bytes([130, 48]):
            self.device_query_count += 1
            self.device_query_started.set()
            if self.block_device_query:
                await self.device_query_release.wait()
        start = len(self.incoming)
        await super().sock_sendall(connection, packet)
        if self.button_requests and packet[0] == 38 and self.logical_held:
            assert len(self.incoming) >= start + 32
            struct.pack_into("<H", self.incoming, len(self.incoming) - 8, 256)


async def button_packet_context(peer=None):
    peer = peer or ButtonPeer()
    context = await motion_context(peer)
    await context[-1].prepare_motion()
    packet = await context[-1].button_packet()
    peer.owner = context[5].connection
    packet.wire.connection = peer
    return context, packet
