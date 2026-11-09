"""Kernel Unix peer UID/GID and PID; never accepts a TCP credential result or an empty native option."""

from __future__ import annotations

import ctypes
import socket
import struct
import sys

from ...domain.contracts import BusinessError


def peer_identity(connection: socket.socket) -> tuple[int, int, int]:
    if (
        sys.platform != "darwin"
        or connection.family != socket.AF_UNIX
        or connection.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM
    ):
        raise BusinessError("service_admin_peer_invalid", "Require the fixed local stream peer")
    library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    method = library.getpeereid
    method.argtypes, method.restype = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p], ctypes.c_int
    uid, gid = ctypes.c_uint32(), ctypes.c_uint32()
    raw = connection.getsockopt(0, 2, 4)  # SOL_LOCAL / LOCAL_PEERPID, a kernel pid_t.
    if len(raw) != 4 or method(connection.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0:
        raise BusinessError("service_admin_peer_invalid", "Kernel peer credentials are unavailable")
    pid = struct.unpack("=i", raw)[0]
    if pid <= 0:
        raise BusinessError("service_admin_peer_invalid", "Kernel peer PID is invalid")
    return pid, uid.value, gid.value
