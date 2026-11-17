"""Injected native seat input and watch ports; no device, permissions or desktop access."""

import asyncio
import os
import threading
from types import SimpleNamespace


class Watch:
    def __init__(self):
        self.callbacks = {}
        self.removed = []
        self.fail_remove = False

    def add(self, fd, callback):
        assert fd not in self.callbacks
        self.callbacks[fd] = callback

    def remove(self, fd):
        if self.fail_remove:
            raise OSError("private-watch-canary")
        self.callbacks.pop(fd, None)
        self.removed.append(fd)


class Source:
    def __init__(self):
        self.fds = (101, 102, 103)
        self.opened = self.checks = self.closes = 0
        self.error = None
        self.hold = None
        self.entered, self.release = threading.Event(), threading.Event()
        self.loop = None

    def pause(self, name):
        self.hold = name
        self.loop = asyncio.get_running_loop()

    def step(self, name):
        if self.hold == name:
            self.entered.set()
            assert self.release.wait(10)
        if self.error:
            raise self.error

    def open(self, seat, uid):
        assert seat == "seat0" and uid == 1000
        self.opened += 1
        self.step("open")

    def check(self):
        self.checks += 1
        self.step("check")

    def close(self):
        self.closes += 1
        if self.hold == "close":
            self.entered.set()
            assert self.release.wait(10)
        return True


def inspector():
    return SimpleNamespace(
        ops=SimpleNamespace(uid=1000), session=SimpleNamespace(seat="seat0", uid=1000, kind="wayland")
    )


class Function:
    def __init__(self, owner, name):
        self.owner, self.name = owner, name
        self.argtypes = self.restype = None

    def __call__(self, *args):
        self.owner.calls.append((self.name, args))
        return self.owner.invoke(self.name, args)


class UdevLibrary:
    context, enumeration = 0x123456780001, 0x123456780002

    def __init__(self, rows=None, fault=None):
        self.rows = rows if rows is not None else [dict(node=b"/dev/input/event0", number=0x100000003)]
        self.calls, self.functions, self.monitors, self.writers = [], {}, {}, []
        self.fault = fault

    def __getattr__(self, name):
        if not name.startswith("udev_"):
            raise AttributeError(name)
        if name not in self.functions:
            self.functions[name] = Function(self, name)
        return self.functions[name]

    def invoke(self, name, args):
        if name == self.fault:
            if name.endswith("_unref"):
                raise OSError("private-unref-canary")
            return -1 if name in {"udev_monitor_enable_receiving", "udev_enumerate_scan_devices"} else None
        if name == "udev_new":
            return self.context
        if name == "udev_monitor_new_from_netlink":
            assert args[0] == self.context and args[1] in (b"kernel", b"udev")
            handle = self.context + 100 + len(self.monitors)
            read, write = os.pipe()
            self.monitors[handle] = read
            self.writers.append(write)
            return handle
        if name == "udev_monitor_get_fd":
            return self.monitors[args[0]]
        if name == "udev_monitor_unref":
            os.close(self.monitors.pop(args[0]))
        if name == "udev_enumerate_new":
            return self.enumeration
        if name == "udev_enumerate_get_list_entry":
            return self.context + 1000 if self.rows else None
        if name == "udev_list_entry_get_next":
            index = args[0] - self.context - 1000
            return args[0] if self.fault == "cycle" else args[0] + 1 if index + 1 < len(self.rows) else None
        if name == "udev_list_entry_get_name":
            return self.rows[args[0] - self.context - 1000].get(
                "path", f"/sys/devices/input/event{args[0] - self.context - 1000}".encode()
            )
        if name == "udev_device_new_from_syspath":
            for index, row in enumerate(self.rows):
                if row.get("path", f"/sys/devices/input/event{index}".encode()) == args[1]:
                    return self.context + 2000 + index
            raise AssertionError("unknown syspath")
        if name.startswith("udev_device_get_"):
            index = args[0] - self.context - 2000
            row = self.rows[index]
            if name == "udev_device_get_devnode":
                return row.get("node")
            if name == "udev_device_get_devnum":
                return row.get("number", index + 1)
            if name == "udev_device_get_syspath":
                return row.get("path", f"/sys/devices/input/event{index}".encode())
            if name == "udev_device_get_is_initialized":
                return row.get("initialized", 1)
            if name == "udev_device_get_property_value":
                return row.get(args[1], b"1" if args[1] == b"ID_INPUT" else None)
        return 0

    def cleanup(self):
        for fd in self.monitors.values():
            os.close(fd)
        self.monitors.clear()
        for fd in self.writers:
            os.close(fd)
        self.writers.clear()
