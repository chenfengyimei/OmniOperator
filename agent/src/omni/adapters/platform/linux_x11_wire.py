"""Small bounded X11 read protocol on an already verified socket; no discovery, input or reconnect."""

from __future__ import annotations

import asyncio
import socket
import struct
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .linux_bus import failure

MAX_METADATA = 65536


class XError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__("x11_request_failed")
        self.code = code


def unpack(format: str, data: bytes, offset: int = 0) -> tuple[Any, ...]:
    try:
        return struct.unpack_from("<" + format, data, offset)
    except struct.error:
        raise failure("desktop_x11_protocol_invalid") from None


def padded(data: bytes) -> bytes:
    return data + b"\0" * (-len(data) % 4)


@dataclass(frozen=True)
class Setup:
    release: int
    base: int
    mask: int
    root: int
    order: int
    formats: dict[int, tuple[int, int]]
    visuals: dict[int, tuple[int, int, int, int, int]]
    minimum_keycode: int
    maximum_keycode: int
    screens: int


def setup_reply(data: bytes, screen: int) -> Setup:
    if not 32 <= len(data) <= MAX_METADATA:
        raise failure("desktop_x11_protocol_invalid")
    release, base, mask = unpack("III", data)
    vendor, _max_request = unpack("HH", data, 16)
    screens, formats, order = unpack("BBB", data, 20)
    minimum_keycode, maximum_keycode = unpack("BB", data, 26)
    if not 1 <= screens <= 16 or screen >= screens or formats > 32 or order not in {0, 1} or not mask or base & mask:
        raise failure("desktop_x11_protocol_invalid")
    if not 8 <= minimum_keycode <= maximum_keycode <= 255:
        raise failure("desktop_x11_protocol_invalid")
    position = 32 + (vendor + 3) // 4 * 4
    pixels: dict[int, tuple[int, int]] = {}
    for _ in range(formats):
        depth, bits, pad = unpack("BBB", data, position)
        if depth in pixels:
            raise failure("desktop_x11_protocol_invalid")
        pixels[depth] = (bits, pad)
        position += 8
    selected_root = 0
    selected_visuals: dict[int, tuple[int, int, int, int, int]] = {}
    for index in range(screens):
        root = unpack("I", data, position)[0]
        depths = unpack("B", data, position + 39)[0]
        position += 40
        visuals: dict[int, tuple[int, int, int, int, int]] = {}
        for _ in range(depths):
            depth, count = unpack("BxH", data, position)
            position += 8
            if count > 4096:
                raise failure("desktop_x11_protocol_invalid")
            for _ in range(count):
                visual, category, _bits, _entries, red, green, blue = unpack("IBBHIII", data, position)
                if visual in visuals:
                    raise failure("desktop_x11_protocol_invalid")
                visuals[visual] = (depth, category, red, green, blue)
                position += 24
        if index == screen:
            selected_root, selected_visuals = root, visuals
    if position != len(data) or not selected_root:
        raise failure("desktop_x11_protocol_invalid")
    return Setup(
        release, base, mask, selected_root, order, pixels, selected_visuals, minimum_keycode, maximum_keycode, screens
    )


