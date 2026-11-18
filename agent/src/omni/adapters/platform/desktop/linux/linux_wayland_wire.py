"""Bounded read-only Wayland display/registry/output transport; no surfaces, grabs, pixel capture or input commands."""

from __future__ import annotations

import asyncio
import socket
import struct
import sys
from collections.abc import Callable

from omni.adapters.platform.desktop.linux.linux_bus import failure
from omni.adapters.platform.desktop.linux.linux_fds import ReceivedFds

ORDER = "<" if sys.byteorder == "little" else ">"
SERVER_ID = 0xFF000000
MAX_FRAME, MAX_BYTES, MAX_MESSAGES = 65532, 4 * 1024 * 1024, 2048


def word(value: int) -> bytes:
    return struct.pack(ORDER + "I", value)


def string(value: str) -> bytes:
    raw = value.encode("utf-8") + b"\0"
    return word(len(raw)) + raw + bytes(-len(raw) % 4)


def read_string(data: bytes, offset: int = 0, *, maximum: int = 512) -> tuple[str, int]:
    if offset + 4 > len(data):
        raise failure("portal_wayland_protocol_invalid")
    size = struct.unpack_from(ORDER + "I", data, offset)[0]
    end = offset + 4 + (size + 3) // 4 * 4
    if not 1 <= size <= maximum + 1 or end > len(data):
        raise failure("portal_wayland_protocol_invalid")
    raw = data[offset + 4 : offset + 4 + size]
    if raw[-1:] != b"\0" or b"\0" in raw[:-1]:
        raise failure("portal_wayland_protocol_invalid")
    try:
        value = raw[:-1].decode("utf-8")
    except UnicodeError:
        raise failure("portal_wayland_protocol_invalid") from None
    return value, end


class WaylandWire:
    def __init__(self, connection: socket.socket, check: Callable[[], None]) -> None:
        self.connection, self.check = connection, check
        self.rights = ReceivedFds()
        self.sent = self.received = self.messages = 0
        self.client_id = 1
        self.retired: set[int] = set()
        self.deleted: set[int] = set()

    def allocate(self) -> int:
        self.client_id += 1
        if self.client_id >= SERVER_ID:
            raise failure("portal_wayland_protocol_limit")
        return self.client_id

    async def wait(self, write: bool) -> None:
        loop = asyncio.get_running_loop()
        descriptor = self.connection.fileno()
        ready = loop.create_future()

        def wake() -> None:
            if not ready.done():
                ready.set_result(None)

        if write:
            loop.add_writer(descriptor, wake)
        else:
            loop.add_reader(descriptor, wake)
        try:
            await ready
        finally:
            if write:
                loop.remove_writer(descriptor)
            else:
                loop.remove_reader(descriptor)

    async def send(self, identity: int, opcode: int, payload: bytes = b"") -> None:
        packet = struct.pack(ORDER + "II", identity, (8 + len(payload)) << 16 | opcode) + payload
        self.sent += len(packet)
        if len(packet) > MAX_FRAME or len(packet) % 4 or self.sent > MAX_BYTES:
            raise failure("portal_wayland_protocol_limit")
        position = 0
        while position < len(packet):
            self.check()
            try:
                count = self.connection.send(packet[position:])
            except BlockingIOError:
                await self.wait(True)
                continue
            if not 1 <= count <= len(packet) - position:
                raise failure("portal_wayland_unavailable")
            position += count

    async def read(self, size: int) -> bytes:
        result = bytearray()
        while len(result) < size:
            self.check()
            try:
                data, ancillary, _flags, _address = self.rights.receive(self.connection, size - len(result))
            except BlockingIOError:
                await self.wait(False)
                continue
            self.received += len(data)
            if ancillary or self.rights.owned or not data or self.received > MAX_BYTES:
                self.rights.close()
                raise failure("portal_wayland_protocol_invalid")
            result.extend(data)
        return bytes(result)

    async def message(self) -> tuple[int, int, bytes]:
        self.messages += 1
        if self.messages > MAX_MESSAGES:
            raise failure("portal_wayland_protocol_limit")
        identity, header = struct.unpack(ORDER + "II", await self.read(8))
        size, opcode = header >> 16, header & 65535
        if identity == 0 or not 8 <= size <= MAX_FRAME or size % 4:
            raise failure("portal_wayland_protocol_invalid")
        return identity, opcode, await self.read(size - 8)

    def display(self, opcode: int, payload: bytes) -> None:
        if opcode == 1 and len(payload) == 4:
            identity = struct.unpack(ORDER + "I", payload)[0]
            if identity not in self.retired or identity in self.deleted:
                raise failure("portal_wayland_protocol_invalid")
            self.deleted.add(identity)
            return
        raise failure("portal_wayland_protocol_invalid")  # display error text never escapes
