"""EI seat/device inventory and guarded binding on a granted connection; no emulation, coordinate inference or input."""

from __future__ import annotations

import asyncio
import math
import socket
import struct
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ...domain.desktop_control import devices
from .linux_bus import failure
from .linux_eis import ORDER, EisConnection, interface, native_socket
from .linux_eis_keymap import MAX_KEYMAP, Keymap, KeymapInfo
from .linux_fds import OwnedFd, ReceivedFds

POINTER = ("ei_pointer", "ei_pointer_absolute", "ei_button", "ei_scroll")
MAX_SEATS, MAX_DEVICES, MAX_REGIONS = 4, 4, 16
Region = tuple[int, int, int, int, float, str | None]
Ref = tuple[str, int, bool]


def text(payload: bytes, maximum: int = 256) -> tuple[str | None, int]:
    if len(payload) < 4:
        raise failure("portal_eis_protocol_invalid")
    length = struct.unpack_from(ORDER + "I", payload)[0]
    size = 4 + (length + 3) // 4 * 4
    if length > maximum + 1 or size > len(payload):
        raise failure("portal_eis_protocol_invalid")
    if length == 0:
        return None, size
    raw = payload[4 : 4 + length]
    if raw[-1:] != b"\0" or b"\0" in raw[:-1]:
        raise failure("portal_eis_protocol_invalid")
    try:
        return raw[:-1].decode("utf-8"), size
    except UnicodeError:
        raise failure("portal_eis_protocol_invalid") from None


def name(payload: bytes) -> str | None:
    value, consumed = text(payload)
    if consumed != len(payload):
        raise failure("portal_eis_protocol_invalid")
    return value


@dataclass(frozen=True)
class SeatInfo:
    identity: int
    name: str | None
    capabilities: tuple[tuple[str, int], ...]
    done: bool
    destroyed: bool


@dataclass(frozen=True)
class DeviceInfo:
    identity: int
    seat: int
    version: int
    name: str | None
    interfaces: tuple[Ref, ...]
    regions: tuple[Region, ...]
    keymap: KeymapInfo | None
    modifiers: tuple[int, int, int, int] | None
    done: bool
    resumed: bool
    destroyed: bool
    epoch: int


@dataclass(frozen=True)
class Inventory:
    seat: int
    mask: int
    seats: tuple[SeatInfo, ...]
    devices: tuple[DeviceInfo, ...]


class Seat:
    def __init__(self) -> None:
        self.name: str | None = None
        self.named = self.done = self.destroyed = False
        self.capabilities: dict[str, int] = {}


class Device:
    def __init__(self, seat: int, version: int) -> None:
        self.seat, self.version = seat, version
        self.name: str | None = None
        self.kind: int | None = None
        self.named = self.done = self.resumed = self.destroyed = False
        self.interfaces: dict[str, int] = {}
        self.removed: set[str] = set()
        self.regions: list[Region] = []
        self.pending_mapping: str | None = None
        self.keymap: KeymapInfo | None = None
        self.modifiers: tuple[int, int, int, int] | None = None
        self.epoch = 0