class Wire:
    request_limit = 1024
    message_limit = 2048

    def __init__(self, connection: socket.socket, check: Callable[[], None]) -> None:
        self.connection, self.check = connection, check
        self.loop = asyncio.get_running_loop()
        self.sequence = self.messages = self.received = 0
        self.watched: int | None = None
        self.setup: Setup | None = None

    async def read(self, length: int) -> bytes:
        if not 0 <= length <= 64_000_000 or self.received + length > 66_000_000:
            raise failure("desktop_x11_protocol_limit")
        blocks = bytearray()
        while len(blocks) < length:
            chunk = await self.loop.sock_recv(self.connection, min(length - len(blocks), 65536))
            if not chunk:
                raise failure("desktop_x11_disconnected")
            blocks.extend(chunk)
        self.received += length
        return bytes(blocks)

    async def authenticate(self, cookie: bytes, screen: int) -> Setup:
        if len(cookie) != 16:
            raise failure("desktop_x11_auth_unavailable")
        self.check()
        name = b"MIT-MAGIC-COOKIE-1"
        header = struct.pack("<BBHHHHH", ord("l"), 0, 11, 0, len(name), len(cookie), 0)
        await self.loop.sock_sendall(self.connection, header + padded(name) + padded(cookie))
        response = await self.read(8)
        status, _reason, major, minor, length = unpack("BBHHH", response)
        if status != 1 or major != 11 or minor != 0 or length * 4 > MAX_METADATA:
            raise failure("desktop_x11_auth_unavailable")
        self.setup = setup_reply(await self.read(length * 4), screen)
        self.check()
        return self.setup

    async def send(self, opcode: int, data: bytes = b"", minor: int = 0) -> None:
        self.check()
        self.sequence += 1
        if self.sequence > self.request_limit or len(data) % 4 or len(data) > 4096:
            raise failure("desktop_x11_protocol_limit")
        await self.loop.sock_sendall(self.connection, struct.pack("<BBH", opcode, minor, len(data) // 4 + 1) + data)

    async def request(self, opcode: int, data: bytes = b"", minor: int = 0, *, maximum: int = MAX_METADATA) -> bytes:
        await self.send(opcode, data, minor)
        async with asyncio.timeout(0.5):
            while True:
                header = await self.read(32)
                self.messages += 1
                if self.messages > self.message_limit:
                    raise failure("desktop_x11_protocol_limit")
                kind, detail, sequence = unpack("BBH", header)
                if kind == 0:
                    raise XError(detail)
                if kind == 1:
                    length = unpack("I", header, 4)[0] * 4
                    if sequence != self.sequence or length > maximum:
                        raise failure("desktop_x11_protocol_invalid")
                    result = header + await self.read(length)
                    self.check()
                    return result
                await self.event(header)

    async def event(self, header: bytes) -> None:
        # Capture connections only accept the selected window's structural
        # lifecycle; the dedicated input monitor has a separate strict parser.
        kind = header[0]
        if kind & 0x80 or kind not in range(16, 28):
            raise failure("desktop_x11_protocol_invalid")
        if self.watched is not None and unpack("I", header, 8)[0] == self.watched:
            raise failure("desktop_target_changed")

    async def extension(self, name: bytes) -> int:
        reply = await self.request(98, struct.pack("<Hxx", len(name)) + padded(name))
        if len(reply) != 32 or reply[8] != 1 or reply[9] < 128:
            raise failure("desktop_x11_extension_unavailable")
        return reply[9]

    async def atom(self, name: bytes) -> int:
        reply = await self.request(16, struct.pack("<Hxx", len(name)) + padded(name), 1)
        return unpack("I", reply, 8)[0]

    async def windows(self) -> tuple[list[int], bool]:
        assert self.setup is not None
        atom = await self.atom(b"_NET_CLIENT_LIST")
        if not atom:
            raise failure("desktop_x11_window_list_unavailable")
        reply = await self.request(20, struct.pack("<IIIII", self.setup.root, atom, 33, 0, 128))
        category, remaining, count = unpack("III", reply, 8)
        if reply[1] != 32 or category != 33 or count > 128 or len(reply) != 32 + count * 4:
            raise failure("desktop_x11_protocol_invalid")
        windows = list(unpack("I" * count, reply, 32))
        if len(set(windows)) != len(windows):
            raise failure("desktop_x11_protocol_invalid")
        return windows, remaining > 0

    async def client(self, extension: int, window: int) -> tuple[int, int]:
        reply = await self.request(extension, minor=1)
        count = unpack("I", reply, 8)[0]
        if count > 1024 or len(reply) != 32 + count * 8:
            raise failure("desktop_x11_protocol_invalid")
        matches: list[int] = []
        for index in range(count):
            base, mask = unpack("II", reply, 32 + index * 8)
            if base and not base & mask and window & ~mask == base:
                matches.append(base)
        if len(matches) != 1:
            raise failure("desktop_x11_target_unverified")
        base = matches[0]
        reply = await self.request(extension, struct.pack("<III", 1, base, 2), 4)
        if len(reply) != 48 or unpack("I", reply, 8)[0] != 1:
            raise failure("desktop_x11_target_unverified")
        actual, mask, length, pid = unpack("IIII", reply, 32)
        if actual != base or mask != 2 or length != 4 or not pid:
            raise failure("desktop_x11_target_unverified")
        return base, pid

    async def geometry(self, resource: int) -> tuple[int, int, int, int, int]:
        reply = await self.request(14, struct.pack("<I", resource))
        root, _x, _y, width, height, border = unpack("IhhHHH", reply, 8)
        if len(reply) != 32:
            raise failure("desktop_x11_protocol_invalid")
        return root, width, height, border, reply[1]

    async def attributes(self, window: int) -> tuple[int, int]:
        reply = await self.request(3, struct.pack("<I", window))
        visual, category = unpack("IH", reply, 8)
        if len(reply) != 44 or category != 1 or reply[26] != 2 or reply[27] != 0:
            raise failure("desktop_x11_target_unverified")
        return visual, category

    async def watch(self, window: int) -> None:
        await self.send(2, struct.pack("<III", window, 1 << 11, 1 << 17))
        self.watched = window
        await self.geometry(window)  # barrier for subscription errors

    async def image(self, composite: int, window: int, geometry: tuple[int, int, int, int, int], stride: int) -> bytes:
        assert self.setup is not None
        _root, width, height, _border, depth = geometry
        if window == self.setup.root or _root != self.setup.root or _border:
            raise failure("desktop_x11_target_unverified")
        pixmap = self.setup.base | (self.setup.mask & -self.setup.mask)
        await self.send(composite, struct.pack("<II", window, pixmap), 6)
        # Name existing redirected storage only. Never redirect or read the root,
        # an obscured window framebuffer, or a differently sized backing pixmap.
        expected = (self.setup.root, width, height, 0, depth)
        if await self.geometry(pixmap) != expected:
            raise failure("desktop_target_changed")
        try:
            reply = await self.request(
                73, struct.pack("<IhhHHI", pixmap, 0, 0, width, height, 0xFFFFFFFF), 2, maximum=stride * height
            )
            if reply[1] != depth or len(reply) != 32 + stride * height:
                raise failure("desktop_x11_protocol_invalid")
            return reply[32:]
        finally:
            await self.send(54, struct.pack("<I", pixmap))
            await self.geometry(window)  # acknowledge release; disconnect also frees this client's resources
