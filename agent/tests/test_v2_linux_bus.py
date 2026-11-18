"""Kernel/bus transport faults through injected handles; no real Unix sockets or services."""

from __future__ import annotations

import asyncio
import io
import struct
from pathlib import PurePosixPath
from types import SimpleNamespace

import pytest

from omni.adapters.platform.desktop.linux import linux_bus as b
from omni.domain.contracts import BusinessError


@pytest.mark.parametrize(
    "address",
    [
        "tcp:host=localhost,port=1",
        "unix:path=/tmp/unowned",
        "unix:path=/run/user/1001/at-spi/bus",
        "unix:path=/run/user/1000/at-spi/../bus",
        "unix:path=/run/user/1000/at-spi/bus,noncefile=/tmp/x",
        "unix:path=/run/user/1000/at-spi/bus;unix:path=/other",
        "unix:path=/run/user/1000/at-spi/bus,path=/other",
        "unix:abstract=unowned",
        "unix:abstract=/tmp/dbus-test,path=/run/user/1000/at-spi/bus",
        "unix:path=/run/user/1000/at-spi/%00",
        "unix:path=/run/user/1000/at-spi/%ff",
        "unix:path=/run/user/1000/at-spi/bus,guid=INVALID",
    ],
)
def test_remote_ambiguous_or_unowned_bus_addresses_are_denied(address):
    with pytest.raises(BusinessError) as error:
        b.Endpoint.accessibility(address, 1000)
    assert error.value.code == "desktop_bus_address_denied"


def test_canonical_runtime_or_peer_verified_abstract_bus_with_guid():
    guid = "ab" * 16
    assert b.Endpoint.accessibility("unix:path=/run/user/1000/at-spi/bus,guid=" + guid, 1000) == b.Endpoint(
        "/run/user/1000/at-spi/bus", 1000, guid
    )
    assert b.Endpoint.accessibility("unix:abstract=%2Ftmp%2Fdbus-xyz", 1000).address == "\0/tmp/dbus-xyz"


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


@pytest.mark.parametrize(
    "changes",
    [
        {"/run/user/1000": (0o40755, 1000)},
        {"/run/user/1000/at-spi": (0o40777, 1000)},
        {"/run/user/1000/at-spi/bus": (0o140600, 1001)},
        {"/run/user/1000/at-spi/bus": (0o100600, 1000)},
        {"/run/user/1000/at-spi": (0o120777, 1000)},
        {"/run": (0o40755, 1000)},
    ],
)
def test_socket_ancestry_rejects_links_foreign_owner_and_shared_runtime(monkeypatch, changes):
    ops = path_ops(monkeypatch, changes)
    with pytest.raises(BusinessError) as error:
        ops._path(b.Endpoint("/run/user/1000/at-spi/bus", 1000))
    assert error.value.code == "desktop_bus_peer_unverified"


def test_socket_path_uses_full_uid_component_and_one_inode_snapshot(monkeypatch):
    ops = path_ops(monkeypatch)
    assert ops._path(b.Endpoint("/run/user/1000/at-spi/bus", 1000)) == (5, 10)
    ops = path_ops(monkeypatch, {"/run/user/10001": (0o40700, 1000), "/run/user/10001/bus": (0o140600, 1000)})
    with pytest.raises(BusinessError):
        ops._path(b.Endpoint("/run/user/10001/bus", 1000))


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


