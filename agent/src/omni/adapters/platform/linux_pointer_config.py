"""Read canonical pointer axes, feedback and transforms on an existing XKB/point proof; no device writes or mouse dispatch."""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Protocol, cast

from .linux_bus import failure
from .linux_button_mapping import ButtonPlan
from .linux_input import X11Input, device_topology
from .linux_x11_wire import padded, unpack


@dataclass(frozen=True)
class Axis:
    number: int
    source: int
    label: int
    minimum: int
    maximum: int
    resolution: int
    mode: int


@dataclass(frozen=True)
class PointerDevice:
    identity: int
    button_source: int
    labels: tuple[int, ...]
    axes: tuple[Axis, ...]
    scrolls: tuple[bytes, ...]


@dataclass(frozen=True)
class PointerState:
    plan: ButtonPlan
    master: PointerDevice
    source: PointerDevice
    master_feedback: tuple[tuple[int, int, int, int], ...]
    source_feedback: tuple[tuple[int, int, int, int], ...]
    master_transform: tuple[float, ...]
    source_transform: tuple[float, ...]


class ConfigurationFocus(Protocol):
    monitor: X11Input

    async def check(self) -> None: ...

    async def verify_pointer_actions(self) -> ButtonPlan: ...


def fixed(data: bytes, position: int) -> int:
    integral, fraction = cast(tuple[int, int], unpack("iI", data, position))
    return integral * (1 << 32) + fraction


def pointer_classes(
    data: bytes, position: int, count: int, identity: int, buttons: int, allowed: set[int]
) -> tuple[PointerDevice, int]:
    """Canonical classes shared by QueryDevice and a source-class SlaveSwitch receipt."""
    if not 1 <= count <= 32 or not 1 <= buttons <= 256:
        raise failure("desktop_input_unverified")
    axes: dict[int, Axis] = {}
    scrolls: dict[int, bytes] = {}
    labels: tuple[int, ...] | None = None
    button_source = 0
    for _ in range(count):
        category, words, source = cast(tuple[int, int, int], unpack("HHH", data, position))
        end = position + words * 4
        if words < 2 or end > len(data) or source not in allowed:
            raise failure("desktop_input_unverified")
        if category == 1:
            size = cast(int, unpack("H", data, position + 6)[0])
            start = position + 8 + (buttons + 31) // 32 * 4
            if labels is not None or size != buttons or end != start + buttons * 4:
                raise failure("desktop_input_unverified")
            if any(data[position + 8 : start]):
                raise failure("desktop_input_held")
            labels = cast(tuple[int, ...], unpack("I" * buttons, data, start))
            button_source = source
        elif category == 2:
            if words != 11:
                raise failure("desktop_input_unverified")
            number, label = cast(tuple[int, int], unpack("HI", data, position + 6))
            resolution = cast(int, unpack("I", data, position + 36)[0])
            mode = data[position + 40]
            if number >= 32 or number in axes or mode not in {0, 1}:
                raise failure("desktop_input_unverified")
            axes[number] = Axis(
                number, source, label, fixed(data, position + 12), fixed(data, position + 20), resolution, mode
            )
        elif category == 3:
            if words != 6:
                raise failure("desktop_input_unverified")
            number, kind = cast(tuple[int, int], unpack("HH", data, position + 6))
            flags = cast(int, unpack("I", data, position + 12)[0])
            if number in scrolls or kind not in {1, 2} or flags & ~3 or not fixed(data, position + 16):
                raise failure("desktop_input_unverified")
            scrolls[number] = data[position : position + 10] + data[position + 12 : end]
        else:
            raise failure("desktop_input_unverified")
        position = end
    if labels is None or not {0, 1} <= axes.keys() or not scrolls.keys() <= axes.keys():
        raise failure("desktop_input_unverified")
    return PointerDevice(
        identity,
        button_source,
        labels,
        tuple(axes[number] for number in sorted(axes)),
        tuple(scrolls[number] for number in sorted(scrolls)),
    ), position


def pointer_devices(data: bytes, plan: ButtonPlan, expected: str | None) -> tuple[PointerDevice, PointerDevice]:
    topology = device_topology(data)
    if topology.fingerprint != expected:
        raise failure("desktop_user_intervened")
    allowed = {plan.master} | {
        device.identity
        for device in topology.entries
        if device.kind == 3 and device.enabled and device.attachment == plan.master
    }
    counts = {plan.master: plan.master_buttons, plan.source: plan.source_buttons}
    devices: dict[int, PointerDevice] = {}
    position = 32
    for _ in topology.entries:
        identity, _kind, _attachment, count, size = cast(
            tuple[int, int, int, int, int], unpack("HHHHH", data, position)
        )
        position += 12 + (size + 3) // 4 * 4
        if identity in counts:
            devices[identity], position = pointer_classes(
                data, position, count, identity, counts[identity], {identity} if identity == plan.source else allowed
            )
        else:
            for _ in range(count):
                position += cast(int, unpack("H", data, position + 2)[0]) * 4
    if set(devices) != set(counts):
        raise failure("desktop_input_unverified")
    return devices[plan.master], devices[plan.source]