class EisDevices(EisConnection):
    def __init__(
        self,
        descriptor: OwnedFd,
        guard: Callable[[], Awaitable[None]],
        requested: int,
        *,
        socket_factory: Callable[[int], socket.socket] = native_socket,
    ) -> None:
        self.requested = devices(requested)
        capabilities = (("ei_keyboard",) if self.requested & 1 else ()) + (POINTER if self.requested & 2 else ())
        self.supported = (
            *EisConnection.supported,
            ("ei_seat", 1),
            ("ei_device", 2),
            *((key, 1) for key in capabilities),
        )
        super().__init__(descriptor, guard, socket_factory=socket_factory)
        self._rights = ReceivedFds(maximum=4)
        self._capabilities = capabilities
        self._seats: dict[int, Seat] = {}
        self._devices: dict[int, Device] = {}
        self._interfaces: dict[int, tuple[int, str]] = {}
        self._keymaps: list[Keymap] = []
        self._binding = self._bind_attempted = False
        self._selected: int | None = None
        self._mask = 0
        self._bound: tuple[str, ...] = ()

    def _accept_rights(self) -> bool:
        return self._binding and "ei_keyboard" in self._bound

    def _serial(self, payload: bytes) -> None:
        if len(payload) != 4:
            raise failure("portal_eis_protocol_invalid")
        value = struct.unpack(ORDER + "I", payload)[0]
        if self.serial is None or (value - self.serial) % (1 << 32) >= (1 << 31):
            raise failure("portal_eis_protocol_invalid")
        self.serial = value

    async def _event(self, identity: int, opcode: int, payload: bytes) -> None:
        if identity == self._object and opcode == 1 and len(payload) == 12:
            seat, version = struct.unpack(ORDER + "QI", payload)
            if len(self._seats) >= MAX_SEATS:
                raise failure("portal_eis_protocol_limit")
            self._new_server(seat, version)
            self._seats[seat] = Seat()
        elif identity in self._seats:
            self._seat(identity, opcode, payload)
        elif identity in self._devices:
            self._device(identity, opcode, payload)
        elif identity in self._interfaces:
            await self._subinterface(identity, opcode, payload)
        else:
            await super()._event(identity, opcode, payload)

    def _seat(self, identity: int, opcode: int, payload: bytes) -> None:
        seat = self._seats[identity]
        if seat.destroyed:
            raise failure("portal_eis_protocol_invalid")
        if opcode == 0:
            self._serial(payload)
            seat.destroyed = True
            for device in self._devices.values():
                if device.seat == identity:
                    device.destroyed, device.resumed = True, False
                    device.epoch += 1
        elif opcode == 1 and not seat.done and not seat.named:
            seat.name, seat.named = name(payload), True
        elif opcode == 2 and not seat.done and len(payload) >= 12:
            mask = struct.unpack_from(ORDER + "Q", payload)[0]
            capability, consumed = text(payload[8:], 80)
            if (
                consumed != len(payload) - 8
                or capability not in self._capabilities
                or capability in seat.capabilities
                or not mask
                or any(mask & previous for previous in seat.capabilities.values())
            ):
                raise failure("portal_eis_protocol_invalid")
            assert capability is not None
            seat.capabilities[capability] = mask
        elif opcode == 3 and not payload and not seat.done:
            seat.done = True
        elif opcode == 4 and seat.done and identity == self._selected and len(payload) == 12:
            device, version = struct.unpack(ORDER + "QI", payload)
            if len(self._devices) >= MAX_DEVICES:
                raise failure("portal_eis_protocol_limit")
            self._new_server(device, version, 2)
            self._devices[device] = Device(identity, version)
        else:
            raise failure("portal_eis_protocol_invalid")

    def _device(self, identity: int, opcode: int, payload: bytes) -> None:
        device = self._devices[identity]
        if device.destroyed:
            raise failure("portal_eis_protocol_invalid")
        if opcode == 0:
            self._serial(payload)
            device.destroyed, device.resumed = True, False
            device.epoch += 1
        elif opcode in {7, 8} and device.done:
            self._serial(payload)
            if opcode == 7 and device.resumed:
                raise failure("portal_eis_protocol_invalid")
            device.resumed = opcode == 7
            if opcode == 8 and device.keymap is not None:
                device.modifiers = (0, 0, 0, 0)
            device.epoch += 1
        elif device.done:
            raise failure("portal_eis_protocol_invalid")
        elif opcode == 1 and not device.named:
            device.name, device.named = name(payload), True
        elif opcode == 2 and device.kind is None and payload == struct.pack(ORDER + "I", 1):
            device.kind = 1  # physical devices are receiver-only
        elif opcode == 4 and len(payload) == 20:
            x, y, width, height, scale = struct.unpack(ORDER + "IIIIf", payload)
            if (
                not width
                or not height
                or not math.isfinite(scale)
                or not 0 < scale <= 1000000
                or x + width > 0xFFFFFFFF
                or y + height > 0xFFFFFFFF
                or len(device.regions) >= MAX_REGIONS
            ):
                raise failure("portal_eis_protocol_invalid")
            device.regions.append((x, y, width, height, scale, device.pending_mapping))
            device.pending_mapping = None
        elif opcode == 5 and len(payload) >= 16:
            child = struct.unpack_from(ORDER + "Q", payload)[0]
            capability, version = interface(payload[8:])
            if capability not in self._bound or capability in device.interfaces:
                raise failure("portal_eis_protocol_invalid")
            self._new_server(child, version)
            device.interfaces[capability] = child
            self._interfaces[child] = (identity, capability)
        elif opcode == 6 and not payload:
            if device.kind != 1 or not device.interfaces or device.pending_mapping is not None:
                raise failure("portal_eis_protocol_invalid")
            if "ei_pointer_absolute" in device.interfaces and not device.regions:
                raise failure("portal_eis_protocol_invalid")
            device.done = True
        elif opcode == 12 and device.version >= 2 and device.pending_mapping is None:
            value = name(payload)
            if not value:
                raise failure("portal_eis_protocol_invalid")
            device.pending_mapping = value
        else:
            raise failure("portal_eis_protocol_invalid")

    async def _subinterface(self, identity: int, opcode: int, payload: bytes) -> None:
        parent, capability = self._interfaces[identity]
        device = self._devices[parent]
        if device.destroyed or capability in device.removed:
            raise failure("portal_eis_protocol_invalid")
        if opcode == 0:
            self._serial(payload)
            device.removed.add(capability)
            device.epoch += 1
        elif (
            capability == "ei_keyboard"
            and opcode == 1
            and not device.done
            and device.keymap is None
            and len(payload) == 8
        ):
            kind, size = struct.unpack(ORDER + "II", payload)
            if kind != 1 or not 1 <= size <= MAX_KEYMAP:
                raise failure("portal_eis_keymap_invalid")
            keymap = Keymap(self._rights.claim_next(), size)
            self._keymaps.append(keymap)
            device.keymap = await keymap.load()
            device.modifiers = (0, 0, 0, 0)
        elif (
            capability == "ei_keyboard"
            and opcode == 3
            and device.keymap is not None
            and device.done
            and device.resumed
            and len(payload) == 20
        ):
            self._serial(payload[:4])
            depressed, locked, latched, group = struct.unpack(ORDER + "IIII", payload[4:])
            device.modifiers = (depressed, locked, latched, group)
            device.epoch += 1
        else:
            raise failure("portal_eis_protocol_invalid")

    def seats(self) -> tuple[SeatInfo, ...]:
        return tuple(
            SeatInfo(identity, seat.name, tuple(sorted(seat.capabilities.items())), seat.done, seat.destroyed)
            for identity, seat in self._seats.items()
        )

    def _inventory(self) -> Inventory:
        if self._selected is None:
            raise failure("portal_state_invalid")
        values = tuple(
            DeviceInfo(
                identity,
                value.seat,
                value.version,
                value.name,
                tuple((key, child, key not in value.removed) for key, child in sorted(value.interfaces.items())),
                tuple(value.regions),
                value.keymap,
                value.modifiers,
                value.done,
                value.resumed,
                value.destroyed,
                value.epoch,
            )
            for identity, value in self._devices.items()
        )
        return Inventory(self._selected, self._mask, self.seats(), values)

    def _complete(self) -> bool:
        if not self._devices or any(not value.done for value in self._devices.values()) or self._rights.owned:
            return False
        available = {
            name
            for value in self._devices.values()
            if not value.destroyed
            for name in value.interfaces
            if name not in value.removed
        }
        return set(self._bound) <= available

    async def bind(self, seat_id: int) -> Inventory:
        if not self._ready or self._busy or self._bind_attempted:
            raise failure("portal_state_invalid")
        self._bind_attempted = self._busy = self._binding = True
        try:
            async with asyncio.timeout(3):
                await self.guard()
                seat = self._seats.get(seat_id)
                if seat is None or not seat.done or seat.destroyed:
                    raise failure("portal_eis_seat_unavailable")
                self._bound = tuple(key for key in self._capabilities if key in seat.capabilities)
                if (
                    self.requested & 1
                    and "ei_keyboard" not in self._bound
                    or self.requested & 2
                    and not set(POINTER) & set(self._bound)
                ):
                    raise failure("portal_eis_devices_unavailable")
                self._selected = seat_id
                self._mask = 0
                for capability in self._bound:
                    self._mask |= seat.capabilities[capability]
                await self._send(seat_id, 1, struct.pack(ORDER + "Q", self._mask))
                await self._sync()
                while not self._complete():
                    await self._event(*(await self._message()))
                await self._sync()
                if not self._complete():
                    raise failure("portal_eis_devices_unavailable")
                await self.guard()
                self._check()
                return self._inventory()
        except BaseException:
            self._failed = True
            raise
        finally:
            self._busy = self._binding = False

    async def inventory(self, expected: Inventory | None = None) -> Inventory:
        await self.check()
        value = self._inventory()
        if expected is not None and value != expected:
            self._failed = True
            raise failure("portal_eis_observation_changed")
        return value

    async def close(self) -> bool:
        self._closed, self._ready = True, False
        clean = True
        for keymap in self._keymaps:
            clean = await keymap.close() and clean
        return await super().close() if clean else False
