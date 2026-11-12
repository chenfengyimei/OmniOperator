"""Bounded smart-socket ADB client. Never starts a server, pairs, installs, roots or accepts arbitrary commands."""

from __future__ import annotations

import asyncio
import re
import struct
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import cast

from ...domain.android import device


class Adb:
    def __init__(self, port: int, serial: str) -> None:
        if type(port) is not int or not 1 <= port <= 65535 or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", serial):
            raise ValueError("Invalid ADB connection")
        self.port, self.serial = port, serial

    @staticmethod
    async def request(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, service: str) -> None:
        payload = service.encode("utf-8")
        if len(payload) > 4096:
            raise ValueError("ADB service exceeds limit")
        writer.write(f"{len(payload):04x}".encode() + payload)
        await writer.drain()
        if await reader.readexactly(4) != b"OKAY":
            # Device/server error bodies may contain private data. Never expose them.
            raise ValueError("ADB request was rejected")

    @asynccontextmanager
    async def connect(self) -> AsyncGenerator[tuple[asyncio.StreamReader, asyncio.StreamWriter], None]:
        async with asyncio.timeout(8):
            reader, writer = await asyncio.open_connection("127.0.0.1", self.port, limit=65536)
            try:
                yield reader, writer
            finally:
                writer.close()
                await writer.wait_closed()

    async def transport(self) -> int:
        async with self.connect() as (reader, writer):
            await self.request(reader, writer, "host:devices-l")
            size = int(await reader.readexactly(4), 16)
            if size > 32768:
                raise ValueError("Too many ADB devices")
            rows = (await reader.readexactly(size)).decode("utf-8").splitlines()
        matches: list[int] = []
        for line in rows:
            parts = line.split()
            if len(parts) >= 2 and parts[0] == self.serial:
                ids = [p.split(":", 1)[1] for p in parts[2:] if p.startswith("transport_id:")]
                if parts[1] != "device" or len(ids) != 1 or not ids[0].isdigit():
                    raise ValueError("ADB device is offline or unauthorized")
                matches.append(int(ids[0]))
        if len(matches) != 1 or not 1 <= matches[0] <= 2147483647:
            raise ValueError("ADB device is absent or ambiguous")
        return matches[0]

    async def shell(
        self, transport: int, command: str, *, maximum: int = 262144, before_send: Callable[[], None] | None = None
    ) -> bytes:
        # Only this module's fixed command builders call shell; no raw API/IPC argument is a command.
        async with self.connect() as (reader, writer):
            await self.request(reader, writer, "host:transport-id:" + str(transport))
            if before_send:
                before_send()
            await self.request(reader, writer, "shell,v2,raw:" + command)
            output = bytearray()
            total = 0
            for _ in range(4096):
                kind, length = struct.unpack("<BI", await reader.readexactly(5))
                total += length
                if kind not in {1, 2, 3} or length > maximum or total > maximum:
                    raise ValueError("ADB shell response exceeds limits")
                data = await reader.readexactly(length)
                if kind == 1:
                    output.extend(data)
                elif kind == 3:
                    if data != b"\x00":
                        raise ValueError("ADB shell failed")
                    return bytes(output)
            raise ValueError("Too many shell frames")

    async def identify(self) -> dict[str, object]:
        transport = await self.transport()
        boot = (await self.shell(transport, "cat /proc/sys/kernel/random/boot_id", maximum=1024)).decode().strip()
        user = (await self.shell(transport, "am get-current-user", maximum=1024)).decode().strip()
        fingerprint = (await self.shell(transport, "getprop ro.build.fingerprint", maximum=2048)).decode().strip()
        final_boot = (await self.shell(transport, "cat /proc/sys/kernel/random/boot_id", maximum=1024)).decode().strip()
        final_user = (await self.shell(transport, "am get-current-user", maximum=1024)).decode().strip()
        if not user.isdigit() or boot != final_boot or user != final_user or await self.transport() != transport:
            raise ValueError("Android identity changed")
        return device(
            {
                "serial": self.serial,
                "transport_id": transport,
                "boot_id": boot,
                "user_id": int(user),
                "fingerprint": fingerprint,
            }
        )

    async def require(self, expected: dict[str, object]) -> None:
        if await self.identify() != device(expected):
            raise ValueError("Android boot, user or transport changed")

    async def window(self, expected: dict[str, object]) -> dict[str, object]:
        transport = cast(int, expected["transport_id"])
        raw = (await self.shell(transport, "dumpsys window windows")).decode("utf-8")
        policy = (await self.shell(transport, "dumpsys window policy")).decode("utf-8")
        matches = re.findall(r"mCurrentFocus=Window\{[^}\r\n]*\bu(\d+) ([A-Za-z0-9_.]+)/(\S+)\}", raw)
        if len(matches) != 1 or int(matches[0][0]) != expected["user_id"]:
            raise ValueError("Foreground Android user/application is unverified")
        # Missing vendor-specific fields explicitly disable input rather than assuming unlocked.
        showing = re.findall(r"(?m)^\s*showing=(true|false)\s*$", policy)
        restricted = re.findall(r"(?m)^\s*inputRestricted=(true|false)\s*$", policy)
        awake = re.findall(r"\bm(?:Awake|Interactive)=(true|false)\b", policy)
        return {
            "package": matches[0][1],
            "activity": matches[0][2],
            "unlocked": showing == ["false"]
            and restricted == ["false"]
            and bool(awake)
            and all(v == "true" for v in awake),
        }
