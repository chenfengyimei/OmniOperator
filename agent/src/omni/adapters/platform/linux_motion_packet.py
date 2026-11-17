"""Own one XTEST absolute move and its acknowledgment; no business click or automatic retry."""

from __future__ import annotations

import asyncio
import struct
from collections.abc import Awaitable, Callable

from .linux_bus import failure
from .linux_input import X11Input
from .linux_motion_events import MotionEvents, MotionTransition
from .linux_pointer_motion import MotionPlan
from .linux_x11_wire import unpack
from .linux_xtest import Sources


class MotionPacket:
    def __init__(
        self,
        monitor: X11Input,
        opcode: int,
        sources: Sources,
        plan: MotionPlan,
        transition: MotionTransition,
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
            or wire.motion_lease is not None
            or wire.motion_events is not None
            or wire.button_lease is not None
            or wire.button_events is not None
            or not 128 <= opcode <= 255
            or opcode == wire.opcode
            or not 2 <= sources.pointer <= 65535
            or not 2 <= sources.pointer_master <= 65535
            or sources.pointer == sources.pointer_master
            or plan.point.root != wire.setup.root
            or any(type(value) is not int or not -32768 <= value <= 32767 for value in plan.request)
            or wire.sequence + 2 > wire.request_limit
        ):
            raise failure("desktop_input_unverified")
        self.monitor, self.wire, self.plan, self.sequence = monitor, wire, plan, wire.sequence
        self.packet = struct.pack("<BBH", opcode, 2, 9) + struct.pack(
            "<BBHIIIIhh8x", 6, 0, 0, 0, plan.point.root, 0, 0, *plan.request
        )
        self.events = MotionEvents(wire.sequence + 1, sources.pointer, sources.pointer_master, plan, transition)
        self.position = 0
        self.attempted = self.sending = self.settled = self.read_started = False
        self.reply: bytes | None = None
        self._settlement: asyncio.Task[bool] | None = None
        self._wait_write: Callable[[], Awaitable[None]] = self._writable

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
        if (
            wire.sequence != self.sequence
            or wire.changed
            or wire.key_lease is not None
            or wire.key_events is not None
            or wire.motion_lease is not None
            or wire.motion_events is not None
            or wire.button_lease is not None
            or wire.button_events is not None
        ):
            raise failure("desktop_input_unverified")
        wire.check()
        wire.motion_lease, wire.motion_events = self, self.events
        wire.sequence += 1
        try:
            self.sending = True
            try:
                await self._write()
            finally:
                self.sending = False
            if not await self.settle():
                raise failure("desktop_input_unverified")
            reply = self.reply
            if (
                reply is None
                or reply[1] != 1
                or unpack("I", reply, 8)[0] != self.plan.point.root
                or unpack("hh", reply, 16) != (self.plan.point.x, self.plan.point.y)
                or unpack("H", reply, 24)[0]
                or wire.changed
                or not self.events.complete
            ):
                raise failure("desktop_user_intervened")
            await self.events.transition.verify_after()
        except BaseException:
            wire.changed = True
            raise

    async def _finish(self) -> bool:
        try:
            if self.position < len(self.packet):
                # Dropping an incomplete request is safe; completing it here
                # would introduce a new move after cancellation.
                if self.monitor.connection is None or not await self.monitor.connection.close():
                    return False
                self.wire.changed = True
            else:
                self.read_started = True
                async with asyncio.timeout(0.5):
                    reply = await self.wire.motion_barrier(self)
                if len(reply) != 32 or reply[1] not in {0, 1} or unpack("I", reply, 8)[0] < 2:
                    return False
                self.reply = reply
            # A valid barrier proves processing, even when the cursor/mask or
            # raw receipts make the move outcome unverified. No button is held
            # by this action; it has no key-style release suffix to send.
            self.settled = True
            self.wire.motion_lease = self.wire.motion_events = None
            return True
        except Exception:  # noqa: BLE001 -- retain the exact stream/task when acknowledgment is uncertain
            return False

    async def settle(self) -> bool:
        if self.settled:
            return True
        if not self.attempted or self.sending or self.wire.motion_lease is not self:
            return False
        if self._settlement is None or self._settlement.done() and not self.read_started:
            self._settlement = asyncio.create_task(self._finish())
        return await asyncio.shield(self._settlement)
