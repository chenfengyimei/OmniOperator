"""Owner-only Unix-socket launcher for the fixed macOS helper.

The helper must be started by LaunchServices so its TCC permissions belong to the
helper application instead of the responsible host ([helper TCC contract](../../../../../../docs/v2/contracts/V2_MACOS_HELPER_TCC.md)).
LaunchServices hands out no stdio pipes, so the helper connects back to a
host-owned socket and the backend keeps using its existing frame loop over that
connection. This module owns only the transport: the helper validates the socket
path, directory ownership/mode and peer uid; the host validates the connected
peer pid/executable and keeps identity-based ownership of the process.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import socket
import struct
import tempfile
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

import psutil

# Darwin does not export these through the Python socket module.
SOL_LOCAL = 0
LOCAL_PEERPID = 0x002
MAX_SOCKET_PATH = 100
DEFAULT_TIMEOUT_S = 5.0


class LaunchError(Exception):
    """The helper was not launched or the connected peer failed validation."""


class _HalfCloseWriter:
    """Close only the write side of the helper socket.

    The backend closes stdin after the single request frame to signal that no
    further work follows. For a pipe that is a half-close; closing the whole
    asyncio transport would discard the reply that is still being read.
    """

    def __init__(self, writer: asyncio.StreamWriter) -> None:
        self._writer = writer
        self._closed = False

    def write(self, data: bytes) -> None:
        self._writer.write(data)

    async def drain(self) -> None:
        await self._writer.drain()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        raw = self._writer.get_extra_info("socket")
        try:
            if raw is not None:
                raw.shutdown(socket.SHUT_WR)
        except OSError:
            pass


class _ProcessView:
    """Minimal process view used by the backend: stdio streams, pid and wait()."""

    def __init__(
        self,
        stdin: _HalfCloseWriter,
        stdout: asyncio.StreamReader,
        stderr: asyncio.StreamReader,
        pid: int,
        wait: Callable[[], Awaitable[int]],
    ) -> None:
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr
        self.pid = pid
        self.returncode: int | None = None
        self._wait = wait

    async def wait(self) -> int:
        code = await self._wait()
        self.returncode = code
        return code


def _gone(pid: int, created: float) -> bool:
    try:
        process = psutil.Process(pid)
        if process.create_time() != created:
            return True
        return process.status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True
    except psutil.Error:
        return False


async def _wait_gone(pid: int, created: float, timeout_s: float) -> bool:
    deadline = asyncio.get_running_loop().time() + max(0.0, timeout_s)
    while True:
        if _gone(pid, created):
            return True
        if asyncio.get_running_loop().time() >= deadline:
            return False
        await asyncio.sleep(0.01)


class LaunchedHelper:
    """A LaunchServices-started helper owned by pid + create time."""

    def __init__(
        self,
        stdin: _HalfCloseWriter,
        stdout: asyncio.StreamReader,
        stderr: asyncio.StreamReader,
        pid: int,
        created: float,
        directory: Path,
        socket_path: Path,
    ) -> None:
        self._pid = pid
        self._created = created
        self._directory = directory
        self._socket_path = socket_path
        self._closed = False
        self.process = _ProcessView(stdin, stdout, stderr, pid, wait=self._exit)

    def _cleanup(self) -> None:
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(Exception):
            self.process.stdin.close()
        with contextlib.suppress(OSError):
            self._socket_path.unlink()
        with contextlib.suppress(OSError):
            self._directory.rmdir()

    async def _exit(self) -> int:
        while not _gone(self._pid, self._created):
            await asyncio.sleep(0.01)
        self._cleanup()
        return 0

    async def terminate(self, timeout_s: float) -> bool:
        if self._closed:
            return True
        try:
            process = psutil.Process(self._pid)
            if process.create_time() != self._created:
                self._cleanup()
                return True
            process.terminate()
        except psutil.NoSuchProcess:
            self._cleanup()
            return True
        except psutil.Error:
            return False
        if await _wait_gone(self._pid, self._created, timeout_s):
            self._cleanup()
            return True
        try:
            process.kill()
        except psutil.NoSuchProcess:
            self._cleanup()
            return True
        except psutil.Error:
            return False
        if await _wait_gone(self._pid, self._created, max(0.5, timeout_s)):
            self._cleanup()
            return True
        return False


async def launch_helper(
    executable: Path,
    bundle: Path,
    *,
    deadline: float | None = None,
    open_command: Sequence[str] = ("/usr/bin/open",),
) -> LaunchedHelper:
    """Launch the fixed helper through LaunchServices and accept its socket.

    ``open_command`` exists for isolated transport tests; production always uses
    the fixed ``/usr/bin/open`` and the validated bundle path.
    """
    loop = asyncio.get_running_loop()
    timeout_s = DEFAULT_TIMEOUT_S if deadline is None else max(0.05, deadline - loop.time())
    directory = Path(tempfile.mkdtemp(prefix="omni-d-", dir="/tmp"))
    socket_path = directory / "s"
    if len(os.fsencode(socket_path)) >= MAX_SOCKET_PATH:
        shutil.rmtree(directory, ignore_errors=True)
        raise LaunchError("socket path exceeds the Darwin limit")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(os.fspath(socket_path))
        os.chmod(socket_path, 0o600)
        listener.listen(1)
        listener.setblocking(False)
        launcher = await asyncio.create_subprocess_exec(
            *open_command,
            "-n",
            os.fspath(bundle),
            "--args",
            "--connect",
            os.fspath(socket_path),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            code = await asyncio.wait_for(launcher.wait(), timeout_s)
        except TimeoutError as error:
            with contextlib.suppress(ProcessLookupError):
                launcher.kill()
            raise LaunchError("LaunchServices launcher did not return") from error
        if code != 0:
            raise LaunchError("LaunchServices did not accept the helper launch")
        try:
            connection, _ = await asyncio.wait_for(loop.sock_accept(listener), timeout_s)
        except TimeoutError as error:
            raise LaunchError("helper did not connect before the deadline") from error
    except BaseException:
        listener.close()
        shutil.rmtree(directory, ignore_errors=True)
        raise
    finally:
        listener.close()
    try:
        connection.setblocking(False)
        pid = struct.unpack("i", connection.getsockopt(SOL_LOCAL, LOCAL_PEERPID, 4))[0]
        process = psutil.Process(pid)
        created = process.create_time()
        if process.uids().real != os.getuid():
            raise LaunchError("connected peer is not the current user")
        if Path(process.exe()).resolve() != executable.resolve():
            raise LaunchError("connected peer is not the fixed helper")
        reader, writer = await asyncio.open_connection(sock=connection)
    except BaseException:
        connection.close()
        shutil.rmtree(directory, ignore_errors=True)
        raise
    with contextlib.suppress(OSError):
        socket_path.unlink()
    stderr = asyncio.StreamReader()
    stderr.feed_eof()
    return LaunchedHelper(_HalfCloseWriter(writer), reader, stderr, pid, created, directory, socket_path)
