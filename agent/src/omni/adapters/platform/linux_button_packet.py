"""Own one paired XTEST button request and retained release on the proven stream; no application-success claim."""

from __future__ import annotations

import asyncio
import struct
from collections.abc import Awaitable, Callable

from omni.domain.contracts import BusinessError

from .linux_bus import failure
from .linux_button_events import ButtonEvents
from .linux_button_mapping import ButtonPlan
from .linux_input import X11Input, device_topology
from .linux_motion_events import MotionTransition
from .linux_pointer_target import PointerPoint
from .linux_x11_wire import unpack
from .linux_xtest import Sources


class ButtonPacket:
    def __init__(
        self,
        monitor: X11Input,
        opcode: int,
        sources: Sources,
        plan: ButtonPlan,
        point: PointerPoint,
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
            or point.root != wire.setup.root
            or (sources.pointer, sources.pointer_master) != (plan.source, plan.master)
            or not 2 <= plan.source <= 65535
            or not 2 <= plan.master <= 65535
            or plan.source == plan.master
            or not 2 <= sources.keyboard_master <= 255
            or plan.logical != 1
            or not 1 <= plan.physical <= plan.source_buttons <= 255
            or not 1 <= plan.master_code <= plan.master_buttons <= 255
            or wire.sequence + 4 > wire.request_limit
        ):
            raise failure("desktop_input_unverified")
        self.monitor, self.wire, self.sequence, self.plan, self.point = monitor, wire, wire.sequence, plan, point
        self.packet = b"".join(
            struct.pack("<BBH", opcode, 2, 9) + struct.pack("<BBHIIIIhh8x", kind, plan.physical, 0, 0, 0, 0, 0, 0, 0)
            for kind in (4, 5)
        )
        self.events = ButtonEvents(wire.sequence + 1, plan, sources.keyboard_master, transition)
        self.position = 0
        self.attempted = self.sending = self.settled = self.read_started = self.read_complete = False
        self.reply: bytes | None = None
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
        wire.button_lease, wire.button_events = self, self.events
        wire.sequence += 2
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
                or unpack("I", reply, 8)[0] != self.point.root
                or unpack("hh", reply, 16) != (self.point.x, self.point.y)
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
            if self.position < 36:
                if self.monitor.connection is None or not await self.monitor.connection.close():
                    return False
                self.wire.changed = True
            else:
                await self._write()  # only the unsent release suffix; never replay press
                self.read_started, self.read_complete = True, False
                async with asyncio.timeout(0.5):
                    reply = await self.wire.button_barrier(self)
                if len(reply) != 32 or reply[1] not in {0, 1} or unpack("I", reply, 8)[0] < 2:
                    return False
                self.read_complete = True
                if unpack("H", reply, 24)[0] & 0x1F00:
                    return False
                self.reply = reply
                self.read_complete = False
                async with asyncio.timeout(0.5):
                    data = await self.wire.button_barrier(self, devices=True)
                try:
                    topology = device_topology(data)  # every enabled physical button must be released
                except BusinessError as error:
                    self.read_complete = error.code == "desktop_input_held"
                    raise
                self.read_complete = True
                entries = {item.identity: item for item in topology.entries}
                source, master = entries.get(self.plan.source), entries.get(self.plan.master)
                if (
                    source is None
                    or master is None
                    or not source.enabled
                    or not master.enabled
                    or source.kind != 3
                    or master.kind != 1
                    or source.attachment != master.identity
                    or 1 not in source.classes
                    or 1 not in master.classes
                ):
                    return False
            self.settled = True
            self.wire.button_lease = self.wire.button_events = None
            return True
        except Exception:  # noqa: BLE001 -- retain the actual stream/release obligation on uncertainty
            return False

    async def settle(self) -> bool:
        if self.settled:
            return True
        if not self.attempted or self.sending or self.wire.button_lease is not self:
            return False
        if self._settlement is None or self._settlement.done() and (not self.read_started or self.read_complete):
            self._settlement = asyncio.create_task(self._finish())
        return await asyncio.shield(self._settlement)