def feedback(data: bytes) -> tuple[tuple[int, int, int, int], ...]:
    if len(data) < 32 or data[0:2] != b"\1\x16" or not 1 <= unpack("H", data, 8)[0] <= 8:
        raise failure("desktop_input_unverified")
    count = cast(int, unpack("H", data, 8)[0])
    if len(data) != 32 + count * 12 or unpack("I", data, 4)[0] * 4 != count * 12:
        raise failure("desktop_input_unverified")
    values: list[tuple[int, int, int, int]] = []
    seen: set[int] = set()
    for offset in range(32, len(data), 12):
        category, identity, length = cast(tuple[int, int, int], unpack("BBH", data, offset))
        numerator, denominator, threshold = cast(tuple[int, int, int], unpack("HHH", data, offset + 6))
        if category != 1 or length != 12 or identity in seen or not denominator:
            raise failure("desktop_input_unverified")
        seen.add(identity)
        values.append((identity, numerator, denominator, threshold))
    return tuple(values)


def transform(data: bytes, atom: int) -> tuple[float, ...]:
    if (
        len(data) != 68
        or data[0] != 1
        or unpack("I", data, 4)[0] != 9
        or unpack("III", data, 8) != (atom, 0, 9)
        or data[20] != 32
    ):
        raise failure("desktop_input_unverified")
    values = cast(tuple[float, ...], unpack("9f", data, 32))
    if not all(math.isfinite(value) for value in values):
        raise failure("desktop_input_unverified")
    a, b, c, d, e, f, g, h, i = values
    determinant = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if not math.isfinite(determinant) or determinant == 0:
        raise failure("desktop_input_unverified")
    return tuple(0.0 if value == 0 else value for value in values)


class PointerConfiguration:
    def __init__(self, focus: ConfigurationFocus) -> None:
        self.focus = focus
        self._attempted = self._valid = False
        self._plan: ButtonPlan | None = None
        self._atoms: tuple[int, int] | None = None
        self._state: PointerState | None = None

    async def _guard(self) -> None:
        if await self.focus.verify_pointer_actions() != self._plan:
            raise failure("desktop_user_intervened")

    async def _read(self, expected_topology: str | None = None) -> PointerState:
        wire, plan, atoms = self.focus.monitor.wire, self._plan, self._atoms
        if wire is None or wire.opcode is None or plan is None or atoms is None:
            raise failure("desktop_input_unverified")
        data = await wire.request(wire.opcode, struct.pack("<Hxx", 0), 48, maximum=32768)
        master, source = pointer_devices(data, plan, expected_topology or self.focus.monitor.topology)
        controls: list[tuple[tuple[int, int, int, int], ...]] = []
        matrices: list[tuple[float, ...]] = []
        for identity in (plan.master, plan.source):
            data = await wire.request(wire.opcode, struct.pack("<Bxxx", identity), 22, maximum=96)
            controls.append(feedback(data))
            data = await wire.request(
                wire.opcode, struct.pack("<HBxIIII", identity, 0, atoms[0], 0, 0, 9), 59, maximum=36
            )
            matrices.append(transform(data, atoms[1]))
        return PointerState(plan, master, source, controls[0], controls[1], matrices[0], matrices[1])

    async def prepare(self) -> PointerState:
        if self._attempted:
            raise failure("desktop_input_unverified")
        self._attempted = True
        try:
            self._plan = await self.focus.verify_pointer_actions()
            wire = self.focus.monitor.wire
            if wire is None:
                raise failure("desktop_input_unverified")
            atoms: list[int] = []
            for name in (b"Coordinate Transformation Matrix", b"FLOAT"):
                data = await wire.request(16, struct.pack("<Hxx", len(name)) + padded(name), 1, maximum=0)
                if len(data) != 32 or not unpack("I", data, 8)[0]:
                    raise failure("desktop_input_unverified")
                atoms.append(cast(int, unpack("I", data, 8)[0]))
            if atoms[0] == atoms[1]:
                raise failure("desktop_input_unverified")
            self._atoms = atoms[0], atoms[1]
            await self.focus.check()
            state = await self._read()
            await self.focus.check()
            if await self._read() != state:
                raise failure("desktop_user_intervened")
            await self._guard()
            self._state, self._valid = state, True
            return state
        except BaseException:
            self._valid = False
            raise

    async def verify(self) -> PointerState:
        if not self._valid or self._state is None:
            raise failure("desktop_input_unverified")
        try:
            await self._guard()
            if await self._read() != self._state:
                raise failure("desktop_user_intervened")
            await self._guard()
            return self._state
        except BaseException:
            self._valid = False
            raise

    async def read_current(self) -> PointerState:
        """Leaf comparison inside a complete point/source/map/XKB bracket; no permission is produced."""
        if not self._valid or self._state is None:
            raise failure("desktop_input_unverified")
        try:
            if await self._read() != self._state:
                raise failure("desktop_user_intervened")
            return self._state
        except BaseException:
            self._valid = False
            raise

    async def read_copied(self, expected: PointerState, topology: str) -> None:
        """Check a precomputed source copy inside the owner's native transition bracket; do not adopt."""
        if not self._valid or self._state is None or expected.plan != self._state.plan:
            raise failure("desktop_input_unverified")
        try:
            if await self._read(topology) != expected:
                raise failure("desktop_user_intervened")
        except BaseException:
            self._valid = False
            raise

    def adopt_copied(self, before: PointerState, after: PointerState) -> None:
        if not self._valid or self._state != before or before.plan != after.plan:
            raise failure("desktop_input_unverified")
        self._state = after
