"""Bind a planned pointer move to live single-root RandR resources and active scanouts; no input or layout mutation."""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from typing import Protocol, cast

from .linux_bus import failure
from .linux_input import X11Input
from .linux_pointer_config import PointerState
from .linux_pointer_motion import MotionPlan, plan
from .linux_pointer_target import PointerPoint
from .linux_x11_wire import padded, unpack


@dataclass(frozen=True)
class Resources:
    config_time: int
    crtcs: tuple[int, ...]
    outputs: tuple[int, ...]
    modes: tuple[int, ...]
    identity: bytes


@dataclass(frozen=True)
class Crtc:
    identity: int
    x: int
    y: int
    width: int
    height: int
    mode: int
    outputs: tuple[int, ...]
    receipt: bytes


@dataclass(frozen=True)
class Layout:
    width: int
    height: int
    resources: Resources
    crtcs: tuple[Crtc, ...]


class MotionFocus(Protocol):
    monitor: X11Input

    async def check(self) -> None: ...

    async def verify_pointer(self) -> PointerPoint: ...

    async def verify_pointer_configuration(self) -> PointerState: ...


def resources(data: bytes) -> Resources:
    if len(data) < 32 or data[0] != 1:
        raise failure("desktop_input_unverified")
    nc, no, nm, names = cast(tuple[int, int, int, int], unpack("4H", data, 16))
    if not 1 <= nc <= 32 or not 1 <= no <= 64 or not 1 <= nm <= 256 or names > 8192:
        raise failure("desktop_input_unverified")
    end = 32 + (nc + no) * 4 + nm * 32 + names
    if len(data) != (end + 3) // 4 * 4 or len(data) != 32 + unpack("I", data, 4)[0] * 4:
        raise failure("desktop_input_unverified")
    crtcs = cast(tuple[int, ...], unpack("I" * nc, data, 32))
    outputs = cast(tuple[int, ...], unpack("I" * no, data, 32 + nc * 4))
    position, total_names = 32 + (nc + no) * 4, 0
    modes: list[int] = []
    for _ in range(nm):
        identity, width, height = cast(tuple[int, int, int], unpack("IHH", data, position))
        if not width or not height:
            raise failure("desktop_input_unverified")
        modes.append(identity)
        total_names += cast(int, unpack("H", data, position + 26)[0])
        position += 32
    identities = (*crtcs, *outputs, *modes)
    if total_names != names or len(set(identities)) != len(identities) or any(value < 2 for value in identities):
        raise failure("desktop_input_unverified")
    return Resources(
        cast(int, unpack("I", data, 12)[0]),
        crtcs,
        outputs,
        tuple(modes),
        hashlib.sha256(data[8:24] + data[32:end]).digest(),
    )


def crtc(data: bytes, identity: int, current: Resources) -> Crtc:
    if len(data) < 32 or data[0:2] != b"\1\0":
        raise failure("desktop_input_unverified")
    x, y, width, height, mode, rotation, supported, no, np = cast(
        tuple[int, int, int, int, int, int, int, int, int], unpack("hhHHIHHHH", data, 12)
    )
    if no > 64 or np > 64 or len(data) != 32 + (no + np) * 4:
        raise failure("desktop_input_unverified")
    outputs = cast(tuple[int, ...], unpack("I" * no, data, 32))
    possible = cast(tuple[int, ...], unpack("I" * np, data, 32 + no * 4))
    if (
        len(set(outputs)) != no
        or len(set(possible)) != np
        or not set(outputs) <= set(possible) <= set(current.outputs)
        or rotation & ~63
        or supported & ~63
        or rotation & supported != rotation
        or rotation & 15 not in ({0, 1, 2, 4, 8} if mode == 0 else {1, 2, 4, 8})
        or (mode == 0 and (width or height or no))
        or (mode != 0 and (mode not in current.modes or not width or not height or not no))
    ):
        raise failure("desktop_input_unverified")
    return Crtc(identity, x, y, width, height, mode, outputs, data[8:])


