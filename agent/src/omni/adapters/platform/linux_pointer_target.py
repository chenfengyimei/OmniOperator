"""Read a bounded Xorg hit path for one physical window point; no pointer motion, button input or AT-SPI coordinate inference."""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from ...domain.desktop import MAX_CAPTURE_EDGE, validate_target
from .linux_bus import failure
from .linux_x11 import X11Desktop
from .linux_x11_wire import unpack

if TYPE_CHECKING:
    from .linux_foreground import WindowFocus

MAX_LINEAGE, MAX_CHILDREN = 16, 256


@dataclass(frozen=True)
class PointerPoint:
    root: int
    window: int
    x: int
    y: int
    lineage: tuple[int, ...]


@dataclass(frozen=True)
class Snapshot:
    point: PointerPoint
    fingerprint: bytes


def physical_point(value: object) -> tuple[int, int]:
    if not isinstance(value, tuple) or len(cast(tuple[object, ...], value)) != 2:
        raise failure("desktop_input_unverified")
    point = cast(tuple[object, object], value)
    if any(type(item) is not int or not 0 <= item < MAX_CAPTURE_EDGE for item in point):
        raise failure("desktop_input_unverified")
    return cast(tuple[int, int], point)


class PointerTarget:
    """Borrow the already proven focus connection; a read or cancellation failure permanently invalidates this preparation."""

    def __init__(self, focus: WindowFocus, original: dict[str, object], resource: int, point: object) -> None:
        self.focus, self.original, self.resource = focus, validate_target(original), resource
        self.window, self.local = X11Desktop.window(self.original), physical_point(point)
        if self.window != focus.selected_window:
            raise failure("desktop_target_changed")
        self._attempted = self._invalidated = self._ready = False
        self._snapshot: Snapshot | None = None

    async def _geometry(self, window: int, trace: bytearray) -> tuple[int, int]:
        wire = self.focus.monitor.wire
        if wire is None or wire.setup is None:
            raise failure("desktop_input_unverified")
        reply = await wire.request(14, struct.pack("<I", window), maximum=0)
        root, x, y, width, height, border = cast(tuple[int, int, int, int, int, int], unpack("IhhHHH", reply, 8))
        if len(reply) != 32 or root != wire.setup.root or not width or not height:
            raise failure("desktop_x11_protocol_invalid")
        if window == wire.setup.root:
            if x or y or border or width > 32767 or height > 32767 or not 1 <= reply[1] <= 32:
                raise failure("desktop_input_unverified")
        elif width > MAX_CAPTURE_EDGE or height > MAX_CAPTURE_EDGE:
            raise failure("desktop_input_unverified")
        if reply[1] > 32 or window == self.window and (border or not reply[1]):
            raise failure("desktop_input_unverified")
        trace.extend(struct.pack("<IIhhHHHB", window, root, x, y, width, height, border, reply[1]))
        return width, height

    async def _attributes(self, window: int, trace: bytearray) -> None:
        wire = self.focus.monitor.wire
        if wire is None:
            raise failure("desktop_input_unverified")
        reply = await wire.request(3, struct.pack("<I", window), maximum=12)
        visual, category = cast(tuple[int, int], unpack("IH", reply, 8))
        if len(reply) != 44 or category not in {1, 2} or reply[26] != 2 or reply[27] not in {0, 1}:
            raise failure("desktop_x11_target_unverified")
        if window == self.window and (category != 1 or reply[27]):
            raise failure("desktop_target_changed")
        # Event masks change when our own observer subscribes. They are not a
        # target identity or a substitute for the server's shape-aware hit test.
        trace.extend(struct.pack("<IHBB", visual, category, reply[26], reply[27]))

    async def _translate(self, source: int, destination: int, x: int, y: int) -> tuple[int, int, int]:
        wire = self.focus.monitor.wire
        if wire is None:
            raise failure("desktop_input_unverified")
        reply = await wire.request(40, struct.pack("<IIhh", source, destination, x, y), maximum=0)
        if len(reply) != 32 or reply[1] != 1:
            raise failure("desktop_input_unverified")
        child, translated_x, translated_y = cast(tuple[int, int, int], unpack("Ihh", reply, 8))
        if child == 1 or child == destination:
            raise failure("desktop_x11_protocol_invalid")
        return child, translated_x, translated_y

    async def _children(self, window: int, parent: int, trace: bytearray) -> tuple[int, ...]:
        wire = self.focus.monitor.wire
        if wire is None or wire.setup is None:
            raise failure("desktop_input_unverified")
        reply = await wire.request(15, struct.pack("<I", window), maximum=MAX_CHILDREN * 4)
        root, actual_parent, count = cast(tuple[int, int, int], unpack("IIH", reply, 8))
        if len(reply) != 32 + count * 4 or count > MAX_CHILDREN or (root, actual_parent) != (wire.setup.root, parent):
            raise failure("desktop_x11_protocol_invalid")
        children = cast(tuple[int, ...], unpack("I" * count, reply, 32))
        if len(set(children)) != count or any(child < 2 or child in {window, wire.setup.root} for child in children):
            raise failure("desktop_x11_protocol_invalid")
        trace.extend(struct.pack("<IIH", window, parent, count) + reply[32:])
        return children

    async def _owner(self, window: int, trace: bytearray) -> None:
        wire = self.focus.monitor.wire
        if wire is None:
            raise failure("desktop_input_unverified")
        client, pid = await wire.client(self.resource, window)
        inspector = self.focus.leases.desktop.inspector
        process = inspector.ops.process(pid, executable=True)
        if (
            pid != self.original["pid"]
            or process.created != self.original["process_created"]
            or process.executable != self.original["application_id"]
            or process.uid != inspector.ops.uid
        ):
            raise failure("desktop_target_changed")
        trace.extend(struct.pack("<II", client, pid))

    async def _read(self) -> Snapshot:
        wire = self.focus.monitor.wire
        if wire is None or wire.setup is None or wire.changed or self._invalidated:
            raise failure("desktop_input_unverified")
        root, trace = wire.setup.root, bytearray()
        if self.window == root:
            raise failure("desktop_input_unverified")
        width, height = await self._geometry(self.window, trace)
        if self.local[0] >= width or self.local[1] >= height:
            raise failure("desktop_target_changed")
        root_width, root_height = await self._geometry(root, trace)
        child, x, y = await self._translate(self.window, root, *self.local)
        if not 0 <= x < root_width or not 0 <= y < root_height:
            raise failure("desktop_input_unverified")
        # TranslateCoordinates chooses the first mapped child in actual stacking
        # order and checks bounding/input shapes in Xorg, unlike rectangle guesses.
        lineage, current, parent = [root], root, 0
        selected = False
        for _ in range(MAX_LINEAGE):
            children = await self._children(current, parent, trace)
            if not child:
                if not selected:
                    raise failure("desktop_target_changed")
                return Snapshot(PointerPoint(root, self.window, x, y, tuple(lineage)), hashlib.sha256(trace).digest())
            if child not in children or child in lineage or len(lineage) == MAX_LINEAGE:
                raise failure("desktop_input_unverified")
            parent, current = current, child
            lineage.append(current)
            selected = selected or current == self.window
            await self._attributes(current, trace)
            width, height = await self._geometry(current, trace)
            child, local_x, local_y = await self._translate(root, current, x, y)
            if not 0 <= local_x < width or not 0 <= local_y < height:
                raise failure("desktop_target_changed")
            if current == self.window and (local_x, local_y) != self.local:
                raise failure("desktop_target_changed")
            if selected:
                # WM frames above the selected client may belong to a different
                # process. Every receiving descendant beneath it must be owned.
                await self._owner(current, trace)
            trace.extend(struct.pack("<Ihh", current, local_x, local_y))
        raise failure("desktop_input_unverified")

    async def prepare(self) -> PointerPoint:
        if self._attempted or self._invalidated:
            raise failure("desktop_input_unverified")
        self._attempted = True
        try:
            await self.focus.check()
            initial = await self._read()
            await self.focus.monitor.watch_pointer_lineage(initial.point.lineage)
            await self.focus.check()
            if await self._read() != initial:
                raise failure("desktop_target_changed")
            await self.focus.check()
            self._snapshot, self._ready = initial, True
            return initial.point
        except BaseException:
            self._invalidated, self._ready = True, False
            raise

    async def verify(self) -> PointerPoint:
        if not self._ready or self._invalidated or self._snapshot is None:
            raise failure("desktop_input_unverified")
        try:
            await self.focus.check()
            current = await self._read()
            await self.focus.check()
            if current != self._snapshot:
                raise failure("desktop_target_changed")
            return current.point
        except BaseException:
            self._invalidated, self._ready = True, False
            raise
