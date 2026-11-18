"""Bind a canonical Wayland socket to the actual granted EI peer; no environment discovery, authority or protocol requests."""

from __future__ import annotations

import os
import re
import socket
import struct
from collections.abc import Callable
from typing import Protocol

from omni.adapters.platform.desktop.linux.linux_bus import Endpoint, LinuxOps, Process, failure
from omni.adapters.platform.desktop.linux.linux_fds import OwnedFd
from omni.adapters.platform.desktop.linux.linux_session import SessionInspector


class PeerOps(Protocol):
    uid: int

    def process(self, pid: int, *, executable: bool = False) -> Process: ...
    def connect(self, endpoint: Endpoint) -> tuple[socket.socket, Process, tuple[int, int] | None]: ...
    def peer(
        self, endpoint: Endpoint, connection: socket.socket, expected: Process, inode: tuple[int, int] | None
    ) -> None: ...


def eis_peer(descriptor: OwnedFd, ops: PeerOps) -> Process:
    descriptor.verify_socket()
    borrowed = socket.socket(fileno=descriptor.fileno())
    try:
        pid, uid, _gid = struct.unpack("3i", borrowed.getsockopt(socket.SOL_SOCKET, getattr(socket, "SO_PEERCRED"), 12))
        process = ops.process(pid, executable=True)
        if pid <= 0 or uid != ops.uid or process.uid != uid or not process.executable:
            raise failure("portal_wayland_peer_unverified")
        return process
    finally:
        borrowed.detach()


class WaylandPeer:
    def __init__(
        self,
        inspector: SessionInspector,
        source: OwnedFd,
        name: str,
        *,
        ops: PeerOps | None = None,
        read_peer: Callable[[OwnedFd, PeerOps], Process] = eis_peer,
    ) -> None:
        self.inspector, self.original, self.name = inspector, source, name
        self.ops, self.read_peer = ops, read_peer
        self.duplicate: OwnedFd | None = None
        self.connection: socket.socket | None = None
        self.process: Process | None = None
        self.connected: Process | None = None
        self.endpoint: Endpoint | None = None
        self.inode: tuple[int, int] | None = None
        self.source_number: int | None = None
        self._attempted = self._closed = self._uncertain = False

    def open(self) -> socket.socket:
        if self._attempted or self._closed:
            raise failure("portal_state_invalid")
        self._attempted = True
        session = self.inspector.session
        if (
            session is None
            or session.kind != "wayland"
            or session.uid <= 0
            or session.uid != self.inspector.ops.uid
            or not re.fullmatch(r"wayland-(0|[1-9][0-9]{0,3})", self.name)
        ):
            raise failure("portal_wayland_peer_unverified")
        ops = LinuxOps() if self.ops is None else self.ops
        self.ops = ops
        if ops.uid != session.uid:
            raise failure("portal_wayland_peer_unverified")
        self.source_number = self.original.fileno()
        # Private descriptor keeps peer verification valid if owner shutdown
        # closes its original EI number while this read-only operation settles.
        self.duplicate = OwnedFd(os.dup(self.source_number))
        os.set_inheritable(self.duplicate.fileno(), False)
        self.process = self.read_peer(self.duplicate, ops)
        self.endpoint = Endpoint(f"/run/user/{ops.uid}/{self.name}", ops.uid)
        connection, connected, inode = ops.connect(self.endpoint)
        self.connection, self.connected, self.inode = connection, connected, inode
        if (connected.pid, connected.uid, connected.created) != (
            self.process.pid,
            self.process.uid,
            self.process.created,
        ) or inode is None:
            raise failure("portal_wayland_peer_unverified")
        self.check()
        return connection

    def check(self) -> None:
        if (
            self._closed
            or self.ops is None
            or self.connection is None
            or self.duplicate is None
            or self.process is None
            or self.connected is None
            or self.endpoint is None
            or self.original.descriptor != self.source_number
            or self.read_peer(self.duplicate, self.ops) != self.process
        ):
            raise failure("portal_wayland_peer_unverified")
        self.ops.peer(self.endpoint, self.connection, self.connected, self.inode)

    def close(self) -> bool:
        self._closed = True
        for resource in (self.connection, self.duplicate):
            if resource is not None:
                try:
                    resource.close()
                except OSError:
                    self._uncertain = True
        return not self._uncertain