@pytest.mark.parametrize("fault", [None, "uid", "inode", "creation", "effective_uid"])
def test_socket_credentials_are_verified_before_auth_and_rechecked_afterwards(monkeypatch, fault):
    ops = object.__new__(b.LinuxOps)
    ops.uid, ops.pid = 1000, 50
    connection = Socket(uid=1001 if fault == "uid" else 1000)
    monkeypatch.setattr(b.socket, "socket", lambda *_args: connection)
    monkeypatch.setattr(b.socket, "SO_PEERCRED", 17, raising=False)
    monkeypatch.setattr(b.socket, "AF_UNIX", 1, raising=False)
    monkeypatch.setattr(ops, "_path", lambda endpoint: (5, 10))
    monkeypatch.setattr(ops, "process", lambda pid: b.Process(pid, connection.uid, 100.0))
    endpoint = b.Endpoint("/run/user/1000/bus", 1000)
    if fault == "uid":
        with pytest.raises(BusinessError):
            ops.connect(endpoint)
        assert connection.closed
        return
    actual, identity, inode = ops.connect(endpoint)
    assert actual is connection and identity.pid == 25 and inode == (5, 10)
    if fault == "inode":
        monkeypatch.setattr(ops, "_path", lambda endpoint: (5, 11))
    if fault in {"creation", "effective_uid"}:
        monkeypatch.setattr(
            ops,
            "process",
            lambda pid: b.Process(
                pid, 1001 if fault == "effective_uid" else 1000, 101.0 if fault == "creation" else 100.0
            ),
        )
    try:
        if fault:
            with pytest.raises(BusinessError) as error:
                ops.peer(endpoint, connection, identity, inode)
            assert error.value.code == "desktop_bus_peer_unverified"
        else:
            ops.peer(endpoint, connection, identity, inode)
    finally:
        connection.close()


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


def test_library_uses_exact_preverified_socket_pins_calls_and_closes_handles(monkeypatch):
    async def scenario():
        bus, events, messages, connection = transport(monkeypatch)
        await bus.open()
        assert events[:2] == ["kernel-peer-verified", "authentication"]
        assert len(bus.fingerprint) == 64
        assert await bus.call(":1.20", "/path", "a.b", "Read", returns="s") == ["ok"]
        assert messages[0].flags == 2
        assert await bus.close() and connection.closed and connection.stream.closed
        assert await bus.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["sender", "signature", "fd", "type", "remote"])
def test_wrong_sender_signature_fds_or_type_never_become_valid_results(monkeypatch, fault):
    async def scenario():
        bus, _, _, _ = transport(monkeypatch)
        await bus.open()

        async def respond(message):
            return SimpleNamespace(
                sender=":1.99" if fault == "sender" else message.destination,
                unix_fds=[99] if fault == "fd" else [],
                message_type=3 if fault == "remote" else 1 if fault == "type" else 2,
                signature="u" if fault == "signature" else "s",
                body=["private-body-canary"],
                error_name="private-error-canary",
            )

        bus.bus.call = respond
        try:
            with pytest.raises(b.RemoteError if fault == "remote" else BusinessError) as error:
                await bus.call(":1.20", "/path", "a.b", "Read", returns="s")
            assert "private" not in str(error.value)
        finally:
            assert await bus.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["constructor", "authentication"])
def test_partial_library_construction_and_auth_failure_close_even_the_file_wrapper(monkeypatch, fault):
    async def scenario():
        bus, _, _, connection = transport(
            monkeypatch, constructor_failure=fault == "constructor", auth_failure=fault == "authentication"
        )
        with pytest.raises(OSError):
            await bus.open()
        assert connection.closed and connection.stream.closed

    asyncio.run(scenario())


def test_binary_message_auth_line_and_total_traffic_have_independent_bounds(monkeypatch):
    async def scenario():
        bus, _, _, _ = transport(monkeypatch)
        await bus.open()
        try:
            reader = bus.bus._create_unmarshaller()
            reader.offset = 65530
            with pytest.raises(BusinessError) as error:
                reader.read(7, prefetch=True)
            assert error.value.code == "desktop_bus_message_limit"
            with pytest.raises(BusinessError):
                reader.read(-1)

            async def receive(sock, size):
                return b"x"

            bus.bus._loop = SimpleNamespace(sock_recv=receive)
            with pytest.raises(BusinessError) as error:
                await bus.bus._auth_readline()
            assert error.value.code == "desktop_bus_message_limit"

            async def eof(sock, size):
                return b""

            bus.bus._loop = SimpleNamespace(sock_recv=eof)
            with pytest.raises(BusinessError) as error:
                await bus.bus._auth_readline()
            assert error.value.code == "desktop_bus_disconnected"
            bus.bus.omni_messages = b.MAX_MESSAGES
            with pytest.raises(BusinessError):
                bus.bus._on_message(object())
            bus.bus.omni_messages, bus.bus.omni_bytes = 0, b.MAX_TOTAL
            with pytest.raises(BusinessError):
                bus.bus._on_message(object())
        finally:
            assert await bus.close()

    asyncio.run(scenario())
