"""Bounded EI handshake/roundtrips and owned release lifetime; no business input authority or application proof."""

from __future__ import annotations

import asyncio
import re
import socket
import struct
import sys
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from omni.adapters.platform.desktop.linux.linux_bus import failure
from omni.adapters.platform.desktop.linux.linux_fds import OwnedFd, ReceivedFds

if TYPE_CHECKING:
    from omni.adapters.platform.desktop.linux.linux_eis_key_packet import EisKeyPacket

ORDER = "<" if sys.byteorder == "little" else ">"
HEADER, MAX_FRAME, MAX_TOTAL, MAX_MESSAGES = 16, 4096, 65536, 128
SERVER_ID = 0xFF00000000000000
INTERFACES = ("ei_connection", "ei_callback", "ei_pingpong")


def native_socket(descriptor: int) -> socket.socket:
    return socket.socket(fileno=descriptor)


def string(value: str) -> bytes:
    encoded = value.encode("utf-8") + b"\0"
    return struct.pack(ORDER + "I", len(encoded)) + encoded + bytes(-len(encoded) % 4)


def interface(payload: bytes) -> tuple[str, int]:
    if len(payload) < 8:
        raise failure("portal_eis_protocol_invalid")
    length = struct.unpack_from(ORDER + "I", payload)[0]
    size = (length + 3) // 4 * 4
    if not 2 <= length <= 81 or len(payload) != 4 + size + 4:
        raise failure("portal_eis_protocol_invalid")
    raw = payload[4 : 4 + length]
    try:
        name = raw[:-1].decode("ascii")
    except UnicodeError:
        raise failure("portal_eis_protocol_invalid") from None
    version = struct.unpack_from(ORDER + "I", payload, 4 + size)[0]
    if raw[-1:] != b"\0" or not re.fullmatch(r"ei_[a-z0-9_]+", name) or not version:
        raise failure("portal_eis_protocol_invalid")
    return name, version


