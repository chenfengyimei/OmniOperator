"""Independent EI core binary peer on injected descriptors; no compositor, devices or native input."""

import asyncio
import os
import struct
import sys

from omni.adapters.platform.linux_eis import EisConnection
from omni.adapters.platform.linux_fds import OwnedFd

ORDER = "<" if sys.byteorder == "little" else ">"
CONNECTION = 0xFF00000000000001


def frame(identity, opcode, payload=b""):
    return struct.pack(ORDER + "QII", identity, 16 + len(payload), opcode) + payload


def word(value):
    return struct.pack(ORDER + "I", value)


def string(value, *, padding=b"\xa5"):
    raw = value.encode("utf-8") + b"\0"
    return word(len(raw)) + raw + padding * (-len(raw) % 4)


class Peer:
    def __init__(self, *, fault=None, partial=4096, ping=False):
        self.fd, write = os.pipe()
        os.close(write)
        self.owned = OwnedFd(self.fd)
        self.fault, self.partial, self.ping = fault, partial, ping
        self.incoming = bytearray(frame(0, 0, word(3)))
        self.outgoing = bytearray()
        self.requests, self.closed, self.blocked = [], False, False
        self.release, self.waiting = asyncio.Event(), asyncio.Event()
        self.next_ping = CONNECTION
        self.pending_callback = None
        if fault == "initial_object":
            self.incoming[:] = frame(1, 0, word(1))
        elif fault == "initial_opcode":
            self.incoming[:] = frame(0, 1, word(1))
        elif fault == "initial_zero":
            self.incoming[:] = frame(0, 0, word(0))
        elif fault in {"short", "large", "unaligned"}:
            length = {"short": 12, "large": 4100, "unaligned": 19}[fault]
            self.incoming[:] = struct.pack(ORDER + "QII", 0, length, 0)

    def setblocking(self, value):
        assert value is False

    def fileno(self):
        return -1 if self.closed else self.fd

    def detach(self):
        self.closed = True
        return self.fd

    def recvmsg(self, length, ancillary_size, flags):
        assert length <= 4096 and ancillary_size >= 8
        if self.blocked or not self.incoming:
            raise BlockingIOError
        count = min(length, self.partial, len(self.incoming))
        data = bytes(self.incoming[:count])
        del self.incoming[:count]
        return data, [], 0, None

    async def wait(self, write):
        assert write is False
        self.waiting.set()
        await self.release.wait()
        self.blocked = False

    def send(self, data):
        count = min(self.partial, len(data))
        self.outgoing.extend(data[:count])
        while len(self.outgoing) >= 16:
            identity, size, opcode = struct.unpack_from(ORDER + "QII", self.outgoing)
            assert 16 <= size <= 4096 and size % 4 == 0
            if len(self.outgoing) < size:
                break
            payload = bytes(self.outgoing[16:size])
            del self.outgoing[:size]
            self.requests.append((identity, opcode, payload))
            if identity == 0:
                assert opcode in {0, 1, 2, 3, 4}, "No input or device operation belongs to the handshake"
                if opcode == 1:
                    self.finish()
            elif identity == CONNECTION:
                assert opcode == 0 and len(payload) == 12, "Only core sync, never seat/device/input requests"
                callback, version = struct.unpack(ORDER + "QI", payload)
                assert version == 1
                if self.ping:
                    self.next_ping += 1
                    identity = CONNECTION if self.fault == "ping_reuse" else self.next_ping
                    version = 2 if self.fault == "ping_version" else 1
                    self.pending_callback = callback
                    self.incoming.extend(frame(CONNECTION, 3, struct.pack(ORDER + "QI", identity, version)))
                else:
                    self.callback(callback)
            else:
                assert identity == self.next_ping and opcode == 0 and payload == bytes(8)
                self.callback(self.pending_callback)
        return count

    def finish(self):
        announcement = string("ei_connection") + word(3)
        if self.fault == "interface_null":
            announcement = bytes(4) + word(1)
        elif self.fault == "interface_zero":
            announcement = string("ei_connection") + word(0)
        elif self.fault == "interface_handshake":
            announcement = string("ei_handshake") + word(1)
        elif self.fault == "interface_tail":
            announcement += word(1)
        elif self.fault == "interface_nonascii":
            announcement = word(4) + b"ei\xff\0" + word(1)
        elif self.fault == "interface_embedded_null":
            announcement = string("ei_\0connection") + word(1)
        self.incoming.extend(frame(0, 1, announcement))
        if self.fault == "duplicate_interface":
            self.incoming.extend(frame(0, 1, announcement))
        elif self.fault == "repeat_handshake":
            self.incoming.extend(frame(0, 0, word(1)))
        identity = 10 if self.fault == "client_connection_id" else CONNECTION
        version = 2 if self.fault == "connection_version" else 1
        body = word(0xFFFFFFFF) + struct.pack(ORDER + "Q", identity) + word(version)
        self.incoming.extend(frame(0, 2, body[:-4] if self.fault == "short_connection" else body))

    def callback(self, identity):
        if self.fault == "callback_id":
            identity += 1
        if self.fault == "unexpected_seat":
            self.incoming.extend(frame(CONNECTION, 1, struct.pack(ORDER + "QI", self.next_ping + 1, 1)))
        elif self.fault == "disconnected":
            self.incoming.extend(frame(CONNECTION, 0, word(5) + word(1) + string("private-provider-canary")))
        elif self.fault == "invalid_object":
            self.incoming.extend(frame(CONNECTION, 2, word(5) + struct.pack(ORDER + "Q", 1)))
        else:
            self.incoming.extend(
                frame(identity, 0, struct.pack(ORDER + "Q", 1 if self.fault == "callback_value" else 0))
            )

    def connection(self, guard):
        value = EisConnection(self.owned, guard, socket_factory=lambda descriptor: self)
        value._wait = self.wait
        return value


def injected_fds(monkeypatch):
    import socket

    monkeypatch.setattr(socket, "CMSG_SPACE", lambda length: length + 16, raising=False)
    monkeypatch.setattr(OwnedFd, "verify_socket", lambda descriptor: os.fstat(descriptor.fileno()))
