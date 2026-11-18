"""Bind actual AT-SPI component hit/physical extents to a continuous X11 window; no scaling guesses or input."""

from __future__ import annotations

import struct
from typing import cast

from omni.adapters.platform.desktop.linux.linux_atspi import (
    ACCESSIBLE,
    COMPONENT,
    PASSWORD_ROLE,
    AtspiDesktop,
    reference,
)
from omni.adapters.platform.desktop.linux.linux_bus import RemoteError, failure
from omni.adapters.platform.desktop.linux.linux_window_binding import WindowBinding, declared_window
from omni.adapters.platform.desktop.linux.linux_x11_wire import unpack
from omni.domain.contracts import BusinessError
from omni.domain.desktop import MAX_CAPTURE_EDGE

Payload = dict[str, object]
Ref = tuple[str, str]


def rectangle(value: object) -> tuple[int, int, int, int]:
    if not isinstance(value, list):
        raise failure("desktop_bus_response_invalid")
    values = cast(list[object], value)
    if len(values) != 4 or any(type(item) is not int or abs(item) > 10000000 for item in values):
        raise failure("desktop_bus_response_invalid")
    result = cast(tuple[int, int, int, int], tuple(values))
    if not 1 <= result[2] <= MAX_CAPTURE_EDGE or not 1 <= result[3] <= MAX_CAPTURE_EDGE:
        raise failure("desktop_input_unverified")
    return result


async def hit(desktop: AtspiDesktop, root: Ref, target: Ref, point: tuple[int, int], coordinates: int) -> None:
    current, seen = root, {root}
    for _ in range(8):
        found = reference(
            await desktop.call(current, COMPONENT, "GetAccessibleAtPoint", "iiu", [*point, coordinates], returns="(so)")
        )
        if found[0] != root[0] or found in seen:
            raise failure("desktop_input_unverified")
        ancestor, chain = found, {found}
        for _ in range(8):
            role, flags = await desktop.role_state(ancestor)
            if role == PASSWORD_ROLE or not {8, 24, 25, 30} <= flags:
                raise failure("desktop_input_unverified")
            ancestor = reference(await desktop.property(ancestor, ACCESSIBLE, "Parent", "(so)"))
            if ancestor == current:
                break
            if ancestor[0] != root[0] or ancestor in chain:
                raise failure("desktop_input_unverified")
            chain.add(ancestor)
        else:
            raise failure("desktop_input_unverified")
        if found == target:
            return
        # A provider may return an immediate child or the deepest descendant.
        # Both need an actual bounded parent path inside this hit subtree.
        seen.add(found)
        current = found
    raise failure("desktop_input_unverified")


async def point_context(desktop: AtspiDesktop, root: Ref, ref: Ref) -> Payload | None:
    """Optional only for the known explicit-XID physical-pixel provider; root self-hit is insufficient."""
    if ref == root or desktop.inspector.session is None or desktop.inspector.session.kind != "x11":
        return None
    try:
        window = declared_window(await desktop.call(root, ACCESSIBLE, "GetAttributes", returns="a{ss}"))
        screen = rectangle(await desktop.call(root, COMPONENT, "GetExtents", "u", [0], returns="(iiii)"))
        local_root = rectangle(await desktop.call(root, COMPONENT, "GetExtents", "u", [1], returns="(iiii)"))
        bounds = rectangle(await desktop.call(ref, COMPONENT, "GetExtents", "u", [0], returns="(iiii)"))
        local = rectangle(await desktop.call(ref, COMPONENT, "GetExtents", "u", [1], returns="(iiii)"))
        if local_root != (0, 0, *screen[2:]) or local != (bounds[0] - screen[0], bounds[1] - screen[1], *bounds[2:]):
            raise failure("desktop_input_unverified")
        left, top = max(screen[0], bounds[0]), max(screen[1], bounds[1])
        right, bottom = (
            min(screen[0] + screen[2], bounds[0] + bounds[2]),
            min(screen[1] + screen[3], bounds[1] + bounds[3]),
        )
        if right <= left or bottom <= top:
            raise failure("desktop_input_unverified")
        point = (left + (right - left - 1) // 2, top + (bottom - top - 1) // 2)
        relative = point[0] - screen[0], point[1] - screen[1]
        for coordinates, selected in ((0, point), (1, relative)):
            if await desktop.call(ref, COMPONENT, "Contains", "iiu", [*selected, coordinates], returns="b") is not True:
                raise failure("desktop_input_unverified")
            await hit(desktop, root, ref, selected, coordinates)
        return {
            "window": window,
            "window_bounds": list(screen),
            "bounds": list(bounds),
            "local_bounds": list(local),
            "point": list(point),
            "local_point": list(relative),
        }
    except (BusinessError, RemoteError):
        return None


class NodePoint:
    def __init__(
        self, desktop: AtspiDesktop, binding: WindowBinding, target: Payload, ref: Ref, context: Payload
    ) -> None:
        self.desktop, self.binding, self.target, self.ref = desktop, binding, target, ref
        self.context = {key: value for key, value in context.items() if key != "state"}
        self._attempted = self._ready = False

    async def _read(self) -> None:
        await self.binding.check()
        root = await self.desktop.verified(self.target)
        _role, root_flags = await self.desktop.role_state(root)
        role, flags = await self.desktop.role_state(self.ref)
        if 1 not in root_flags or role == PASSWORD_ROLE or not {8, 24, 25, 30} <= flags:
            raise failure("desktop_observation_changed")
        if await point_context(self.desktop, root, self.ref) != self.context:
            raise failure("desktop_observation_changed")
        focus = self.binding.focus
        wire = self.binding.monitor.wire
        if focus is None or wire is None or wire.setup is None or focus.selected_window != self.context["window"]:
            raise failure("desktop_input_unverified")
        window = focus.selected_window
        data = await wire.request(14, struct.pack("<I", window), maximum=0)
        native_root, _x, _y, width, height, border = cast(
            tuple[int, int, int, int, int, int], unpack("IhhHHH", data, 8)
        )
        if len(data) != 32 or native_root != wire.setup.root or border or not data[1]:
            raise failure("desktop_input_unverified")
        data = await wire.request(40, struct.pack("<IIhh", window, wire.setup.root, 0, 0), maximum=0)
        x, y = cast(tuple[int, int], unpack("hh", data, 12))
        if len(data) != 32 or data[1] != 1 or [x, y, width, height] != self.context["window_bounds"]:
            raise failure("desktop_target_changed")
        await self.binding.check()

    async def prepare(self) -> tuple[int, int]:
        if self._attempted:
            raise failure("desktop_input_unverified")
        self._attempted = True
        try:
            await self._read()
            await self._read()
            local = cast(list[int], self.context["local_point"])
            self._ready = True
            return local[0], local[1]
        except BaseException:
            self._ready = False
            raise

    async def check(self) -> None:
        if not self._ready:
            raise failure("desktop_input_unverified")
        try:
            await self._read()
        except BaseException:
            self._ready = False
            raise
