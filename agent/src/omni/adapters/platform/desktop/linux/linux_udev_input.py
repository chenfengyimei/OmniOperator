"""Read-only seat input catalog with kernel/udev change FDs; no input reads, grants or device configuration."""

from __future__ import annotations

import ctypes
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from omni.adapters.platform.desktop.linux.linux_bus import failure
from omni.domain.contracts import BusinessError

MAX_ENTRIES, MAX_DEVICES = 256, 64


@dataclass(frozen=True, order=True)
class InputDevice:
    node: str
    syspath: str
    number: int
    touch: bool


def string(value: bytes | None, maximum: int = 4096) -> str:
    if value is None or not 1 <= len(value) <= maximum:
        raise failure("portal_input_unverified")
    try:
        result = value.decode("ascii")
    except UnicodeError:
        raise failure("portal_input_unverified") from None
    if any(ord(c) < 32 or ord(c) == 127 for c in result):
        raise failure("portal_input_unverified")
    return result


class UdevInput:
    def __init__(self, *, loader: Callable[[str], Any] | None = None) -> None:
        self.context: int | None = None
        self.monitors: list[int] = []
        self.fds: tuple[int, ...] = ()
        self.uncertain = self.attempted = self.closed = False
        if loader is None and (
            sys.platform != "linux" or ctypes.sizeof(ctypes.c_ulong) != 8 or ctypes.sizeof(ctypes.c_void_p) != 8
        ):
            raise failure("portal_input_unavailable")
        try:
            self.library = (loader or ctypes.CDLL)("libudev.so.1")
            pointer, text, number = ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int
            signatures: dict[str, tuple[list[Any], Any]] = {
                "udev_new": ([], pointer),
                "udev_unref": ([pointer], pointer),
                "udev_monitor_new_from_netlink": ([pointer, text], pointer),
                "udev_monitor_filter_add_match_subsystem_devtype": ([pointer, text, text], number),
                "udev_monitor_enable_receiving": ([pointer], number),
                "udev_monitor_get_fd": ([pointer], number),
                "udev_monitor_unref": ([pointer], pointer),
                "udev_enumerate_new": ([pointer], pointer),
                "udev_enumerate_add_match_subsystem": ([pointer, text], number),
                "udev_enumerate_add_match_property": ([pointer, text, text], number),
                "udev_enumerate_scan_devices": ([pointer], number),
                "udev_enumerate_get_list_entry": ([pointer], pointer),
                "udev_enumerate_unref": ([pointer], pointer),
                "udev_list_entry_get_next": ([pointer], pointer),
                "udev_list_entry_get_name": ([pointer], text),
                "udev_device_new_from_syspath": ([pointer, text], pointer),
                "udev_device_get_devnode": ([pointer], text),
                "udev_device_get_syspath": ([pointer], text),
                "udev_device_get_devnum": ([pointer], ctypes.c_uint64),
                "udev_device_get_is_initialized": ([pointer], number),
                "udev_device_get_property_value": ([pointer, text], text),
                "udev_device_unref": ([pointer], pointer),
            }
            for name, (arguments, result) in signatures.items():
                function = getattr(self.library, name)
                function.argtypes, function.restype = arguments, result
        except (AttributeError, OSError, TypeError, ValueError):
            raise failure("portal_input_unavailable") from None

    def _release(self, name: str, value: int) -> None:
        try:
            getattr(self.library, name)(value)
        except Exception:  # noqa: BLE001 -- an ambiguous native unref is never retried
            self.uncertain = True
            raise failure("portal_input_cleanup_pending") from None

    def open(self) -> None:
        if self.attempted or self.closed:
            raise failure("portal_state_invalid")
        self.attempted = True
        self.context = self.library.udev_new()
        if not self.context:
            raise failure("portal_input_unavailable")
        descriptors: list[int] = []
        # Kernel hotplug precedes udev metadata/ACL processing. Observe both;
        # either readiness invalidates, without trusting/decoding event bodies.
        for group in (b"kernel", b"udev"):
            monitor = self.library.udev_monitor_new_from_netlink(self.context, group)
            if not monitor:
                raise failure("portal_input_unavailable")
            self.monitors.append(monitor)
            if self.library.udev_monitor_filter_add_match_subsystem_devtype(monitor, b"input", None) < 0:
                raise failure("portal_input_unavailable")
            if self.library.udev_monitor_enable_receiving(monitor) < 0:
                raise failure("portal_input_unavailable")
            descriptor = self.library.udev_monitor_get_fd(monitor)
            if type(descriptor) is not int or descriptor < 0 or descriptor in descriptors:
                raise failure("portal_input_unverified")
            os.set_inheritable(descriptor, False)
            os.set_blocking(descriptor, False)
            descriptors.append(descriptor)
        self.fds = tuple(descriptors)

    def _device(self, path: bytes, seat: str) -> InputDevice | None:
        device = self.library.udev_device_new_from_syspath(self.context, path)
        if not device:
            raise failure("portal_input_unverified")
        try:
            raw = self.library.udev_device_get_devnode(device)
            if raw is None:
                return None  # parent input objects have no event interface
            node = string(raw)
            if re.fullmatch(r"/dev/input/(?:mouse[0-9]+|mice|js[0-9]+)", node):
                return None  # all events are observed on their evdev interface
            match = re.fullmatch(r"/dev/input/event(0|[1-9][0-9]{0,4})", node)
            if match is None or self.library.udev_device_get_is_initialized(device) != 1:
                raise failure("portal_input_unverified")
            actual_seat = self.library.udev_device_get_property_value(device, b"ID_SEAT")
            if (string(actual_seat, 64) if actual_seat is not None else "seat0") != seat:
                return None
            if self.library.udev_device_get_property_value(device, b"ID_INPUT") != b"1":
                raise failure("portal_input_unverified")
            syspath = string(self.library.udev_device_get_syspath(device))
            if (
                syspath.encode() != path
                or not syspath.startswith("/sys/devices/")
                or any(part in {".", "..", ""} for part in syspath.split("/")[1:])
            ):
                raise failure("portal_input_unverified")
            number = self.library.udev_device_get_devnum(device)
            if type(number) is not int or not 0 < number < 1 << 64:
                raise failure("portal_input_unverified")
            touch = False
            for name in (b"ID_INPUT_TOUCHSCREEN", b"ID_INPUT_TOUCHPAD", b"ID_INPUT_TABLET"):
                flag = self.library.udev_device_get_property_value(device, name)
                if flag not in (None, b"0", b"1"):
                    raise failure("portal_input_unverified")
                touch |= flag == b"1"
            return InputDevice(node, syspath, number, touch)
        finally:
            self._release("udev_device_unref", device)

    def catalog(self, seat: str) -> tuple[InputDevice, ...]:
        if self.context is None or self.closed or self.uncertain or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", seat):
            raise failure("portal_input_unverified")
        enumeration = self.library.udev_enumerate_new(self.context)
        if not enumeration:
            raise failure("portal_input_unavailable")
        try:
            if any(
                (
                    self.library.udev_enumerate_add_match_subsystem(enumeration, b"input") < 0,
                    self.library.udev_enumerate_add_match_property(enumeration, b"ID_INPUT", b"1") < 0,
                    self.library.udev_enumerate_scan_devices(enumeration) < 0,
                )
            ):
                raise failure("portal_input_unavailable")
            entry = self.library.udev_enumerate_get_list_entry(enumeration)
            seen: set[int] = set()
            devices: list[InputDevice] = []
            while entry:
                if entry in seen or len(seen) >= MAX_ENTRIES:
                    raise failure("portal_input_unverified")
                seen.add(entry)
                path = string(self.library.udev_list_entry_get_name(entry)).encode()
                item = self._device(path, seat)
                if item is not None:
                    devices.append(item)
                if len(devices) > MAX_DEVICES:
                    raise failure("portal_input_unverified")
                entry = self.library.udev_list_entry_get_next(entry)
            if (
                not devices
                or len({row.node for row in devices}) != len(devices)
                or len({row.number for row in devices}) != len(devices)
            ):
                raise failure("portal_input_unverified")
            return tuple(sorted(devices))
        finally:
            self._release("udev_enumerate_unref", enumeration)

    def close(self) -> bool:
        self.closed = True
        clean = not self.uncertain
        monitors, self.monitors = self.monitors, []
        for monitor in reversed(monitors):
            try:
                self._release("udev_monitor_unref", monitor)
            except BusinessError:
                clean = False
        context, self.context = self.context, None
        if context:
            try:
                self._release("udev_unref", context)
            except BusinessError:
                clean = False
        self.fds = ()
        return clean
