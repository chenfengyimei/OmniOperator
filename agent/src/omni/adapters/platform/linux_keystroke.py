"""Single paired XTEST wire delivery and retained release; business target/permit integration remains external."""

from __future__ import annotations

import asyncio
import struct
from collections.abc import Awaitable, Callable

from .linux_bus import failure
from .linux_input import KeyEvents, KeyTransition, X11Input
from .linux_xtest import Sources


class KeyPacket:
    """Own exact bytes on one proven connection; completion proves transport/release, not application handling."""

    def __init__(
        self, monitor: X11Input, opcode: int, sources: Sources, code: int, transition: KeyTransition | None = None
    ) -> None:
        wire = monitor.wire
        if (
            not monitor.armed
            or wire is None
            or wire.setup is None
            or wire.focus is None
            or wire.changed
            or wire.key_lease is not None
            or wire.key_events is not None
            or not 128 <= opcode <= 255
            or opcode == wire.opcode
            or not wire.setup.minimum_keycode <= code <= wire.setup.maximum_keycode
            or not 2 <= sources.keyboard <= 65535
            or not 2 <= sources.keyboard_master <= 65535
            or sources.keyboard == sources.keyboard_master
            or wire.sequence + 3 > 1024
        ):
            raise failure("desktop_input_unverified")
        self.monitor, self.wire = monitor, wire
        self.sequence, self.code, self.sources = wire.sequence, code, sources
        # The 4-byte request header is separate from the 32-byte event body.
        # Core keyboard, zero delay/root/coordinates; no modifier/layout writes.
        self.packet = b"".join(
            struct.pack("<BBH", opcode, 2, 9) + struct.pack("<BBHIIIIhh8x", kind, code, 0, 0, 0, 0, 0, 0, 0)
            for kind in (2, 3)
        )
        self.events = KeyEvents(wire.sequence + 1, code, sources.keyboard, sources.keyboard_master, transition)
        self.position = 0
        self.attempted = self.settled = self.sending = self.read_started = self.read_complete = False
        self._settlement: asyncio.Task[bool] | None = None
        self._wait_write: Callable[[], Awaitable[None]] = self._writable

    @property
    def release_pending(self) -> bool:
        return self.position >= 36 and not self.settled

    async def _writable(self) -> None:
        loop = asyncio.get_running_loop()
        descriptor = self.wire.connection.fileno()
        ready: asyncio.Future[None] = loop.create_future()

        def wake() -> None:
            if not ready.done():
                ready.set_result(None)

        loop.add_writer(descriptor, wake)
        try:
            await ready
        finally:
            loop.remove_writer(descriptor)

    async def _write(self) -> None:
        async with asyncio.timeout(0.5):
            while self.position < len(self.packet):
                self.wire.check()
                try:
                    sent = self.wire.connection.send(self.packet[self.position :])
                except BlockingIOError:
                    await self._wait_write()
                    continue
                if not 1 <= sent <= len(self.packet) - self.position:
                    raise failure("desktop_x11_disconnected")
                self.position += sent

    async def emit(self) -> None:
        if self.attempted:
            raise failure("desktop_input_unverified")
        self.attempted = True
        wire = self.wire
        if wire.sequence != self.sequence or wire.changed or wire.key_lease is not None or wire.key_events is not None:
            raise failure("desktop_input_unverified")
        wire.check()
        # Register actual ownership before any byte can reach Xorg. Reserving
        # both sequences forbids queries until the release suffix is complete.
        wire.key_lease, wire.key_events = self, self.events
        wire.sequence += 2
        self.sending = True
        try:
            await self._write()
        finally:
            self.sending = False
        if not await self.settle():
            raise failure("desktop_input_unverified")
        if wire.changed or not self.events.complete:
            raise failure("desktop_user_intervened")
        if self.events.transition is not None:
            try:
                await self.events.transition.verify_after()
            except BaseException:
                wire.changed = True
                raise

    async def _finish(self) -> bool:
        try:
            if self.position < 36:
                # An incomplete press is discarded by closing this stream. Do
                # not complete a new press merely to make cancellation clean.
                if self.monitor.connection is None or not await self.monitor.connection.close():
                    return False
                self.wire.changed = True
            else:
                await self._write()  # only the remaining release bytes
                self.read_started = True
                self.read_complete = False
                async with asyncio.timeout(0.5):
                    keys = await self.wire.key_barrier(self)
                self.read_complete = True
                if len(keys) != 40 or any(keys[8:40]):
                    return False
            self.settled = True
            self.wire.key_lease = self.wire.key_events = None
            return True
        except Exception:  # noqa: BLE001 -- uncertainty retains the actual connection/release lease
            return False

    async def settle(self) -> bool:
        if self.settled:
            return True
        if not self.attempted or self.sending or self.wire.key_lease is not self:
            return False
        if self._settlement is None or self._settlement.done() and (not self.read_started or self.read_complete):
            self._settlement = asyncio.create_task(self._finish())
        # Cancellation of a waiter cannot cancel an in-flight release/query or
        # orphan it: the monitor still owns this lease and this exact task.
        return await asyncio.shield(self._settlement)
