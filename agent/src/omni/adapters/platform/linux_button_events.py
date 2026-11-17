"""Exact RawButton pairs and their ordinary XKB pointer-state notifications; no blanket input exemptions."""

from __future__ import annotations

from .linux_button_mapping import ButtonPlan
from .linux_motion_events import MotionTransition
from .linux_x11_wire import unpack


class ButtonEvents:
    def __init__(self, sequence: int, plan: ButtonPlan, keyboard: int, transition: MotionTransition) -> None:
        self.sequence, self.plan, self.keyboard_master, self.transition = sequence, plan, keyboard, transition
        self.stage = 0
        self.time: int | None = None

    def device(self, header: bytes, payload: bytes) -> bool:
        return self.stage == 0 and self.transition.device(header, payload, self.sequence)

    def accept(self, header: bytes, payload: bytes) -> bool:
        if self.stage not in {0, 1, 4, 5} or not self.transition.complete:
            return False
        release = self.stage >= 4
        words = unpack("H", header, 22)[0]
        if (
            unpack("H", header, 2)[0] != self.sequence + int(release)
            or unpack("HH", header, 8)
            != (16 if release else 15, self.plan.master if self.stage % 2 else self.plan.source)
            or unpack("I", header, 16)[0] != self.plan.physical
            or unpack("H", header, 20)[0] != self.plan.source
            or unpack("I", header, 24)[0]
            or words > 8
            or len(payload) != words * 4
            or any(payload)
        ):
            return False
        timestamp = unpack("I", header, 12)[0]
        if self.stage % 2 and timestamp != self.time:
            return False
        self.time = timestamp
        self.stage += 1
        return True

    def keyboard(self, header: bytes) -> bool:
        if self.stage not in {2, 3, 6, 7}:
            return False
        release, master = self.stage >= 6, bool(self.stage % 2)
        code = self.plan.master_code if master else self.plan.physical
        mapped = self.plan.logical if master else self.plan.master_code
        mask = 0 if release or mapped > 5 else 1 << (mapped + 7)
        # ProcessDeviceEvent restores the incoming detail on the normal path.
        # An activated passive grab returns before that restoration on press;
        # its mapped detail is another precisely predicted native outcome.
        codes = {code} if release else {code, mapped}
        if (
            header[1] != 2
            or unpack("H", header, 2)[0] != self.sequence + int(release)
            or header[8] != self.keyboard_master
            or any(header[9:24])
            or unpack("HH", header, 24) != (mask, 1 << 13)
            or header[28] not in codes
            or header[29] != (5 if release else 4)
            or any(header[30:32])
        ):
            return False
        self.stage += 1
        return True

    @property
    def complete(self) -> bool:
        return self.stage == 8 and self.transition.complete
