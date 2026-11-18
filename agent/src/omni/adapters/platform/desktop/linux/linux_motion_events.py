"""Exact two-axis RawMotion receipts belonging to one reserved XTEST request."""

from __future__ import annotations

from typing import Protocol

from omni.adapters.platform.desktop.linux.linux_pointer_config import fixed
from omni.adapters.platform.desktop.linux.linux_pointer_motion import MotionPlan
from omni.adapters.platform.desktop.linux.linux_x11_wire import unpack


class MotionTransition(Protocol):
    @property
    def complete(self) -> bool: ...

    def device(self, header: bytes, payload: bytes, sequence: int) -> bool: ...

    async def verify_after(self) -> None: ...


class MotionEvents:
    def __init__(
        self,
        sequence: int,
        source: int,
        master: int,
        plan: MotionPlan,
        transition: MotionTransition,
    ) -> None:
        self.sequence, self.source, self.master, self.plan = sequence, source, master, plan
        self.transition = transition
        self.seen = 0
        self.time: int | None = None

    def device(self, header: bytes, payload: bytes) -> bool:
        return self.seen == 0 and self.transition.device(header, payload, self.sequence)

    def accept(self, header: bytes, payload: bytes) -> bool:
        if self.seen >= 2 or not self.transition.complete:
            return False
        words = unpack("H", header, 22)[0]
        if (
            unpack("H", header, 2)[0] != self.sequence
            or unpack("HH", header, 8) != (17, self.source if self.seen == 0 else self.master)
            or unpack("I", header, 16)[0]
            or unpack("H", header, 20)[0] != self.source
            or unpack("I", header, 24)[0]
            or not 1 <= words <= 8
            or len(payload) != words * 4 + 32
            or unpack("I", payload)[0] != 3
            or any(payload[4 : words * 4])
        ):
            return False
        position = words * 4
        # FP32.32 is signed integral followed by unsigned fraction, not a
        # little-endian int64. Processed values precede unprocessed values.
        processed = fixed(payload, position), fixed(payload, position + 8)
        raw = fixed(payload, position + 16), fixed(payload, position + 24)
        timestamp = unpack("I", header, 12)[0]
        if processed != self.plan.processed or raw != self.plan.raw or self.seen == 1 and timestamp != self.time:
            return False
        self.time = timestamp
        self.seen += 1
        return True

    @property
    def complete(self) -> bool:
        return self.seen == 2 and self.transition.complete
