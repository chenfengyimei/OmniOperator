"""Single-threaded Darwin command ownership; pre-exec permit, kqueue no-fork proof and unreaped group anchor."""

from __future__ import annotations

import os
import select
import selectors
import signal
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ...domain.contracts import BusinessError


def failed(code: str) -> BusinessError:
    return BusinessError(code, "The scoped Darwin command has not proved actual cleanup")


@dataclass
class Command:
    pid: int
    selector: selectors.BaseSelector
    events: Any
    permit: int
    ready: int
    streams: set[int]
    output: bytearray = field(default_factory=bytearray)
    total: int = 0
    exited: bool = False
    forked: bool = False
    observed: bool = False
    reaped: bool = False
    status: int | None = None
    accepting: bool = True
    extras: set[int] = field(default_factory=set)
    uncertain: bool = False


class DarwinCommands:
    def __init__(self, connected: Callable[[], bool]) -> None:
        self.connected = connected
        self._owned: Command | None = None

    def _spawn(self, argv: list[str]) -> Command:
        if (
            sys.platform != "darwin"
            or threading.active_count() != 1
            or signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL
        ):
            raise failed("service_admin_process_unavailable")
        import resource

        limit = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
        if not 3 < limit <= 1_048_576:
            raise failed("service_admin_process_unavailable")
        pipes: list[int] = []
        events, selector, pid = None, None, None
        try:
            for _ in range(4):
                pipes.extend(os.pipe())
            out_r, out_w, err_r, err_w, permit_r, permit_w, ready_r, ready_w = pipes
            events, selector = getattr(select, "kqueue")(), selectors.DefaultSelector()
            pid = os.fork()
            if pid == 0:
                try:
                    os.setpgid(0, 0)
                    with open(os.devnull, "rb") as source:
                        os.dup2(source.fileno(), 0)
                    os.dup2(out_w, 1)
                    os.dup2(err_w, 2)
                    # The only inherited non-stdio descriptors are the permit
                    # reader and readiness writer. In particular no root socket,
                    # instance lock or business descriptor reaches an exec.
                    for low, high in (
                        (3, min(permit_r, ready_w)),
                        (min(permit_r, ready_w) + 1, max(permit_r, ready_w)),
                        (max(permit_r, ready_w) + 1, int(limit)),
                    ):
                        os.closerange(low, high)
                    os.write(ready_w, b"r")
                    os.close(ready_w)
                    allowed = os.read(permit_r, 1)
                    os.close(permit_r)
                    if allowed != b"x":
                        os._exit(127)
                    os.execve(argv[0], argv, {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"})
                except BaseException:  # noqa: BLE001 -- the fork child must never resume the root broker on any exception
                    os._exit(127)
            owned = Command(pid, selector, events, permit_w, ready_r, {out_r, err_r})
            owned.extras = {out_w, err_w, permit_r, ready_w}
            self._owned = owned  # Ownership is established before any fallible handle setup.
            for fd in (out_w, err_w, permit_r, ready_w):
                owned.extras.discard(fd)
                self._close_fd(owned, fd)
                pipes.remove(fd)
            for fd, retain in ((out_r, True), (err_r, False)):
                os.set_blocking(fd, False)
                selector.register(fd, selectors.EVENT_READ, retain)
            os.set_blocking(ready_r, False)
            selector.register(ready_r, selectors.EVENT_READ, "ready")
            return owned
        except BaseException:
            if pid is None:
                for fd in pipes:
                    os.close(fd)
                if events is not None:
                    events.close()
                if selector is not None:
                    selector.close()
            raise

    @staticmethod
    def _close_fd(owned: Command, descriptor: int) -> None:
        try:
            os.close(descriptor)
        except OSError:
            # close(EINTR) may have already closed the descriptor. Never retry
            # a bare number that could subsequently belong to another resource.
            owned.uncertain = True
            raise

    @staticmethod
    def _observe(owned: Command) -> None:
        event = getattr(select, "kevent")(
            owned.pid,
            filter=getattr(select, "KQ_FILTER_PROC"),
            flags=getattr(select, "KQ_EV_ADD") | getattr(select, "KQ_EV_CLEAR"),
            fflags=getattr(select, "KQ_NOTE_EXIT") | getattr(select, "KQ_NOTE_FORK"),
        )
        owned.events.control([event], 0, 0)
        owned.observed = True

    @staticmethod
    def _events(owned: Command) -> None:
        if not owned.observed:
            return
        for event in owned.events.control(None, 8, 0):
            if event.ident != owned.pid or event.flags & getattr(select, "KQ_EV_ERROR"):
                raise failed("service_admin_process_unverified")
            if event.fflags & getattr(select, "KQ_NOTE_FORK"):
                owned.forked = True
            if event.fflags & getattr(select, "KQ_NOTE_EXIT"):
                owned.exited = True

    def _drain(self, owned: Command, timeout: float) -> None:
        for key, _ in owned.selector.select(timeout):
            try:
                data = os.read(key.fd, 65536)
            except BlockingIOError:
                continue
            if key.data == "ready":
                owned.selector.unregister(key.fd)
                owned.ready = -1
                self._close_fd(owned, key.fd)
                if not owned.accepting:
                    continue
                if data != b"r":
                    raise failed("service_admin_process_unverified")
                self._observe(owned)  # Child cannot exec/fork before this succeeds.
                os.write(owned.permit, b"x")
                permit, owned.permit = owned.permit, -1
                self._close_fd(owned, permit)
            elif not data:
                owned.selector.unregister(key.fd)
                owned.streams.discard(key.fd)
                self._close_fd(owned, key.fd)
            else:
                previous = owned.total
                owned.total += len(data)
                if key.data and previous < 1_048_576:
                    owned.output.extend(data[: 1_048_576 - previous])
        self._events(owned)

    def run(self, argv: list[str]) -> tuple[int, bytes]:
        if self._owned is not None:
            raise failed("service_admin_cleanup_pending")
        try:
            owned = self._spawn(argv)
            deadline = time.monotonic() + 5
            while not (owned.exited and not owned.streams):
                if not self.connected():
                    raise failed("service_admin_peer_disconnected")
                self._drain(owned, min(0.05, max(0, deadline - time.monotonic())))
                if owned.forked:
                    raise failed("service_admin_command_forked")
                if owned.total > 1_048_576:
                    raise failed("service_admin_output_limit")
                if time.monotonic() >= deadline:
                    raise failed("service_admin_command_timeout")
        finally:
            if not self.close(2):
                raise failed("service_admin_cleanup_pending")
        assert owned.status is not None
        return owned.status, bytes(owned.output)

    def close(self, timeout_s: float = 2) -> bool:
        owned = self._owned
        if owned is None:
            return True
        if sys.platform != "darwin":
            return False
        try:
            deadline = time.monotonic() + max(0, timeout_s)
            owned.accepting = False
            if owned.uncertain:
                if not owned.reaped:
                    try:
                        if owned.observed:
                            os.killpg(owned.pid, signal.SIGKILL)
                            os.kill(owned.pid, signal.SIGKILL)
                        else:
                            os.kill(owned.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                return False
            for fd in tuple(owned.extras):
                owned.extras.discard(fd)
                self._close_fd(owned, fd)
            if owned.permit >= 0:
                permit, owned.permit = owned.permit, -1
                self._close_fd(owned, permit)  # An unpermitted child sees EOF and never execs.
            for fd in owned.streams:
                if fd not in owned.selector.get_map():
                    os.set_blocking(fd, False)
                    owned.selector.register(fd, selectors.EVENT_READ, fd == min(owned.streams))
            if not owned.reaped:
                # This direct child is continuously owned and unreaped. Its
                # reserved PID/PGID is never addressed after waitpid succeeds.
                try:
                    if owned.observed:
                        os.killpg(owned.pid, signal.SIGKILL)
                        os.kill(owned.pid, signal.SIGKILL)
                    else:
                        os.kill(owned.pid, signal.SIGKILL)  # Owned direct child has never received an exec permit.
                except ProcessLookupError:
                    pass
                if not owned.observed:
                    pid, status = os.waitpid(owned.pid, os.WNOHANG)
                    if pid != owned.pid:
                        return False
                    owned.reaped, owned.exited = True, True
                    owned.status = os.waitstatus_to_exitcode(status)
                while not (owned.exited and not owned.streams):
                    self._drain(owned, min(0.05, max(0, deadline - time.monotonic())))
                    if time.monotonic() >= deadline:
                        return False
                if owned.forked:
                    # Darwin NOTE_TRACK stopped working in 10.5. Never claim a
                    # tree was reaped by using FreeBSD tracking or historical
                    # descendant PIDs. Retain anchor/lock for explicit review.
                    return False
                if not owned.reaped:
                    pid, status = os.waitpid(owned.pid, os.WNOHANG)
                    if pid != owned.pid:
                        return False
                    owned.reaped, owned.status = True, os.waitstatus_to_exitcode(status)
            while owned.streams:
                self._drain(owned, min(0.05, max(0, deadline - time.monotonic())))
                if time.monotonic() >= deadline:
                    return False
            descriptors = (owned.ready, owned.permit)
            owned.ready = owned.permit = -1
            for fd in descriptors:
                if fd >= 0:
                    self._close_fd(owned, fd)
            owned.events.close()
            owned.selector.close()
            self._owned = None
            return True
        except (BusinessError, OSError, ValueError):
            owned.uncertain = True
            return False
