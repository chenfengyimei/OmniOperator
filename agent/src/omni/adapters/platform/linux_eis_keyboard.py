"""Bind interpreted special keys to exact live EI inventory/map; planning only, no physical-input proof or dispatch."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...domain.foreground import KEYS
from .linux_bus import failure
from .linux_eis_devices import Inventory
from .linux_xkbcommon import KeyMeaning, XkbCompiler

if TYPE_CHECKING:
    from .linux_eis_devices import EisDevices


@dataclass(frozen=True)
class KeyboardPlan:
    inventory: Inventory
    device: int
    keyboard: int
    meaning: KeyMeaning


class KeyboardPlanner:
    def __init__(self, wire: EisDevices, factory: Callable[[], XkbCompiler] = XkbCompiler) -> None:
        self.wire, self.compiler = wire, factory()
        self.plan: KeyboardPlan | None = None
        self._attempted = self._failed = False

    async def prepare(self, device_id: int, key: str) -> KeyboardPlan:
        if self._attempted:
            raise failure("portal_state_invalid")
        self._attempted = True
        try:
            if key not in KEYS:
                raise failure("portal_xkb_key_unavailable")
            before = await self.wire.inventory()
            device = next((value for value in before.devices if value.identity == device_id), None)
            if (
                device is None
                or device.seat != before.seat
                or not device.done
                or not device.resumed
                or device.destroyed
                or device.keymap is None
                or device.modifiers != (0, 0, 0, 0)
            ):
                raise failure("portal_xkb_state_unverified")
            keyboards = [
                identity for name, identity, available in device.interfaces if name == "ei_keyboard" and available
            ]
            if len(keyboards) != 1:
                raise failure("portal_xkb_state_unverified")
            data = self.wire.keymap_data(device)
            meaning = await self.compiler.compile(data, device.keymap, key)
            await self.wire.inventory(before)
            self.wire.keymap_data(device)
            if meaning.keymap != device.keymap or meaning.key != key:
                raise failure("portal_xkb_keymap_invalid")
            self.plan = KeyboardPlan(before, device_id, keyboards[0], meaning)
            return self.plan
        except BaseException:
            self._failed = True
            raise

    async def verify(self) -> KeyboardPlan:
        if self.plan is None or self._failed:
            raise failure("portal_xkb_state_unverified")
        try:
            await self.wire.inventory(self.plan.inventory)
            device = next(value for value in self.plan.inventory.devices if value.identity == self.plan.device)
            data = self.wire.keymap_data(device)
            if len(data) != self.plan.meaning.keymap.size:
                raise failure("portal_xkb_keymap_invalid")
            return self.plan
        except BaseException:
            self._failed = True
            raise

    async def close(self) -> bool:
        self._failed = True
        return await self.compiler.close()
