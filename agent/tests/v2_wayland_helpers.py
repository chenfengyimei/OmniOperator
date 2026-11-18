"""Independent read-only Wayland binary peer and kernel identity ports on isolated pipe FDs; no compositor connection."""

import asyncio
import os
import struct
import sys

from v2_eis_helpers import injected_fds

from omni.adapters.platform.desktop.linux.linux_bus import Process
from omni.adapters.platform.desktop.linux.linux_fds import OwnedFd
from omni.adapters.platform.desktop.linux.linux_wayland_peer import WaylandPeer
from omni.adapters.platform.desktop.linux.linux_wayland_windows import WaylandWindows
from omni.adapters.platform.desktop.linux.linux_wayland_wire import WaylandWire

ORDER = "<" if sys.byteorder == "little" else ">"
BASE = 0xFF000000
MANAGER = "zwlr_foreign_toplevel_manager_v1"


def word(value):
    return struct.pack(ORDER + "I", value)


def frame(identity, opcode, payload=b""):
    return struct.pack(ORDER + "II", identity, (8 + len(payload)) << 16 | opcode) + payload


def string(value):
    raw = value.encode() + b"\0"
    return word(len(raw)) + raw + b"\xa5" * (-len(raw) % 4)


class Peer:
    def __init__(self, *, fault=None, partial=4096, windows=2, outputs=1):
        self.fd, self.writer = os.pipe()
        self.closed = False
        self.incoming, self.outgoing = bytearray(), bytearray()
        self.registry = self.manager = 0
        self.output_ids = []
        self.requests = []
        self.partial, self.fault, self.count, self.output_count = partial, fault, windows, outputs
        self.waiting, self.release = asyncio.Event(), asyncio.Event()
        self.hold = False
        self.active_waits = 0
        self.syncs = 0

    def fileno(self):
        return -1 if self.closed else self.fd

    def close(self):
        if not self.closed:
            os.close(self.fd)
            self.closed = True

    def cleanup(self):
        self.close()
        os.close(self.writer)

    def emit(self, identity, opcode, payload=b""):
        self.incoming.extend(frame(identity, opcode, payload))

    async def wait(self, write):
        assert write is False
        self.waiting.set()
        self.active_waits += 1
        try:
            await self.release.wait()
        finally:
            self.active_waits -= 1

    def recvmsg(self, length, ancillary, flags):
        if self.hold or not self.incoming:
            raise BlockingIOError
        count = min(length, self.partial, len(self.incoming))
        data = bytes(self.incoming[:count])
        del self.incoming[:count]
        return data, [], 0, None

    def send(self, data):
        count = min(len(data), self.partial)
        self.outgoing.extend(data[:count])
        while len(self.outgoing) >= 8:
            identity, header = struct.unpack_from(ORDER + "II", self.outgoing)
            size, opcode = header >> 16, header & 65535
            assert 8 <= size <= 65532 and size % 4 == 0
            if len(self.outgoing) < size:
                break
            payload = bytes(self.outgoing[8:size])
            del self.outgoing[:size]
            self.requests.append((identity, opcode, payload))
            self.request(identity, opcode, payload)
        return count

    def request(self, identity, opcode, payload):
        if identity == 1:
            assert opcode in (0, 1) and len(payload) == 4
            target = struct.unpack(ORDER + "I", payload)[0]
            if opcode == 1:
                self.registry = target
                self.emit(target, 0, word(7) + string("wl_compositor") + word(6))
                for index in range(self.output_count):
                    self.emit(
                        target, 0, word(20 + index) + string("wl_output") + word(2 if self.fault == "output_v2" else 4)
                    )
                if self.fault != "missing_manager":
                    self.emit(target, 0, word(99) + string(MANAGER) + word(2 if self.fault == "old_manager" else 3))
                if self.fault == "duplicate_global":
                    self.emit(target, 0, word(99) + string(MANAGER) + word(3))
            else:
                self.syncs += 1
                self.emit(target, 0, word(999))
                self.emit(1, 1, word(target))
        else:
            assert identity == self.registry and opcode == 0, "No window activation, close, surface or input request"
            name = struct.unpack_from(ORDER + "I", payload)[0]
            length = struct.unpack_from(ORDER + "I", payload, 4)[0]
            interface = payload[8 : 8 + length - 1].decode()
            end = 8 + (length + 3) // 4 * 4
            version, target = struct.unpack_from(ORDER + "II", payload, end)
            assert len(payload) == end + 8
            if interface == "wl_output":
                assert name == 20 + len(self.output_ids) and version in (2, 4)
                self.output_ids.append(target)
                self.emit(
                    target,
                    0,
                    struct.pack(ORDER + "iiiii", -1920, 0, 500, 300, 0)
                    + string("private-maker")
                    + string("private-model")
                    + word(0),
                )
                self.emit(target, 1, struct.pack(ORDER + "Iiii", 1, 1920, 1080, 60000))
                self.emit(target, 3, word(2))
                if version == 4:
                    self.emit(target, 4, string("DP-1"))
                    self.emit(target, 5, string("private-output"))
                self.emit(target, 2)
            else:
                assert interface == MANAGER and name == 99 and version == 3
                self.manager = target
                for index in range(self.count):
                    window = BASE + index
                    if self.fault == "client_window":
                        window = 33
                    self.emit(target, 0, word(window))
                    self.emit(window, 0, string("private-window-title-canary"))
                    self.emit(window, 1, string("org.fixture.Editor"))
                    if self.output_ids:
                        self.emit(window, 2, word(self.output_ids[0]))
                    states = (2,) if index == 0 else ()
                    if self.fault == "multi_active":
                        states = (2,)
                    if self.fault == "invalid_state":
                        states = (4,)
                    if self.fault == "duplicate_state":
                        states = (2, 2)
                    data = b"".join(word(state) for state in states)
                    self.emit(window, 4, word(len(data)) + data)
                    self.emit(window, 7, word(BASE if index and self.fault != "closed" else 0))
                    if self.fault != "missing_done":
                        self.emit(window, 5)
                    if self.fault == "closed":
                        self.emit(window, 6)


class Ops:
    uid = 1000

    def __init__(self, peer):
        self.socket = peer
        self.source = Process(900, 1000, 123.0, "/usr/bin/fixture-compositor")
        self.connected = Process(900, 1000, 123.0)
        self.queries = []
        self.paths = []
        self.fail = False

    def process(self, pid, *, executable=False):
        assert pid == self.source.pid
        return self.source if executable else Process(self.source.pid, self.source.uid, self.source.created)

    def connect(self, endpoint):
        self.paths.append(endpoint)
        return self.socket, self.connected, (1, 2)

    def peer(self, endpoint, connection, expected, inode):
        if self.fail:
            raise OSError("private-peer-canary")
        assert connection is self.socket and inode == (1, 2)

    def read_peer(self, descriptor, ops):
        os.fstat(descriptor.fileno())
        self.queries.append(descriptor.fileno())
        return self.source


def view(inspector, descriptor, peer, guard, *, name="wayland-2"):
    ops = Ops(peer)

    def peer_factory(identity, source, selected):
        return WaylandPeer(identity, source, selected, ops=ops, read_peer=ops.read_peer)

    def wire_factory(connection, check):
        wire = WaylandWire(connection, check)
        wire.wait = peer.wait
        return wire

    return WaylandWindows(inspector, descriptor, name, guard, peer_factory=peer_factory, wire_factory=wire_factory), ops


def source(monkeypatch):
    injected_fds(monkeypatch)
    read, write = os.pipe()
    os.close(write)
    return OwnedFd(read)
