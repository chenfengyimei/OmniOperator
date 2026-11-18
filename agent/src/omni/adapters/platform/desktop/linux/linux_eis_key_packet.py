"""Single EI key pair with separate frames, exact partial writes and retained release/barrier; no business permission or app proof."""

from __future__ import annotations

import asyncio
import struct
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from omni.adapters.platform.desktop.linux.linux_bus import failure
from omni.adapters.platform.desktop.linux.linux_eis import ORDER
from omni.adapters.platform.desktop.linux.linux_eis_keyboard import KeyboardPlan

if TYPE_CHECKING:
    from omni.adapters.platform.desktop.linux.linux_eis_devices import EisDevices

WRITE_SECONDS, OBSERVE_SECONDS = 1.0, 1.0


def monotonic_us() -> int:
    return getattr(time, "clock_gettime_ns")(getattr(time, "CLOCK_MONOTONIC")) // 1000


def frame(identity: int, opcode: int, payload: bytes = b"") -> bytes:
    return struct.pack(ORDER + "QII", identity, 16 + len(payload), opcode) + payload


class EisKeyPacket:
    def __init__(self, wire: EisDevices, plan: KeyboardPlan, *, clock: Callable[[], int] = monotonic_us) -> None:
        self.wire, self.plan, self.clock = wire, plan, clock
        self.packet = b""
        self.position = self.press_end = self.callback = 0
        self.attempted = self.settled = self.dropped = self.transport_verified = False
        self.sending = self.abort = self.unknown_write = self.pause_seen = False
        self._writer: asyncio.Task[bool] | None = None
        self._query: asyncio.Task[bool] | None = None
        self._settlement: asyncio.Task[bool] | None = None

    @property
    def release_pending(self) -> bool:
        return self.attempted and not self.settled and self.position >= self.press_end > 0

    def paused(self, device: int) -> None:
        if device == self.plan.device:
            self.pause_seen = True

    def _build(self) -> None:
        wire = self.wire
        stamp = self.clock()
        if type(stamp) is not int or not 0 <= stamp < 1 << 64:
            raise failure("portal_eis_input_unverified")
        serial, core, self.callback = wire.key_context()
        start = frame(self.plan.device, 1, struct.pack(ORDER + "II", serial, 1))
        down = frame(self.plan.keyboard, 1, struct.pack(ORDER + "II", self.plan.meaning.evdev_code, 1))
        device_frame = frame(self.plan.device, 3, struct.pack(ORDER + "IQ", serial, stamp))
        up = frame(self.plan.keyboard, 1, struct.pack(ORDER + "II", self.plan.meaning.evdev_code, 0))
        stop = frame(self.plan.device, 2, struct.pack(ORDER + "I", serial))
        sync = frame(core, 0, struct.pack(ORDER + "QI", self.callback, 1))
        self.packet = start + down + device_frame + up + device_frame + stop + sync
        self.press_end = len(start) + len(down)

    async def emit(self, before_dispatch: Callable[[], Awaitable[None]]) -> None:
        if self.attempted:
            raise failure("portal_state_invalid")
        self.attempted = True
        await before_dispatch()
        if await self.wire.verify_key() is not self.plan:
            raise failure("portal_eis_observation_changed")
        self._build()
        self.wire.claim_key(self)
        self._writer = asyncio.create_task(self._write(), name="ei-owned-key-write")
        try:
            if not await self.settle():
                raise failure("portal_eis_input_unverified")
            if self.dropped or self.pause_seen or await self.wire.verify_key() is not self.plan:
                raise failure("portal_eis_observation_changed")
            self.transport_verified = True
        except asyncio.CancelledError:
            self.abort = True
            raise

    async def _write(self) -> bool:
        wire = self.wire
        wire.key_owner(self, True)
        self.sending = True
        try:
            async with asyncio.timeout(WRITE_SECONDS):
                while self.position < len(self.packet):
                    if self.abort and self.position < self.press_end:
                        return False
                    try:
                        count = wire.send_key(self, self.packet[self.position :])
                    except BlockingIOError:
                        await wire.wait_key(self)
                        continue
                    if count < 0 or count > len(self.packet) - self.position:
                        self.unknown_write = True
                        return False
                    if count == 0:
                        return False
                    self.position += count
            return True
        except Exception:  # noqa: BLE001 -- retain exact cursor; unknown delivery never restarts a key request
            return False
        finally:
            self.sending = False
            wire.key_owner(self, False)

    async def _read(self, first: bool) -> bool:
        return await self.wire.key_barrier(self, self.callback if first else None)

    def _released(self) -> bool:
        return self.wire.key_released(self)

    def _finish(self) -> bool:
        self.settled = self.wire.settle_key(self)
        return self.settled

    async def _settle(self) -> bool:
        if self.unknown_write or self._writer is None:
            return False
        if not self._writer.done():
            done, _ = await asyncio.wait((self._writer,), timeout=OBSERVE_SECONDS)
            if not done:
                return False
        if self.unknown_write:
            return False
        if self.position < self.press_end:
            # No complete key-down request exists. Discard the incomplete
            # stream instead of completing a new press during cancellation.
            self.dropped = True
            return self._finish()
        if self.position < len(self.packet):
            self._writer = asyncio.create_task(self._write(), name="ei-owned-key-release")
            done, _ = await asyncio.wait((self._writer,), timeout=OBSERVE_SECONDS)
            if not done or self.unknown_write or self.position < len(self.packet):
                return False
        if self._query is not None and self._query.done():
            if self._query.cancelled() or self._query.exception() is not None:
                return False  # preserve failed/partial original barrier, never replace it
            if self._released():
                return self._finish()
            self._query = asyncio.create_task(self._read(False), name="ei-owned-release-refresh")
        elif self._query is None:
            self._query = asyncio.create_task(self._read(True), name="ei-owned-original-barrier")
        done, _ = await asyncio.wait((self._query,), timeout=OBSERVE_SECONDS)
        if not done or self._query.cancelled() or self._query.exception() is not None or not self._released():
            return False
        return self._finish()

    async def settle(self) -> bool:
        if self.settled:
            return True
        if not self.wire.owns_key(self):
            return False
        if self._settlement is None or self._settlement.done():
            self._settlement = asyncio.create_task(self._settle(), name="ei-owned-key-settlement")
        return await asyncio.shield(self._settlement)
