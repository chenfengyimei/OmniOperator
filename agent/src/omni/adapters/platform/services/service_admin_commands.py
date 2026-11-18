"""Single-threaded root manager process ownership using pidfds and subreaping; never uses historical PIDs."""

from __future__ import annotations

import ctypes
import os
import select
import selectors
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from omni.domain.contracts import BusinessError


def failed(code: str) -> BusinessError:
    return BusinessError(code, "The owned administrator command did not finish with verified cleanup")


@dataclass
class _Owned:
    process: subprocess.Popen[bytes]
    pidfd: int | None
    selector: selectors.BaseSelector
    stdout: bytearray
    total: int = 0
    pipes: int = 0
    reaped: bool = False


class RootCommands:
    """One actual process tree; its leader remains unreaped until all adopted children settle."""

    def __init__(self, connected: Callable[[], bool]) -> None:
        self.connected = connected
        self._owned: _Owned | None = None
        self._prctl = None

    def _prepare(self) -> None:
        if (
            sys.platform != "linux"
            or threading.active_count() != 1
            or signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL
        ):
            raise failed("service_admin_process_unavailable")
        if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
            raise failed("service_admin_process_unavailable")
        test = os.pidfd_open(os.getpid())
        os.close(test)
        libc = ctypes.CDLL(None, use_errno=True)
        prctl = libc.prctl
        prctl.argtypes, prctl.restype = (
            [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong],
            ctypes.c_int,
        )
        if prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
            raise failed("service_admin_process_unavailable")
        self._prctl = prctl

    @staticmethod
    def _children() -> list[int]:
        path = f"/proc/self/task/{os.getpid()}/children"
        with open(path, "rb") as stream:
            data = stream.read(8193)
        parts = data.split()
        if len(data) > 8192 or len(parts) > 256 or any(not item.isdigit() or len(item) > 10 for item in parts):
            raise failed("service_admin_process_limit")
        return [int(item) for item in parts]

    @staticmethod
    def _drain(owned: _Owned, timeout: float) -> None:
        for key, _ in owned.selector.select(timeout):
            try:
                block = os.read(key.fd, 65536)
            except BlockingIOError:
                continue
            if not block:
                owned.selector.unregister(key.fd)
                owned.pipes -= 1
                continue
            previous = owned.total
            owned.total += len(block)
            if key.data and previous < 1_048_576:
                owned.stdout.extend(block[: 1_048_576 - previous])

    def run(self, argv: list[str]) -> tuple[int, bytes]:
        if sys.platform != "linux":
            raise failed("service_admin_process_unavailable")
        if self._owned is not None:
            raise failed("service_admin_cleanup_pending")
        self._prepare()
        parent, prctl = os.getpid(), self._prctl
        assert prctl is not None

        def child() -> None:
            # This dedicated broker is single-threaded and already outside the
            # application. No Python user/plugin code or callbacks run here.
            if prctl(1, int(signal.SIGKILL), 0, 0, 0) != 0 or os.getppid() != parent:
                os._exit(127)

        selector = selectors.DefaultSelector()
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
                start_new_session=True,
                preexec_fn=child,
                close_fds=True,
            )
        except BaseException:
            selector.close()
            raise
        owned = _Owned(process, None, selector, bytearray())
        self._owned = owned
        # Popen has no async child watcher. With SIGCHLD default and no poll/wait,
        # this continuously owned child cannot have its PID reused before pidfd_open.
        try:
            owned.pidfd = os.pidfd_open(process.pid)
            assert process.stdout is not None and process.stderr is not None
            for stream, retain in ((process.stdout, True), (process.stderr, False)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, retain)
                owned.pipes += 1
            deadline = time.monotonic() + 5
            while True:
                if not self.connected():
                    raise failed("service_admin_peer_disconnected")
                self._drain(owned, min(0.05, max(0, deadline - time.monotonic())))
                if owned.total > 1_048_576:
                    raise failed("service_admin_output_limit")
                if self._exited(owned) and owned.pipes == 0:
                    break
                if time.monotonic() >= deadline:
                    raise failed("service_admin_command_timeout")
            output = bytes(owned.stdout)
        finally:
            if not self.close(2):
                raise failed("service_admin_cleanup_pending")
        assert process.returncode is not None
        return process.returncode, output

    def close(self, timeout_s: float = 2) -> bool:
        owned = self._owned
        if owned is None:
            return True
        if sys.platform != "linux":
            return False
        try:
            return self._cleanup(owned, timeout_s)
        except (BusinessError, OSError, ValueError, subprocess.TimeoutExpired):
            # Keep the actual child/handles and root lock; an observation or
            # descriptor failure never means that native work was reclaimed.
            return False

    @staticmethod
    def _exited(owned: _Owned) -> bool:
        if sys.platform != "linux":
            return False
        if owned.pidfd is not None:
            return bool(select.select([owned.pidfd], [], [], 0)[0])
        # pidfd/selector setup can fail after spawning. This continuously owned
        # direct child remains unreaped; WNOWAIT observes it without releasing
        # the PID/PGID anchor or performing an unbounded wait.
        return os.waitid(os.P_PID, owned.process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None

    def _cleanup(self, owned: _Owned, timeout_s: float) -> bool:
        if sys.platform != "linux":
            return False
        deadline = time.monotonic() + max(0, timeout_s)
        # The unreaped leader reserves its PID/PGID. Never address this numeric
        # process group after reaping it; no later process can become its target.
        if not owned.reaped:
            try:
                os.killpg(owned.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        while not owned.reaped:
            others = [pid for pid in self._children() if pid != owned.process.pid]
            for pid in others:
                descriptor = os.pidfd_open(pid)
                try:
                    try:
                        signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    if select.select([descriptor], [], [], 0)[0]:
                        os.waitpid(pid, os.WNOHANG)
                finally:
                    os.close(descriptor)
            self._drain(owned, min(0.05, max(0, deadline - time.monotonic())))
            # Check adoption again AFTER exit is observed. Sampling children
            # before leader exit could miss a descendant reparented meanwhile.
            if (
                owned.pipes == 0
                and self._exited(owned)
                and not any(pid != owned.process.pid for pid in self._children())
            ):
                owned.process.wait(timeout=0)
                owned.reaped = True
                break
            if time.monotonic() >= deadline:
                return False
        # A failed handle close can be retried without ever signaling a reaped
        # leader's numeric group. The pidfd is released exactly once.
        owned.selector.close()
        assert owned.process.stdout is not None and owned.process.stderr is not None
        owned.process.stdout.close()
        owned.process.stderr.close()
        if owned.pidfd is not None:
            os.close(owned.pidfd)
            owned.pidfd = None
        self._owned = None
        return True
