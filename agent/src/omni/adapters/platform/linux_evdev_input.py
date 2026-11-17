"""Owned read-only evdev neutral-state/readiness checks; no event contents, grabs, writes, permission or routing changes."""

from __future__ import annotations

import ctypes
import importlib
import os
import platform
import stat
import struct
import sys
from collections.abc import Callable
from typing import cast

from .linux_bus import failure
from .linux_fds import OwnedFd
from .linux_udev_input import InputDevice, UdevInput

BITS_BYTES, MAX_SLOTS = 8192, 64  # full uint16 event-code bitmap
StatIdentity = tuple[int, int, int, int, int, int, int]


def request(number: int, size: int) -> int:
    return 2 << 30 | size << 16 | ord("E") << 8 | number


def native_ioctl(descriptor: int, command: int, data: bytearray) -> int:
    return cast(int, importlib.import_module("fcntl").ioctl(descriptor, command, data, True))


def native_ready(descriptors: tuple[int, ...]) -> bool:
    poll = getattr(importlib.import_module("select"), "poll")()
    for descriptor in descriptors:
        poll.register(descriptor, 3)  # POLLIN|POLLPRI; ERR/HUP/NVAL are unconditional
    return bool(poll.poll(0))


def bit(data: bytes | bytearray, code: int) -> bool:
    return bool(data[code // 8] & 1 << (code % 8))


class EvdevInput:
    def __init__(
        self,
        *,
        udev_factory: Callable[[], UdevInput] = UdevInput,
        ioctl: Callable[[int, int, bytearray], int] = native_ioctl,
        ready: Callable[[tuple[int, ...]], bool] = native_ready,
    ) -> None:
        self.udev_factory, self.ioctl, self.ready = udev_factory, ioctl, ready
        self.udev: UdevInput | None = None
        self.devices: tuple[InputDevice, ...] = ()
        self.descriptors: list[OwnedFd] = []
        self.state_descriptors: list[OwnedFd] = []
        self.identities: list[StatIdentity] = []
        self.seat = ""
        self.attempted = self.closed = self.uncertain = False

    @property
    def fds(self) -> tuple[int, ...]:
        return (() if self.udev is None else self.udev.fds) + tuple(value.fileno() for value in self.descriptors)

    def _identity(self, descriptor: int, device: InputDevice) -> StatIdentity:
        observed = os.fstat(descriptor)
        current = os.stat(device.node, follow_symlinks=False)
        fields = ("st_dev", "st_ino", "st_rdev", "st_uid", "st_gid", "st_mode", "st_ctime_ns")
        identity = cast(StatIdentity, tuple(getattr(observed, field) for field in fields))
        if (
            not stat.S_ISCHR(observed.st_mode)
            or observed.st_uid != 0
            or identity[2] != device.number
            or identity != tuple(getattr(current, field) for field in fields)
            or not os.access(device.node, os.R_OK, effective_ids=True, follow_symlinks=False)
        ):
            raise failure("portal_input_unverified")
        return identity

    def _read(
        self, descriptor: int, number: int, size: int, *, bitmap: bool = False, initial: bytes = b""
    ) -> bytearray:
        data = bytearray(size)
        data[: len(initial)] = initial
        result = self.ioctl(descriptor, request(number, size), data)
        if type(result) is not int or (not 1 <= result <= size if bitmap else result != 0):
            raise failure("portal_input_unverified")
        if bitmap and any(data[result:]):
            raise failure("portal_input_unverified")
        return data

    def _neutral(self, descriptor: int, device: InputDevice) -> None:
        if struct.unpack("=I", self._read(descriptor, 1, 4))[0] != 0x010001:
            raise failure("portal_input_unavailable")
        types = self._read(descriptor, 0x20, BITS_BYTES, bitmap=True)
        if not bit(types, 0):
            raise failure("portal_input_unverified")
        keys = self._read(descriptor, 0x21, BITS_BYTES, bitmap=True) if bit(types, 1) else bytes(BITS_BYTES)
        if bit(types, 1) and any(self._read(descriptor, 0x18, BITS_BYTES, bitmap=True)):
            raise failure("portal_input_held")
        axes = self._read(descriptor, 0x23, BITS_BYTES, bitmap=True) if bit(types, 3) else bytes(BITS_BYTES)
        multitouch = any(bit(axes, code) for code in range(0x2F, 0x3E))
        if multitouch:
            if not bit(axes, 0x2F) or not bit(axes, 0x39):
                raise failure("portal_input_unverified")
            value, minimum, maximum, _fuzz, _flat, _resolution = struct.unpack(
                "=iiiiii", self._read(descriptor, 0x40 + 0x2F, 24)
            )
            if minimum != 0 or not 0 <= value <= maximum < MAX_SLOTS:
                raise failure("portal_input_unverified")
            slots = self._read(descriptor, 0x0A, (maximum + 2) * 4, initial=struct.pack("=i", 0x39))
            if struct.unpack_from("=i", slots)[0] != 0x39 or any(
                struct.unpack_from("=i", slots, (index + 1) * 4)[0] != -1 for index in range(maximum + 1)
            ):
                raise failure("portal_input_held")
        elif device.touch and not any(bit(keys, code) for code in (0x14A, 0x140, 0x141, 0x145)):
            raise failure("portal_input_unverified")

    def _quiet(self) -> None:
        if self.ready(self.fds):
            raise failure("portal_input_changed")

    def open(self, seat: str, uid: int) -> None:
        if self.attempted or self.closed:
            raise failure("portal_state_invalid")
        self.attempted = True
        if (
            sys.platform != "linux"
            or sys.byteorder != "little"
            or ctypes.sizeof(ctypes.c_void_p) != 8
            or platform.machine().lower() not in {"x86_64", "aarch64", "riscv64"}
            or uid <= 0
            or getattr(os, "getuid")() != uid
            or getattr(os, "geteuid")() != uid
        ):
            raise failure("portal_input_unavailable")
        self.seat = seat
        for parent in ("/dev", "/dev/input"):
            info = os.stat(parent, follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise failure("portal_input_unverified")
        self.udev = self.udev_factory()
        self.udev.open()  # both change monitors precede catalog and device opens
        self.devices = self.udev.catalog(seat)
        for device in self.devices:
            # EVIOCGKEY flushes its client's key queue. Two independent opens
            # create distinct evdev clients; dup would share the same client.
            # The watch client never receives state ioctls or event reads.
            for owned in (self.descriptors, self.state_descriptors):
                descriptor = OwnedFd(
                    os.open(
                        device.node,
                        os.O_RDONLY | getattr(os, "O_NONBLOCK") | getattr(os, "O_CLOEXEC") | getattr(os, "O_NOFOLLOW"),
                    )
                )
                owned.append(descriptor)
                os.set_inheritable(descriptor.fileno(), False)
                identity = self._identity(descriptor.fileno(), device)
                if owned is self.descriptors:
                    self.identities.append(identity)
                elif identity != self.identities[-1]:
                    raise failure("portal_input_changed")
        all_fds = self.fds + tuple(value.fileno() for value in self.state_descriptors)
        if len(set(all_fds)) != len(all_fds):
            raise failure("portal_input_unverified")
        self.check()

    def check(self) -> None:
        if self.closed or self.uncertain or self.udev is None or not self.devices:
            raise failure("portal_input_unverified")
        self._quiet()
        if self.udev.catalog(self.seat) != self.devices:
            raise failure("portal_input_changed")
        for device, descriptor, state, identity in zip(
            self.devices, self.descriptors, self.state_descriptors, self.identities, strict=True
        ):
            if (
                self._identity(descriptor.fileno(), device) != identity
                or self._identity(state.fileno(), device) != identity
            ):
                raise failure("portal_input_changed")
            self._neutral(state.fileno(), device)
            if (
                self._identity(descriptor.fileno(), device) != identity
                or self._identity(state.fileno(), device) != identity
            ):
                raise failure("portal_input_changed")
        self._quiet()

    def close(self) -> bool:
        self.closed = True
        for descriptor in (*self.descriptors, *self.state_descriptors):
            try:
                descriptor.close()
            except OSError:
                self.uncertain = True  # never retry an ambiguous close against a recycled fd
        return (self.udev is None or self.udev.close()) and not self.uncertain
