"""Reusable linux_bus fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import io
import struct
from pathlib import PurePosixPath
from types import SimpleNamespace

import pytest

from omni.adapters.platform.desktop.linux import linux_bus as b
from omni.domain.contracts import BusinessError


def path_ops(monkeypatch, changes=None):
    values = {
        "/": (0o40755, 0),
        "/run": (0o40755, 0),
        "/run/user": (0o40755, 0),
        "/run/user/1000": (0o40700, 1000),
        "/run/user/1000/at-spi": (0o40700, 1000),
        "/run/user/1000/at-spi/bus": (0o140600, 1000),
    }
    values.update(changes or {})

    class FakePath(PurePosixPath):
        def lstat(self):
            mode, uid = values[str(self)]
            return SimpleNamespace(st_mode=mode, st_uid=uid, st_nlink=1, st_dev=5, st_ino=10)

        def stat(self, *, follow_symlinks):
            assert follow_symlinks is False
            return self.lstat()

    monkeypatch.setattr(b, "Path", FakePath)
    ops = object.__new__(b.LinuxOps)
    ops.uid, ops.pid = 1000, 50
    return ops


class Socket:
    def __init__(self, *, uid=1000):
        self.uid, self.closed = uid, False
        self.stream = io.BytesIO()
        self.address = None

    def makefile(self, mode, *, buffering):
        assert mode == "rwb" and buffering == 0
        return self.stream

    def fileno(self):
        return -1 if self.closed else 10

    def close(self):
        self.closed = True

    def getsockopt(self, level, option, size):
        return struct.pack("3i", 25, self.uid, self.uid)

    def connect(self, address):
        self.address = address

    def settimeout(self, timeout):
        assert timeout == 0.5

    def setblocking(self, blocking):
        assert blocking is False


def transport(monkeypatch, *, constructor_failure=False, auth_failure=False, unix_fds=False):
    events, messages = [], []
    connection = Socket()

    class Ops:
        def connect(self, endpoint):
            events.append("kernel-peer-verified")
            return connection, b.Process(25, 1000, 100.0), (5, 10)

        def peer(self, endpoint, sock, identity, inode):
            assert sock is connection and not sock.closed
            events.append("peer-rechecked")

    class Unmarshaller:
        def __init__(self, stream, sock=None):
            self.stream, self.offset, self.buf = stream, 0, bytearray(b"x")
            self.sock = sock

        def read(self, count, prefetch=False):
            previous = self.offset
            if not prefetch:
                self.offset += count
            return previous

    class Base:
        def __init__(self, **kwargs):
            assert kwargs == {"bus_address": "unix:path=/omni-preconnected", "negotiate_unix_fd": unix_fds}
            self._setup_socket()
            if constructor_failure:
                raise OSError("private-constructor-body")
            self.connected, self._disconnected = False, False
            self._loop = asyncio.get_running_loop()
            self._unmarshaller = self._create_unmarshaller()

        async def connect(self):
            assert events[0] == "kernel-peer-verified" and self._sock is connection
            events.append("authentication")
            if auth_failure:
                raise OSError("private-auth-body")
            self.connected = True

        async def call(self, message):
            messages.append(message)
            return SimpleNamespace(sender=message.destination, unix_fds=[], message_type=2, signature="s", body=["ok"])

        def next_serial(self):
            return 17

        def _on_message(self, message):
            return None

        def disconnect(self):
            self.connected, self._disconnected = False, True

        async def wait_for_disconnect(self):
            return None

        def _finalize(self, error):
            self.disconnect()

    package = SimpleNamespace(
        Message=lambda **kwargs: SimpleNamespace(**kwargs),
        MessageFlag=SimpleNamespace(NO_AUTO_START=2),
        MessageType=SimpleNamespace(ERROR=3, METHOD_RETURN=2),
    )
    monkeypatch.setattr(b, "load_types", lambda: (package, Base, Unmarshaller))
    return b.LocalBus(b.Endpoint("/run/user/1000/bus", 1000), Ops(), unix_fds=unix_fds), events, messages, connection


__all__ = [
    "BusinessError",
    "PurePosixPath",
    "SimpleNamespace",
    "Socket",
    "asyncio",
    "b",
    "io",
    "path_ops",
    "pytest",
    "struct",
    "transport",
]