class PointerMotion:
    def __init__(self, focus: MotionFocus) -> None:
        self.focus = focus
        self._attempted = self._valid = False
        self._opcode: int | None = None
        self._point: PointerPoint | None = None
        self._state: PointerState | None = None
        self._layout: Layout | None = None
        self._plan: MotionPlan | None = None

    async def _open(self) -> None:
        wire = self.focus.monitor.wire
        if wire is None or wire.setup is None or wire.setup.screens != 1 or wire.randr_event is not None:
            raise failure("desktop_input_unverified")
        name = b"RANDR"
        data = await wire.request(98, struct.pack("<Hxx", len(name)) + padded(name), maximum=0)
        if (
            len(data) != 32
            or data[8] != 1
            or data[9] < 128
            or data[9] == wire.opcode
            or not 64 <= data[10] <= 126
            or any(event in {wire.xkb_event, wire.button_mapping_event} for event in (data[10], data[10] + 1))
        ):
            raise failure("desktop_input_unverified")
        opcode, event = data[9], data[10]
        version = await wire.request(opcode, struct.pack("<II", 1, 6), maximum=0)
        major, minor = cast(tuple[int, int], unpack("II", version, 8))
        if len(version) != 32 or major != 1 or minor < 3:
            raise failure("desktop_input_unverified")
        self._opcode = opcode
        # Existing root/window subscriptions remain; RandR is a separate
        # per-client extension mask. Every notification revokes this plan.
        wire.randr_event = event
        await wire.send(opcode, struct.pack("<IHxx", wire.setup.root, 127 if minor >= 4 else 15), 4)

    async def _read(self) -> Layout:
        wire, point = self.focus.monitor.wire, self._point
        if wire is None or wire.setup is None or self._opcode is None or point is None or point.root != wire.setup.root:
            raise failure("desktop_input_unverified")
        geometry = await wire.request(14, struct.pack("<I", point.root), maximum=0)
        root, x, y, width, height, border = cast(tuple[int, int, int, int, int, int], unpack("IhhHHH", geometry, 8))
        if (
            len(geometry) != 32
            or root != point.root
            or x
            or y
            or border
            or not 1 <= width <= 32767
            or not 1 <= height <= 32767
        ):
            raise failure("desktop_input_unverified")
        # Native XF86 RandR resources are unavailable under legacy panoramic
        # roots; single advertised root alone is not a desktop-origin proof.
        current = resources(await wire.request(self._opcode, struct.pack("<I", point.root), 25, maximum=32768))
        crtcs: list[Crtc] = []
        assigned: set[int] = set()
        for identity in current.crtcs:
            data = await wire.request(self._opcode, struct.pack("<II", identity, current.config_time), 20, maximum=512)
            described = crtc(data, identity, current)
            if assigned & set(described.outputs):
                raise failure("desktop_input_unverified")
            assigned.update(described.outputs)
            crtcs.append(described)
        if not any(
            item.mode and item.x <= point.x < item.x + item.width and item.y <= point.y < item.y + item.height
            for item in crtcs
        ):
            raise failure("desktop_input_unverified")
        return Layout(width, height, current, tuple(crtcs))

    async def _guard(self) -> None:
        if (
            await self.focus.verify_pointer_configuration() != self._state
            or await self.focus.verify_pointer() != self._point
        ):
            raise failure("desktop_user_intervened")

    async def prepare(self) -> MotionPlan:
        if self._attempted:
            raise failure("desktop_input_unverified")
        self._attempted = True
        try:
            self._state = await self.focus.verify_pointer_configuration()
            self._point = await self.focus.verify_pointer()
            await self._open()
            await self.focus.check()
            layout = await self._read()
            await self.focus.check()
            if await self._read() != layout:
                raise failure("desktop_user_intervened")
            result = plan(self._point, self._state, layout.width, layout.height)
            await self._guard()
            self._layout, self._plan, self._valid = layout, result, True
            return result
        except BaseException:
            self._valid = False
            raise

    async def verify(self) -> MotionPlan:
        if not self._valid or self._plan is None:
            raise failure("desktop_input_unverified")
        try:
            await self._guard()
            if await self._read() != self._layout:
                raise failure("desktop_user_intervened")
            await self._guard()
            return self._plan
        except BaseException:
            self._valid = False
            raise

    async def read_current(self) -> None:
        """Actual layout read within an owned native transition bracket; no input permission."""
        if not self._valid or self._plan is None:
            raise failure("desktop_input_unverified")
        try:
            if await self._read() != self._layout:
                raise failure("desktop_user_intervened")
        except BaseException:
            self._valid = False
            raise

    def adopt_copied(self, before: PointerState, after: PointerState) -> None:
        if not self._valid or self._state != before or before.source != after.source:
            raise failure("desktop_input_unverified")
        self._state = after
