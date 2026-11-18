"""Continuous X11 window generations on one proven connection; no input, AT-SPI mapping or database access."""

from __future__ import annotations

import socket
import struct
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from omni.adapters.platform.desktop.linux.linux_bus import failure
from omni.adapters.platform.desktop.linux.linux_x11 import X11Desktop
from omni.adapters.platform.desktop.linux.linux_x11_wire import Wire, unpack
from omni.domain.desktop import validate_target
from omni.domain.linux_window_leases import MAX_GROUPS, MAX_TARGETS, MAX_TOTAL_BYTES

STRUCTURE = frozenset({17, 18, 19, 21, 22, 24, 26})
Payload = dict[str, object]


class LeaseWire(Wire):
    def __init__(self, connection: socket.socket, check: Callable[[], None]) -> None:
        super().__init__(connection, check)
        self.generations: dict[int, int] = {}

    async def read(self, length: int) -> bytes:
        if self.received + length > MAX_TOTAL_BYTES:
            raise failure("desktop_x11_protocol_limit")
        return await super().read(length)

    async def event(self, header: bytes) -> None:
        # StructureNotify is selected on each target itself. Requests for
        # substructure redirection, synthetic events or another window cannot
        # be mistaken for a lifetime notification for this connection.
        if len(header) != 32 or header[0] not in STRUCTURE:
            raise failure("desktop_x11_protocol_invalid")
        event, window = unpack("II", header, 4)
        if event != window or window not in self.generations:
            raise failure("desktop_x11_protocol_invalid")
        self.generations[window] += 1

    async def subscribe(self, window: int) -> None:
        if window in self.generations:
            return
        if len(self.generations) >= MAX_GROUPS * MAX_TARGETS:
            raise failure("desktop_busy")
        self.generations[window] = 0
        await self.send(2, struct.pack("<III", window, 1 << 11, 1 << 17))
        await self.geometry(window)

    async def unsubscribe(self, window: int) -> None:
        if window not in self.generations:
            return
        await self.send(2, struct.pack("<III", window, 1 << 11, 0))
        await self.request(43)  # barrier drains already-queued structure events
        self.generations.pop(window)


@dataclass(frozen=True)
class Lease:
    target: Payload
    original: Payload
    window: int
    generation: int


class WindowLeases:
    def __init__(self, desktop: X11Desktop) -> None:
        self.desktop = desktop
        self.groups: OrderedDict[str, dict[str, Lease]] = OrderedDict()

    @property
    def wire(self) -> LeaseWire:
        if not isinstance(self.desktop.wire, LeaseWire):
            raise failure("desktop_x11_protocol_invalid")
        return self.desktop.wire

    async def tick(self) -> None:
        # One reply fences queued events for every watched window. The same
        # connection, logind session and Xorg process remain owned throughout.
        if self.desktop.connection is None:
            raise failure("desktop_target_changed")
        await self.desktop.connection.verify()
        await self.wire.request(43)

    async def _prune(self) -> None:
        active = {lease.window for group in self.groups.values() for lease in group.values()}
        for window in set(self.wire.generations) - active:
            await self.wire.unsubscribe(window)

    async def bind(self, targets: list[Payload]) -> list[Payload]:
        if not 1 <= len(targets) <= MAX_TARGETS:
            raise failure("desktop_protocol_invalid")
        checked = [validate_target(target) for target in targets]
        windows = [self.desktop.window(target) for target in checked]
        if len(set(windows)) != len(windows):
            raise failure("desktop_protocol_invalid")
        if len(self.groups) == MAX_GROUPS:
            self.groups.popitem(last=False)
            await self._prune()
        group_id = str(uuid.uuid4())
        created: dict[str, Lease] = {}
        self.groups[group_id] = created
        try:
            for original, window in zip(checked, windows, strict=True):
                await self.wire.subscribe(window)
                generation = self.wire.generations[window]
                await self.desktop.verified(original)
                if self.wire.generations[window] != generation:
                    raise failure("desktop_target_changed")
                native_id = "x11-live:" + str(uuid.uuid4())
                target = validate_target({**original, "native_id": native_id})
                created[native_id] = Lease(target, original, window, generation)
            await self.tick()
            if any(self.wire.generations[lease.window] != lease.generation for lease in created.values()):
                raise failure("desktop_target_changed")
            return [lease.target for lease in created.values()]
        except BaseException:
            self.groups.pop(group_id, None)
            await self._prune()
            raise

    async def verify(self, supplied: Payload) -> Payload:
        expected = validate_target(supplied)
        native_id = cast(str, expected["native_id"])
        lease = next((group[native_id] for group in self.groups.values() if native_id in group), None)
        if lease is None or lease.target != expected:
            raise failure("desktop_target_changed")
        await self.tick()
        if self.wire.generations.get(lease.window) != lease.generation:
            raise failure("desktop_target_changed")
        await self.desktop.verified(lease.original)
        if self.wire.generations.get(lease.window) != lease.generation:
            raise failure("desktop_target_changed")
        return lease.original
