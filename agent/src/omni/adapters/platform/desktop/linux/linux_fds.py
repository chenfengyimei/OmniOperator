"""Owned received descriptors and bounded SCM_RIGHTS; no bus or desktop policy."""

from __future__ import annotations

import array
import os
import socket
import stat

from omni.domain.contracts import BusinessError


def failure(code: str) -> BusinessError:
    return BusinessError(code, "Portal descriptor could not be verified", status=503)


class OwnedFd:
    def __init__(self, descriptor: int) -> None:
        self.descriptor = descriptor

    def fileno(self) -> int:
        if self.descriptor < 0:
            raise failure("portal_fd_closed")
        return self.descriptor

    def close(self) -> None:
        descriptor, self.descriptor = self.descriptor, -1
        if descriptor >= 0:
            os.close(descriptor)

    def verify_socket(self) -> None:
        if not stat.S_ISSOCK(os.fstat(self.fileno()).st_mode):
            raise failure("portal_fd_invalid")
        # Borrow, never steal ownership and never connect a new remote.
        borrowed = socket.socket(fileno=self.fileno())
        try:
            if (
                borrowed.family != getattr(socket, "AF_UNIX")
                or borrowed.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM
            ):
                raise failure("portal_fd_invalid")
            borrowed.getpeername()
        finally:
            borrowed.detach()


class ReceivedFds:
    def __init__(self, *, maximum: int = 1) -> None:
        if type(maximum) is not int or not 1 <= maximum <= 4:
            raise ValueError("Invalid descriptor queue limit")
        self.maximum = maximum
        self.owned: dict[int, OwnedFd] = {}
        self.received = 0

    def receive(
        self, connection: socket.socket, length: int
    ) -> tuple[bytes, list[tuple[int, int, bytes]], int, object]:
        size = array.array("i").itemsize
        # recvmsg/CMSG_SPACE/AF_UNIX are absent from the Windows stub set; the
        # platform guard lives in the caller, so read them through getattr.
        received = getattr(connection, "recvmsg")(
            length, getattr(socket, "CMSG_SPACE")((self.maximum + 1) * size), getattr(socket, "MSG_CMSG_CLOEXEC", 0)
        )
        data, ancillary, flags, address = received
        descriptors: list[int] = []
        invalid = bool(flags & (getattr(socket, "MSG_CTRUNC", 8) | getattr(socket, "MSG_TRUNC", 32)))
        for level, kind, body in ancillary:
            if level != socket.SOL_SOCKET or kind != getattr(socket, "SCM_RIGHTS"):
                invalid = True
                continue
            numbers = array.array("i")
            numbers.frombytes(body[: len(body) // size * size])
            invalid |= len(body) % size != 0
            descriptors.extend(numbers)
        try:
            for descriptor in descriptors:
                # Register every delivered fd before any validation can fail.
                self.owned[descriptor] = OwnedFd(descriptor)
            self.received += len(descriptors)
            if (
                invalid
                or len(set(descriptors)) != len(descriptors)
                or len(descriptors) > self.maximum
                or len(self.owned) > self.maximum
                or self.received > 4
            ):
                raise failure("portal_fd_invalid")
            for descriptor in descriptors:
                os.set_inheritable(descriptor, False)
        except BaseException:
            self.close()
            raise
        # dbus-next parses SCM_RIGHTS itself; this wrapper retains obligations
        # through partial frames, protocol failure and late canceled replies.
        return data, ancillary, flags, address

    def claim(self, descriptor: int) -> OwnedFd:
        if set(self.owned) != {descriptor}:
            raise failure("portal_fd_invalid")
        return self.owned.pop(descriptor)

    def claim_next(self) -> OwnedFd:
        if not self.owned:
            raise failure("portal_fd_invalid")
        return self.owned.pop(next(iter(self.owned)))

    def close(self) -> None:
        for descriptor in list(self.owned):
            self.owned.pop(descriptor).close()
