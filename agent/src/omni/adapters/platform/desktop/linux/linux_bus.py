"""Verified local D-Bus transport for desktop helpers; no environment-selected or network buses."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import importlib.metadata
import json
import os
import re
import socket
import stat
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote_to_bytes

import psutil

from omni.domain.contracts import BusinessError
from omni.adapters.platform.desktop.linux.linux_fds import OwnedFd, ReceivedFds

MAX_MESSAGE = 65536
MAX_MESSAGES = 2048
MAX_TOTAL = 4 * 1024 * 1024
DBUS = "org.freedesktop.DBus"
DBUS_PATH = "/org/freedesktop/DBus"


def failure(code: str) -> BusinessError:
    return BusinessError(code, "Linux desktop identity or local bus could not be verified", status=503)


class RemoteError(Exception):
    def __init__(self, name: str) -> None:
        super().__init__("linux_bus_remote_error")
        self.name = name


@dataclass(frozen=True)
class Process:
    pid: int
    uid: int
    created: float
    executable: str = ""


@dataclass(frozen=True)
class Endpoint:
    address: str
    uid: int | None
    guid: str | None = None

    @classmethod
    def accessibility(cls, value: object, uid: int) -> Endpoint:
        if not isinstance(value, str) or len(value) > 1024 or not value.startswith("unix:") or ";" in value:
            raise failure("desktop_bus_address_denied")
        fields: dict[str, str] = {}
        for part in value[5:].split(","):
            key, separator, raw = part.partition("=")
            if not separator or key in fields or key not in {"path", "abstract", "guid"}:
                raise failure("desktop_bus_address_denied")
            try:
                fields[key] = unquote_to_bytes(raw).decode("utf-8", errors="strict")
            except UnicodeError:
                raise failure("desktop_bus_address_denied") from None
        if ("path" in fields) == ("abstract" in fields):
            raise failure("desktop_bus_address_denied")
        guid = fields.get("guid")
        if guid is not None and re.fullmatch(r"[0-9a-f]{32}", guid) is None:
            raise failure("desktop_bus_address_denied")
        if "abstract" in fields:
            name = fields["abstract"]
            if re.fullmatch(r"/tmp/dbus-[A-Za-z0-9_-]{1,80}", name) is None:
                raise failure("desktop_bus_address_denied")
            return cls("\0" + name, uid, guid)
        path = fields["path"]
        prefix = f"/run/user/{uid}/at-spi/"
        if (
            not path.startswith(prefix)
            or re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", path[len(prefix) :]) is None
            or path[len(prefix) :] in {".", ".."}
        ):
            raise failure("desktop_bus_address_denied")
        return cls(path, uid, guid)


class LinuxOps:
    """Kernel credentials and protected canonical paths; no UI API or bus activation."""

    uid: int
    pid: int

    def __init__(self) -> None:
        import sys

        if sys.platform != "linux":
            raise failure("desktop_native_unavailable")
        self.uid, self.pid = os.geteuid(), os.getpid()
        if self.uid <= 0:
            raise failure("desktop_session_unverified")

    def process(self, pid: int, *, executable: bool = False) -> Process:
        try:
            process = psutil.Process(pid)
            uids = getattr(process, "uids")  # platform-only psutil API
            uid, created = uids().effective, process.create_time()
            path = process.exe() if executable else ""
            if process.create_time() != created or uids().effective != uid or not process.is_running():
                raise failure("desktop_target_changed")
            return Process(pid, uid, created, path)
        except (psutil.Error, OSError):
            raise failure("desktop_target_changed") from None

    def _path(self, endpoint: Endpoint) -> tuple[int, int] | None:
        if endpoint.address.startswith("\0"):
            return None
        path = Path(endpoint.address)
        current = path
        while True:
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise failure("desktop_bus_peer_unverified")
            if current == path:
                if not stat.S_ISSOCK(info.st_mode) or info.st_nlink != 1:
                    raise failure("desktop_bus_peer_unverified")
                if endpoint.uid is not None and info.st_uid != endpoint.uid:
                    raise failure("desktop_bus_peer_unverified")
                if endpoint.uid is None and info.st_uid not in self.system_uids():
                    raise failure("desktop_bus_peer_unverified")
            else:
                if not stat.S_ISDIR(info.st_mode):
                    raise failure("desktop_bus_peer_unverified")
                # Canonical per-user runtime directories are private; system parents are root-owned.
                expected = self.uid if current.is_relative_to(Path(f"/run/user/{self.uid}")) else 0
                if info.st_uid != expected or info.st_mode & 0o022:
                    raise failure("desktop_bus_peer_unverified")
                if str(current) == f"/run/user/{self.uid}" and info.st_mode & 0o077:
                    raise failure("desktop_bus_peer_unverified")
            if current == current.parent:
                break
            current = current.parent
        final = path.stat(follow_symlinks=False)
        return final.st_dev, final.st_ino

    def system_uids(self) -> set[int]:
        account_by_name = getattr(importlib.import_module("pwd"), "getpwnam")

        allowed = {0}
        for name in ("messagebus", "dbus"):
            try:
                account = account_by_name(name)
                if account.pw_uid != self.uid:
                    allowed.add(account.pw_uid)
            except KeyError:
                pass
        return allowed

    def connect(self, endpoint: Endpoint) -> tuple[socket.socket, Process, tuple[int, int] | None]:
        before = self._path(endpoint)
        connection = socket.socket(getattr(socket, "AF_UNIX"), socket.SOCK_STREAM)
        try:
            connection.settimeout(0.5)
            connection.connect(endpoint.address)
            pid, uid, _gid = struct.unpack(
                "3i", connection.getsockopt(socket.SOL_SOCKET, getattr(socket, "SO_PEERCRED"), 12)
            )
            allowed = self.system_uids() if endpoint.uid is None else {endpoint.uid}
            process = self.process(pid)
            if uid not in allowed or process.uid != uid or self._path(endpoint) != before:
                raise failure("desktop_bus_peer_unverified")
            connection.setblocking(False)
            return connection, process, before
        except BaseException:
            connection.close()
            raise

    def peer(
        self, endpoint: Endpoint, connection: socket.socket, expected: Process, inode: tuple[int, int] | None
    ) -> None:
        try:
            pid, uid, _gid = struct.unpack(
                "3i", connection.getsockopt(socket.SOL_SOCKET, getattr(socket, "SO_PEERCRED"), 12)
            )
            actual = self.process(pid)
            if (
                actual.uid != uid
                or (pid, uid, actual.created) != (expected.pid, expected.uid, expected.created)
                or self._path(endpoint) != inode
            ):
                raise failure("desktop_bus_peer_unverified")
        except (OSError, psutil.Error):
            raise failure("desktop_bus_peer_unverified") from None


class BusPort(Protocol):
    @property
    def fingerprint(self) -> str: ...
    async def call(
        self,
        destination: str,
        path: str,
        interface: str,
        member: str,
        signature: str = "",
        body: list[Any] | None = None,
        *,
        returns: str,
    ) -> list[Any]: ...
    def check(self) -> None: ...
    async def close(self) -> bool: ...


def load_types() -> tuple[Any, Any, Any]:
    # These protected hooks are deliberately version-fenced. Unknown implementations
    # must be reviewed rather than inheriting arbitrary socket/address behaviour.
    try:
        if importlib.metadata.version("dbus-next") != "0.2.3":
            raise failure("desktop_dependency_incompatible")
        package = importlib.import_module("dbus_next")
        aio = importlib.import_module("dbus_next.aio")
        wire = importlib.import_module("dbus_next._private.unmarshaller")
    except (ImportError, importlib.metadata.PackageNotFoundError):
        raise failure("desktop_dependency_missing") from None
    return package, aio.MessageBus, wire.Unmarshaller


class LocalBus:
    def __init__(self, endpoint: Endpoint, ops: LinuxOps, *, unix_fds: bool = False) -> None:
        self.endpoint, self.ops = endpoint, ops
        self.bus: Any = None
        self.connection: socket.socket | None = None
        self.identity: Process | None = None
        self.inode: tuple[int, int] | None = None
        self.package: Any = None
        self.stream: Any = None
        self.calls = 0
        self.unix_fds = unix_fds
        self._received = ReceivedFds()
        self._fd_expected: tuple[int, str] | None = None

    @property
    def fingerprint(self) -> str:
        if self.identity is None:
            raise failure("desktop_bus_peer_unverified")
        data = {"endpoint": self.endpoint.address, "peer": vars(self.identity), "inode": self.inode}
        return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    async def open(self) -> LocalBus:
        package, base, unmarshaller = load_types()
        self.package = package
        self.connection, self.identity, self.inode = self.ops.connect(self.endpoint)
        connected = self.connection
        transport = self

        class ReceivingSocket:
            def recvmsg(
                self, length: int, _ancillary_size: int
            ) -> tuple[bytes, list[tuple[int, int, bytes]], int, object]:
                return transport._received.receive(connected, length)

        class LimitedUnmarshaller(unmarshaller):
            def read(self, count: int, prefetch: bool = False) -> Any:
                if count < 0 or self.offset + count > MAX_MESSAGE:
                    raise failure("desktop_bus_message_limit")
                parent: Any = super()
                return parent.read(count, prefetch)

        class VerifiedBus(base):
            omni_messages: int
            omni_bytes: int

            def _setup_socket(self) -> None:
                self._sock = connected
                self._stream = connected.makefile("rwb", buffering=0)
                transport.stream = self._stream
                self._fd = connected.fileno()

            def _create_unmarshaller(self):
                if transport.unix_fds:
                    return LimitedUnmarshaller(self._stream, ReceivingSocket())
                return LimitedUnmarshaller(self._stream)

            async def _auth_readline(self):
                line = bytearray()
                while not line.endswith(b"\r\n"):
                    if len(line) >= 4096:
                        raise failure("desktop_bus_message_limit")
                    block = await self._loop.sock_recv(self._sock, 1)
                    if not block:
                        raise failure("desktop_bus_disconnected")
                    line.extend(block)
                return bytes(line[:-2]).decode("ascii")

            def _on_message(self, message: Any) -> None:
                self.omni_messages += 1
                self.omni_bytes += len(self._unmarshaller.buf)
                if self.omni_messages > MAX_MESSAGES or self.omni_bytes > MAX_TOTAL:
                    raise failure("desktop_bus_message_limit")
                if getattr(message, "unix_fds", []):
                    descriptors = message.unix_fds
                    if (
                        not transport.unix_fds
                        or transport._fd_expected != (message.reply_serial, message.sender)
                        or message.message_type != package.MessageType.METHOD_RETURN
                        or message.signature != "h"
                        or message.body != [0]
                        or len(descriptors) != 1
                        or set(transport._received.owned) != set(descriptors)
                    ):
                        transport._received.close()
                        raise failure("portal_fd_invalid")
                parent: Any = super()
                parent._on_message(message)

        try:
            self.bus = VerifiedBus(bus_address="unix:path=/omni-preconnected", negotiate_unix_fd=self.unix_fds)
            self.bus.omni_messages = self.bus.omni_bytes = 0
            async with asyncio.timeout(0.8):
                await self.bus.connect()
            self.check()
            return self
        except BaseException:
            await self.close()
            raise

    def check(self) -> None:
        if self.connection is None or self.identity is None or self.bus is None or not self.bus.connected:
            raise failure("desktop_bus_disconnected")
        self.ops.peer(self.endpoint, self.connection, self.identity, self.inode)

    async def call(
        self,
        destination: str,
        path: str,
        interface: str,
        member: str,
        signature: str = "",
        body: list[Any] | None = None,
        *,
        returns: str,
    ) -> list[Any]:
        self.check()
        self.calls += 1
        if self.calls > MAX_MESSAGES:
            raise failure("desktop_bus_message_limit")
        message = self.package.Message(
            destination=destination,
            path=path,
            interface=interface,
            member=member,
            signature=signature,
            body=body or [],
            flags=self.package.MessageFlag.NO_AUTO_START,
        )
        async with asyncio.timeout(0.3):
            reply = await self.bus.call(message)
        self.check()
        if reply is None or reply.sender != destination or reply.unix_fds:
            raise failure("desktop_bus_response_invalid")
        if reply.message_type == self.package.MessageType.ERROR:
            raise RemoteError(reply.error_name or "")
        if reply.message_type != self.package.MessageType.METHOD_RETURN or reply.signature != returns:
            raise failure("desktop_bus_response_invalid")
        return reply.body

    async def open_pipewire(self, destination: str, session: str) -> OwnedFd:
        return await self._portal_fd(destination, session, "org.freedesktop.portal.ScreenCast", "OpenPipeWireRemote")

    async def connect_eis(self, destination: str, session: str) -> OwnedFd:
        return await self._portal_fd(destination, session, "org.freedesktop.portal.RemoteDesktop", "ConnectToEIS")

    async def _portal_fd(self, destination: str, session: str, interface: str, member: str) -> OwnedFd:
        """Claim one correlated descriptor for a fixed portal operation; never retries or dispatches input."""
        self.check()
        if (interface, member) not in {
            ("org.freedesktop.portal.ScreenCast", "OpenPipeWireRemote"),
            ("org.freedesktop.portal.RemoteDesktop", "ConnectToEIS"),
        }:
            raise failure("portal_fd_unavailable")
        if not self.unix_fds or self._fd_expected is not None:
            raise failure("portal_fd_unavailable")
        self.calls += 1
        if self.calls > MAX_MESSAGES:
            raise failure("desktop_bus_message_limit")
        message = self.package.Message(
            destination=destination,
            path="/org/freedesktop/portal/desktop",
            interface=interface,
            member=member,
            signature="oa{sv}",
            body=[session, {}],
            flags=self.package.MessageFlag.NO_AUTO_START,
            serial=self.bus.next_serial(),
        )
        self._fd_expected = (message.serial, destination)
        try:
            async with asyncio.timeout(0.8):
                reply = await self.bus.call(message)
            self.check()
            if (
                reply is None
                or reply.sender != destination
                or reply.reply_serial != message.serial
                or reply.message_type != self.package.MessageType.METHOD_RETURN
                or reply.signature != "h"
                or reply.body != [0]
                or len(reply.unix_fds) != 1
            ):
                raise failure("portal_fd_invalid")
            result = self._received.claim(reply.unix_fds[0])
            try:
                result.verify_socket()
            except BaseException:
                result.close()
                raise
            return result
        finally:
            self._fd_expected = None
            self._received.close()

    async def close(self) -> bool:
        clean = True
        try:
            if self.bus is not None:
                self.bus.disconnect()
                try:
                    async with asyncio.timeout(0.25):
                        await self.bus.wait_for_disconnect()
                except Exception:  # noqa: BLE001 -- protocol faults still require explicit fd cleanup
                    clean = False
        finally:
            self._received.close()
            # Construction/authentication may fail before self.bus is assigned.
            # Close every locally owned handle even when library finalization fails.
            for release in (
                lambda: self.bus._finalize(None) if self.bus is not None and not self.bus._disconnected else None,
                lambda: self.stream.close() if self.stream is not None else None,
                lambda: self.connection.close() if self.connection is not None else None,
            ):
                try:
                    release()
                except Exception:  # noqa: BLE001 -- never leak another handle because the previous release failed
                    clean = False
        return clean and (self.connection is None or self.connection.fileno() == -1)
