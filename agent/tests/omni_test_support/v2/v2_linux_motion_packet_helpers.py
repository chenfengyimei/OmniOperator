"""Independent XTEST/motion/source-copy peer over injected streams, without native input."""

import asyncio
import math
import struct

from omni_test_support.v2.v2_linux_motion_helpers import MotionPeer, motion_context
from omni_test_support.v2.v2_linux_pointer_helpers import HIT, ROOT
from omni_test_support.v2.v2_x11_helpers import reply


def records(data):
    position = 32
    for _ in range(struct.unpack_from("<H", data, 8)[0]):
        identity, _kind, _attachment, count, size = struct.unpack_from("<HHHHH", data, position)
        end = position + 12 + (size + 3) // 4 * 4
        header = data[position:end]
        position = end
        classes = []
        for _ in range(count):
            size = struct.unpack_from("<H", data, position + 2)[0] * 4
            classes.append(data[position : position + size])
            position += size
        yield identity, header, classes
    assert position == len(data)


def copied(data):
    entries = list(records(data))
    source = next(parts for identity, _header, parts in entries if identity == 4)
    result = bytearray(data[:32])
    for identity, header, parts in entries:
        if identity == 2:
            count = struct.unpack_from("<H", parts[0], 6)[0]
            size = struct.unpack_from("<H", source[0], 6)[0]
            old_start, new_start = 8 + (count + 31) // 32 * 4, 8 + (size + 31) // 32 * 4
            labels = source[0][new_start:] + parts[0][old_start + size * 4 :]
            buttons = bytearray(parts[0][:old_start] + labels)
            struct.pack_into("<H", buttons, 4, 4)
            parts = [bytes(buttons), *source[1:]]
            header = bytearray(header)
            struct.pack_into("<H", header, 6, len(parts))
        result += header + b"".join(parts)
    struct.pack_into("<I", result, 4, (len(result) - 32) // 4)
    return bytes(result), source


def switched(sequence, source):
    payload = b"".join(source)
    header = bytearray(32)
    header[:2] = bytes([35, 130])
    struct.pack_into("<HIHHIHHB", header, 2, sequence, len(payload) // 4, 1, 2, 1000, len(source), 4, 1)
    return bytes(header) + payload


def resize_buttons(peer, master, source):
    result = bytearray(peer.topology[:32])
    for identity, header, parts in records(peer.topology):
        if identity in {2, 4}:
            count = master if identity == 2 else source
            labels = tuple(range(190 if identity == 2 else 90, (190 if identity == 2 else 90) + count))
            parts[0] = struct.pack("<HHHH", 1, 2 + (count + 31) // 32 + count, identity, count)
            parts[0] += bytes((count + 31) // 32 * 4) + struct.pack("<" + "I" * count, *labels)
            peer.maps[identity] = bytes(range(1, count + 1))
        result += header + b"".join(parts)
    struct.pack_into("<I", result, 4, (len(result) - 32) // 4)
    peer.topology = bytes(result)


def raw_motion(sequence, device, raw, processed, *, words=2):
    mask = struct.pack("<I", 3) + bytes((words - 1) * 4)
    values = b"".join(struct.pack("<iI", value >> 32, value & 0xFFFFFFFF) for value in (*processed, *raw))
    header = bytearray(32)
    header[:2] = bytes([35, 130])
    struct.pack_into(
        "<HIHHIIHHI", header, 2, sequence, (len(mask) + len(values)) // 4, 17, device, 1000, 0, 4, words, 0
    )
    return bytes(header) + mask + values


class PacketPeer(MotionPeer):
    def __init__(self):
        super().__init__()
        self.input_requests, self.write_plan = [], []
        self.stream = bytearray()
        self.owner = None
        self.switch = True
        self.raw_fault = self.switch_fault = self.query_fault = None
        self.words = 2
        self.cursor = (110, 212)
        self.query_started, self.query_release = asyncio.Event(), asyncio.Event()
        self.block_query, self.query_count = False, 0
        self.after_move = None

    @property
    def closed(self):
        return self.owner is not None and self.owner.closed

    def send(self, data):
        assert not self.closed
        choice = self.write_plan.pop(0) if self.write_plan else len(data)
        if isinstance(choice, BaseException):
            raise choice
        size = min(choice, len(data))
        self.stream.extend(data[:size])
        if len(self.stream) < 36:
            return size
        request = bytes(self.stream[:36])
        del self.stream[:36]
        assert request[:8] == bytes([132, 2, 9, 0, 6, 0, 0, 0])
        assert request[8:24] == struct.pack("<IIII", 0, ROOT, 0, 0) and request[28:] == bytes(8)
        x, y = struct.unpack_from("<hh", request, 24)
        # Fixtures use native unbounded axes (-1,-1) and a pure translation;
        # these independent expressions retain fractional receipt values.
        matrix = self.matrices[4]
        assert matrix[:2] == (1.0, 0.0) and matrix[3:5] == (0.0, 1.0) and matrix[6:] == (0.0, 0.0, 1.0)
        values = (x + matrix[2], y + matrix[5])
        raw = (x << 32, y << 32)
        processed = tuple(math.floor(value * (1 << 32)) for value in values)
        self.cursor = tuple(math.trunc(value) for value in values)
        self.sequence += 1
        self.input_requests.append(request)
        notification = b""
        if self.switch:
            self.topology, source = copied(self.topology)
            self.feedbacks[2] = self.feedbacks[4] + self.feedbacks[2][len(self.feedbacks[4]) :]
            notification = bytearray(switched(self.sequence, source))
            fault = self.switch_fault
            if fault == "sequence":
                struct.pack_into("<H", notification, 2, self.sequence - 1)
            elif fault == "master":
                notification[10] = 4
            elif fault == "source":
                notification[18] = 6
            elif fault == "reason":
                notification[20] = 2
            elif fault == "classes":
                notification[16] -= 1
            elif fault == "labels":
                notification[44] ^= 1
            elif fault == "held":
                notification[40] = 1
            elif fault == "padding":
                notification[-1] = 0xA5
            elif fault == "value":
                notification[-16:-8] = struct.pack("<iI", 999, 33)
            elif fault == "missing":
                notification = b""
            elif fault == "duplicate":
                notification *= 2
        slave = raw_motion(self.sequence, 4, raw, processed, words=self.words)
        master = raw_motion(self.sequence, 2, raw, processed, words=self.words)
        fault = self.raw_fault
        if fault in {"sequence", "source", "flags", "detail", "mask", "processed", "raw", "padding"}:
            slave = bytearray(slave)
            offset = {
                "sequence": 2,
                "source": 20,
                "flags": 24,
                "detail": 16,
                "mask": 32,
                "processed": 32 + self.words * 4,
                "raw": 48 + self.words * 4,
                "padding": 36,
            }[fault]
            slave[offset] ^= 1
        elif fault == "order":
            slave, master = master, slave
        elif fault == "time":
            master = bytearray(master)
            master[12] ^= 1
        events = bytes(slave) + bytes(master)
        if fault == "missing":
            events = bytes(slave)
        elif fault == "extra":
            events += bytes(slave)
        if self.switch_fault == "late":
            self.incoming.extend(events + bytes(notification))
        else:
            self.incoming.extend(bytes(notification) + events)
        if self.after_move is not None:
            self.after_move()
        return size

    async def sock_sendall(self, connection, packet):
        if not self.input_requests or packet[0] != 38:
            return await super().sock_sendall(connection, packet)
        assert packet == bytes([38, 0, 2, 0]) + struct.pack("<I", ROOT)
        self.query_count += 1
        self.query_started.set()
        if self.block_query:
            await self.query_release.wait()
        self.sequence += 1
        self.sent.append((38, 0, packet[4:]))
        fault = self.query_fault
        root = ROOT + 1 if fault == "root" else ROOT
        x, y = self.cursor
        if fault == "position":
            x += 1
        fields = struct.pack("<IIhhhhH", root, HIT, x, y, 5, 5, 1 if fault == "mask" else 0)
        self.incoming.extend(
            reply(self.sequence, detail=2 if fault == "bool" else int(fault != "screen"), fields=fields)
        )


async def packet_context(peer=None):
    peer = peer or PacketPeer()
    context = await motion_context(peer)
    focus = context[-1]
    await focus.prepare_motion()
    packet = await focus.motion_packet()
    peer.owner = context[5].connection
    packet.wire.connection = peer
    return context, packet