class EisConnection:
    supported = tuple((name, 1) for name in INTERFACES)

    def __init__(
        self,
        descriptor: OwnedFd,
        guard: Callable[[], Awaitable[None]],
        *,
        socket_factory: Callable[[int], socket.socket] = native_socket,
    ) -> None:
        self.descriptor, self.guard = descriptor, guard
        self.connection: socket.socket | None = None
        self._rights = ReceivedFds()
        self._attempted = self._ready = self._closed = self._failed = self._busy = False
        self._object: int | None = None
        self._server_id = SERVER_ID - 1
        self._client_id = 0
        self._received = self._sent = self._messages = 0
        self.versions: dict[str, int] = {}
        self.serial: int | None = None
        self._input_lease: EisKeyPacket | None = None
        self._input_task: asyncio.Task[object] | None = None
        try:
            descriptor.verify_socket()
            self.connection = socket_factory(descriptor.fileno())
            self.connection.setblocking(False)
        except BaseException:
            if self.connection is not None:
                self.connection.detach()
            descriptor.close()
            raise

    @property
    def alive(self) -> bool:
        return not self._closed and not self._failed and self.descriptor.descriptor >= 0

    def _check(self) -> None:
        owned = self._input_lease is not None and asyncio.current_task() is self._input_task
        if self._input_lease is not None and not owned:
            raise failure("portal_eis_input_pending")
        if (
            self._failed
            or self._closed
            and not owned
            or self.descriptor.descriptor < 0
            or self.connection is None
            or self.connection.fileno() != self.descriptor.fileno()
        ):
            raise failure("portal_eis_unavailable")

    async def _wait(self, write: bool) -> None:
        assert self.connection is not None
        loop = asyncio.get_running_loop()
        descriptor = self.connection.fileno()
        ready: asyncio.Future[None] = loop.create_future()

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

    async def _read(self, size: int) -> bytes:
        value = bytearray()
        assert self.connection is not None
        while len(value) < size:
            self._check()
            try:
                data, ancillary, _flags, _address = self._rights.receive(self.connection, size - len(value))
            except BlockingIOError:
                await self._wait(False)
                continue
            self._received += len(data)
            if (
                (ancillary or self._rights.owned)
                and not self._accept_rights()
                or not data
                or self._received > MAX_TOTAL
            ):
                self._rights.close()
                raise failure("portal_eis_protocol_invalid")
            value.extend(data)
        return bytes(value)

    def _accept_rights(self) -> bool:
        return False

    async def _message(self) -> tuple[int, int, bytes]:
        self._messages += 1
        if self._messages > MAX_MESSAGES:
            raise failure("portal_eis_protocol_limit")
        header = await self._read(HEADER)
        identity, length, opcode = struct.unpack(ORDER + "QII", header)
        if not HEADER <= length <= MAX_FRAME or length % 4:
            raise failure("portal_eis_protocol_invalid")
        return identity, opcode, await self._read(length - HEADER)

    async def _send(self, identity: int, opcode: int, payload: bytes = b"") -> None:
        assert self.connection is not None
        packet = struct.pack(ORDER + "QII", identity, HEADER + len(payload), opcode) + payload
        self._sent += len(packet)
        if self._sent > MAX_TOTAL:
            raise failure("portal_eis_protocol_limit")
        position = 0
        while position < len(packet):
            self._check()
            try:
                count = self.connection.send(packet[position:])
            except BlockingIOError:
                await self._wait(True)
                continue
            if not 1 <= count <= len(packet) - position:
                raise failure("portal_eis_unavailable")
            position += count

    def _new_server(self, identity: int, version: int, maximum: int = 1) -> None:
        if identity <= self._server_id or identity > 0xFFFFFFFFFFFFFFFF or not 1 <= version <= maximum:
            raise failure("portal_eis_protocol_invalid")
        self._server_id = identity

    async def _event(self, identity: int, opcode: int, payload: bytes) -> None:
        if identity != self._object or opcode != 3 or len(payload) != 12:
            raise failure("portal_eis_protocol_invalid")
        ping, version = struct.unpack(ORDER + "QI", payload)
        self._new_server(ping, version)
        await self._send(ping, 0, bytes(8))

    async def _sync(self) -> None:
        assert self._object is not None
        self._client_id += 1
        if self._client_id >= SERVER_ID:
            raise failure("portal_eis_protocol_limit")
        callback = self._client_id
        await self._send(self._object, 0, struct.pack(ORDER + "QI", callback, 1))
        while True:
            identity, opcode, payload = await self._message()
            if identity == callback and opcode == 0 and payload == bytes(8):
                return
            await self._event(identity, opcode, payload)

    async def prepare(self) -> None:
        if self._attempted or self._closed or self._busy:
            raise failure("portal_state_invalid")
        self._attempted = self._busy = True
        try:
            async with asyncio.timeout(3):
                await self.guard()
                identity, opcode, payload = await self._message()
                if identity != 0 or opcode != 0 or len(payload) != 4 or not struct.unpack(ORDER + "I", payload)[0]:
                    raise failure("portal_eis_protocol_invalid")
                await self._send(0, 0, struct.pack(ORDER + "I", 1))
                await self._send(0, 2, struct.pack(ORDER + "I", 2))  # explicit sender context
                await self._send(0, 3, string("OmniOperator V2"))
                for name, version in self.supported:
                    await self._send(0, 4, string(name) + struct.pack(ORDER + "I", version))
                await self._send(0, 1)
                while True:
                    identity, opcode, payload = await self._message()
                    if identity != 0:
                        raise failure("portal_eis_protocol_invalid")
                    if opcode == 1:
                        name, version = interface(payload)
                        if name == "ei_handshake" or name in self.versions:
                            raise failure("portal_eis_protocol_invalid")
                        self.versions[name] = version
                    elif opcode == 2 and len(payload) == 16:
                        serial, connection, version = struct.unpack(ORDER + "IQI", payload)
                        self._new_server(connection, version)
                        self._object, self.serial = connection, serial
                        break
                    else:
                        raise failure("portal_eis_protocol_invalid")
                await self._sync()
                await self.guard()
                self._check()
                self._ready = True
        except BaseException:
            self._failed = True
            raise
        finally:
            self._busy = False

    async def check(self) -> None:
        if not self._ready or self._busy:
            raise failure("portal_state_invalid")
        self._busy = True
        try:
            async with asyncio.timeout(1):
                self._check()
                await self.guard()
                await self._sync()
                await self.guard()
                self._check()
        except BaseException:
            self._failed = True
            raise
        finally:
            self._busy = False

    async def close(self) -> bool:
        self._closed, self._ready = True, False
        if self._input_lease is not None:
            self._input_lease.abort = True
            if not await self._input_lease.settle():
                return False
        if self._busy:
            return False
        return self._close_handles()

    def _close_handles(self) -> bool:
        try:
            if self.connection is not None and self.connection.fileno() >= 0:
                self.connection.detach()
        except Exception:  # noqa: BLE001 -- keep original FD while the borrowing wrapper may still own its number
            return False
        clean = True
        for release in (
            self._rights.close,
            self.descriptor.close,
        ):
            try:
                release()
            except Exception:  # noqa: BLE001 -- attempt every handle release; a failed close is not a clean receipt
                clean = False
        return clean
