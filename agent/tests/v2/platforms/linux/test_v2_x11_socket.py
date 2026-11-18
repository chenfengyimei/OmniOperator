"""Verify X11 kernel peer before cookie access using injected POSIX metadata and socket operations."""

import asyncio
import struct
from pathlib import PurePosixPath
from types import SimpleNamespace

import pytest
from omni_test_support.v2.v2_linux_desktop_helpers import SESSION
from omni_test_support.v2.v2_x11_helpers import linux_factory

from omni.adapters.platform.desktop.linux import linux_x11_socket as native
from omni.adapters.platform.desktop.linux.linux_bus import Process
from omni.adapters.platform.desktop.linux.linux_session import SessionInspector
from omni.domain.contracts import BusinessError


def native_paths(monkeypatch, *, fault=None):
    class Path(PurePosixPath):
        def lstat(self):
            is_socket = str(self).endswith("/X0")
            is_binary = self.name == "Xorg"
            mode = (
                0o140777
                if is_socket
                else 0o100755
                if is_binary
                else 0o41777
                if str(self) in {"/tmp", "/tmp/.X11-unix"}
                else 0o40755
            )
            if fault == "link" and str(self) == "/tmp/.X11-unix":
                mode = 0o120777
            if fault == "shared" and str(self) == "/tmp/.X11-unix":
                mode = 0o40777
            if fault == "binary_writable" and is_binary:
                mode = 0o100777
            return SimpleNamespace(
                st_mode=mode,
                st_uid=1001 if fault == "file_uid" and is_socket else 0,
                st_nlink=1,
                st_dev=5,
                st_ino=10,
                st_size=100,
                st_mtime_ns=50,
            )

    monkeypatch.setattr(native, "Path", Path)


@pytest.mark.parametrize(
    "fault", [None, "socket_uid", "file_uid", "session", "pid_reuse", "link", "shared", "binary_writable"]
)
def test_pre_auth_peer_server_executable_and_session_proofs_are_required(monkeypatch, fault):
    async def scenario():
        native_paths(monkeypatch, fault=fault)
        f = linux_factory()
        if fault == "session":
            f.sessions[30] = "/other"
        inspector = SessionInspector(f.ops, f)
        await inspector.open(accessibility=False)

        class Socket:
            closed = False
            calls = 0

            def setblocking(self, value):
                assert value is False

            def getsockopt(self, *args):
                self.calls += 1
                return struct.pack("3i", 30, 1001 if fault == "socket_uid" else 0, 0)

            def close(self):
                self.closed = True

            def fileno(self):
                return -1 if self.closed else 10

        sock = Socket()
        monkeypatch.setattr(
            native,
            "socket",
            SimpleNamespace(socket=lambda *_: sock, AF_UNIX=1, SOCK_STREAM=1, SOL_SOCKET=1, SO_PEERCRED=17),
        )
        loop = asyncio.get_running_loop()

        async def connect(connection, path):
            assert connection is sock and path == "/tmp/.X11-unix/X0"

        monkeypatch.setattr(loop, "sock_connect", connect)
        reads = []

        def authority(uid, display):
            assert sock.calls >= 2 and uid == 1000 and display == 0 and f.sessions[30] == SESSION
            reads.append(True)
            return b"owned-cookie-123"

        monkeypatch.setattr(native, "authority", authority)
        connection = None
        try:
            if fault not in {None, "pid_reuse"}:
                with pytest.raises(BusinessError):
                    await native.Connection.open(inspector)
                assert not reads
            else:
                connection = await native.Connection.open(inspector)
                if fault == "pid_reuse":
                    f.ops.processes[30] = Process(30, 0, 999.0, "/usr/lib/xorg/Xorg")
                    with pytest.raises(BusinessError):
                        connection.cookie()
                    assert not reads
                else:
                    assert connection.cookie() == b"owned-cookie-123" and reads == [True]
                    await connection.verify()
        finally:
            if connection is not None:
                assert await connection.close()
            assert await inspector.close()
        assert sock.closed or fault in {"link", "shared", "file_uid"}

    asyncio.run(scenario())
