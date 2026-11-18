"""Canonical local X11 sockets and private Xauthority; verify peers before sending credentials."""

from __future__ import annotations

import asyncio
import importlib
import os
import re
import socket
import stat
import struct
from dataclasses import dataclass
from pathlib import Path

from omni.adapters.platform.desktop.linux.linux_bus import Process, RemoteError, failure
from omni.adapters.platform.desktop.linux.linux_session import LOGIN, MANAGER, SessionInspector, one


def display_number(value: object) -> tuple[int, int]:
    if not isinstance(value, str) or re.fullmatch(r":[0-9]{1,4}(?:\.[0-9]{1,2})?", value) is None:
        raise failure("desktop_x11_display_unverified")
    display, _, screen = value[1:].partition(".")
    return int(display), int(screen or "0")


def cookie_record(data: bytes, display: int, hostname: bytes) -> bytes:
    if not 1 <= len(data) <= 65536:
        raise failure("desktop_x11_auth_unavailable")
    position = 0
    matches: set[bytes] = set()
    while position < len(data):
        if position + 2 > len(data):
            raise failure("desktop_x11_auth_unavailable")
        family = int.from_bytes(data[position : position + 2], "big")
        position += 2
        fields: list[bytes] = []
        for _ in range(4):
            if position + 2 > len(data):
                raise failure("desktop_x11_auth_unavailable")
            length = int.from_bytes(data[position : position + 2], "big")
            position += 2
            if position + length > len(data):
                raise failure("desktop_x11_auth_unavailable")
            fields.append(data[position : position + length])
            position += length
        address, number, name, secret = fields
        if (
            family == 256
            and address == hostname
            and number == str(display).encode()
            and name == b"MIT-MAGIC-COOKIE-1"
            and len(secret) == 16
        ):
            matches.add(secret)
    if len(matches) != 1:
        raise failure("desktop_x11_auth_unavailable")
    return matches.pop()


def authority(uid: int, display: int) -> bytes:
    home = Path(getattr(importlib.import_module("pwd"), "getpwuid")(uid).pw_dir)
    runtime = Path(f"/run/user/{uid}")
    selected = os.environ.get("XAUTHORITY")
    candidates = [Path(selected)] if selected else [runtime / "gdm/Xauthority", home / ".Xauthority"]
    for path in candidates:
        if (
            not path.is_absolute()
            or ".." in path.parts
            or not (path == home / ".Xauthority" or path.is_relative_to(runtime))
        ):
            raise failure("desktop_x11_auth_unavailable")
        try:
            for parent in path.parents:
                info = parent.lstat()
                if not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, uid} or info.st_mode & 0o022:
                    raise failure("desktop_x11_auth_unavailable")
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError:
            continue
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != uid
                or before.st_mode & 0o077
                or before.st_nlink != 1
                or before.st_size > 65536
            ):
                raise failure("desktop_x11_auth_unavailable")
            data = stream.read(65537)
            after = os.fstat(stream.fileno())
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ):
                raise failure("desktop_x11_auth_unavailable")
        return cookie_record(data, display, socket.gethostname().encode("utf-8"))
    raise failure("desktop_x11_auth_unavailable")


@dataclass
class Connection:
    inspector: SessionInspector
    path: Path
    socket: socket.socket
    process: Process
    inode: tuple[int, int]
    screen: int
    display: int
    binary: tuple[int, int, int, int]

    @classmethod
    async def open(cls, inspector: SessionInspector) -> Connection:
        session = inspector.session
        if session is None or session.kind != "x11":
            raise failure("desktop_x11_session_unavailable")
        display, screen = display_number(session.display)
        path = Path(f"/tmp/.X11-unix/X{display}")
        inode = cls.path_identity(path, inspector.ops.uid)
        connection = socket.socket(getattr(socket, "AF_UNIX"), socket.SOCK_STREAM)
        try:
            connection.setblocking(False)
            async with asyncio.timeout(0.5):
                await asyncio.get_running_loop().sock_connect(connection, str(path))
            pid, uid, _gid = struct.unpack(
                "3i", connection.getsockopt(socket.SOL_SOCKET, getattr(socket, "SO_PEERCRED"), 12)
            )
            process = inspector.ops.process(pid, executable=True)
            if (
                uid not in {0, inspector.ops.uid}
                or process.uid != uid
                or cls.path_identity(path, inspector.ops.uid) != inode
            ):
                raise failure("desktop_x11_peer_unverified")
            await cls.server_session(inspector, process)
            binary = cls.server_binary(process)
            return cls(inspector, path, connection, process, inode, screen, display, binary)
        except BaseException:
            connection.close()
            raise

    @staticmethod
    def path_identity(path: Path, uid: int) -> tuple[int, int]:
        for parent in path.parents:
            info = parent.lstat()
            shared = str(parent) in {"/tmp", "/tmp/.X11-unix"}
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != 0
                or info.st_mode & 0o022
                and not (shared and info.st_mode & stat.S_ISVTX)
            ):
                raise failure("desktop_x11_peer_unverified")
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid not in {0, uid} or info.st_nlink != 1:
            raise failure("desktop_x11_peer_unverified")
        return info.st_dev, info.st_ino

    @staticmethod
    def server_binary(process: Process) -> tuple[int, int, int, int]:
        path = Path(process.executable)
        if not path.is_absolute() or path.name != "Xorg":
            raise failure("desktop_x11_peer_unverified")
        for parent in path.parents:
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise failure("desktop_x11_peer_unverified")
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise failure("desktop_x11_peer_unverified")
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns

    @staticmethod
    async def server_session(inspector: SessionInspector, process: Process) -> None:
        assert inspector.system is not None and inspector.login is not None and inspector.session is not None
        try:
            path = one(
                await inspector.system.call(
                    inspector.login.name,
                    MANAGER,
                    LOGIN + ".Manager",
                    "GetSessionByPID",
                    "u",
                    [process.pid],
                    returns="o",
                )
            )
        except RemoteError:
            raise failure("desktop_x11_peer_unverified") from None
        if path != inspector.session.path:
            raise failure("desktop_x11_peer_unverified")

    def check(self) -> None:
        pid, uid, _gid = struct.unpack(
            "3i", self.socket.getsockopt(socket.SOL_SOCKET, getattr(socket, "SO_PEERCRED"), 12)
        )
        actual = self.inspector.ops.process(pid, executable=True)
        if (
            (pid, uid) != (self.process.pid, self.process.uid)
            or actual != self.process
            or self.path_identity(self.path, self.inspector.ops.uid) != self.inode
            or self.server_binary(actual) != self.binary
        ):
            raise failure("desktop_x11_peer_unverified")

    def cookie(self) -> bytes:
        self.check()
        return authority(self.inspector.ops.uid, self.display)

    async def verify(self) -> None:
        self.check()
        await self.inspector.check()
        await self.server_session(self.inspector, self.process)

    async def close(self) -> bool:
        self.socket.close()
        return self.socket.fileno() == -1
