"""Read-only evdev ioctl contracts and complete FD/catalog identity; injected kernel ports, no physical devices."""

import os
import stat
import struct
from types import SimpleNamespace

import pytest

from omni.adapters.platform.desktop.linux import linux_evdev_input as native
from omni.adapters.platform.desktop.linux.linux_udev_input import InputDevice
from omni.domain.contracts import BusinessError


class Kernel:
    def __init__(self, fault=None, touch=False, slots=2):
        self.calls, self.fault, self.touch, self.slots = [], fault, touch, slots

    def ioctl(self, fd, command, data):
        assert fd >= 0 and command >> 30 == 2, "Only IOC_READ, never write, grab or configuration"
        number, size = command & 255, (command >> 16) & 0x3FFF
        assert command & 0xFF00 == 0x4500 and size == len(data)
        self.calls.append((number, size))
        if self.fault == "ioctl":
            raise OSError("private-ioctl-canary")
        if self.fault == "short":
            return -1
        if number == 1:
            data[:] = struct.pack("=I", 0x010002 if self.fault == "version" else 0x010001)
            return 0
        if number in {0x20, 0x21, 0x23, 0x18}:
            assert size == 8192, "all uint16 codes, not a guessed last keyboard key"
            if number == 0x20:
                data[0] = 0x0B if self.touch else 0x03
            if number == 0x21:
                data[0x14A // 8] |= 1 << (0x14A % 8)
            if number == 0x18 and self.fault == "held":
                data[8191] = 128
            if number == 0x23:
                if self.fault != "type_a":
                    data[0x2F // 8] |= 1 << (0x2F % 8)
                data[0x39 // 8] |= 1 << (0x39 % 8)
            return 8192 if self.fault == "held" else 128
        if number == 0x6F:
            data[:] = struct.pack("=iiiiii", 0, 1 if self.fault == "slot_min" else 0, self.slots - 1, 0, 0, 0)
            return 0
        assert number == 0x0A and struct.unpack_from("=i", data)[0] == 0x39
        assert size == (self.slots + 1) * 4
        for index in range(self.slots):
            struct.pack_into(
                "=i", data, 4 * (index + 1), 14 if self.fault == "contact" and index == self.slots - 1 else -1
            )
        return 0


@pytest.mark.parametrize("touch,slots", [(False, 2), (True, 2), (True, 64)])
def test_exact_readonly_ioctl_layout_checks_all_key_codes_and_all_contact_slots(touch, slots):
    kernel = Kernel(touch=touch, slots=slots)
    reader = native.EvdevInput(ioctl=kernel.ioctl)
    reader._neutral(19, InputDevice("/dev/input/event0", "/sys/devices/input/event0", 2, touch))
    assert kernel.calls[:4] == [(1, 4), (0x20, 8192), (0x21, 8192), (0x18, 8192)]
    if touch:
        assert kernel.calls[-1] == (0x0A, (slots + 1) * 4)


@pytest.mark.parametrize("fault", ["ioctl", "short", "version", "held", "contact", "slot_min", "type_a", "slots"])
def test_unknown_kernel_state_held_key_or_contact_never_proves_neutral(fault):
    kernel = Kernel(fault=fault, touch=True, slots=65 if fault == "slots" else 2)
    reader = native.EvdevInput(ioctl=kernel.ioctl)
    with pytest.raises((BusinessError, OSError)):
        reader._neutral(19, InputDevice("/dev/input/event0", "/sys/devices/input/event0", 2, True))


def test_same_device_node_inode_rdev_owner_and_current_read_access_are_all_required(monkeypatch):
    before = SimpleNamespace(
        st_dev=1, st_ino=2, st_rdev=3, st_uid=0, st_gid=4, st_mode=stat.S_IFCHR | 0o660, st_ctime_ns=5
    )
    state = {"current": before, "access": True}
    monkeypatch.setattr(
        native,
        "os",
        SimpleNamespace(
            fstat=lambda fd: before,
            stat=lambda *args, **kwargs: state["current"],
            access=lambda *args, **kwargs: state["access"],
            R_OK=4,
        ),
    )
    reader, device = native.EvdevInput(), InputDevice("/dev/input/event0", "/sys/devices/input/event0", 3, False)
    assert reader._identity(12, device) == (1, 2, 3, 0, 4, before.st_mode, 5)
    for key, value in (
        ("st_ino", 9),
        ("st_uid", 1000),
        ("st_mode", stat.S_IFREG | 0o660),
        ("st_rdev", 13),
        ("st_ctime_ns", 99),
    ):
        state["current"] = SimpleNamespace(**{**vars(before), key: value})
        with pytest.raises(BusinessError):
            reader._identity(12, device)
    state["current"], state["access"] = before, False
    with pytest.raises(BusinessError):
        reader._identity(12, device)


@pytest.mark.parametrize(
    "fault", [None, "permission", "query_permission", "uid", "directory", "architecture", "endian"]
)
def test_double_catalog_actual_fd_ownership_and_readiness_never_consumes_event_data(monkeypatch, fault):
    pipes = [os.pipe() for _ in range(3)]
    device = InputDevice("/dev/input/event0", "/sys/devices/input/event0", 3, False)
    state = {"ready": False, "catalog": (device,), "denied": False}
    calls = []

    class Udev:
        fds = (pipes[0][0], pipes[1][0])

        def open(self):
            calls.append("monitors")

        def catalog(self, seat):
            assert seat == "seat0"
            calls.append("catalog")
            return state["catalog"]

        def close(self):
            calls.append("udev-close")
            return True

    def opener(path, flags):
        calls.append("device-open")
        assert path == device.node and flags == 2048 | 524288 | 131072
        if state["denied"] or fault == "permission" or fault == "query_permission" and calls.count("device-open") == 2:
            raise PermissionError("private-path-canary")
        return os.dup(pipes[2][0])

    root = SimpleNamespace(st_mode=stat.S_IFDIR | (0o777 if fault == "directory" else 0o755), st_uid=0)
    monkeypatch.setattr(
        native, "sys", SimpleNamespace(platform="linux", byteorder="big" if fault == "endian" else "little")
    )
    monkeypatch.setattr(
        native, "platform", SimpleNamespace(machine=lambda: "ppc64le" if fault == "architecture" else "x86_64")
    )
    monkeypatch.setattr(
        native,
        "os",
        SimpleNamespace(
            getuid=lambda: 0 if fault == "uid" else 1000,
            geteuid=lambda: 1000,
            stat=lambda *a, **k: root,
            open=opener,
            O_RDONLY=0,
            O_NONBLOCK=2048,
            O_CLOEXEC=524288,
            O_NOFOLLOW=131072,
            set_inheritable=os.set_inheritable,
        ),
    )
    reader = native.EvdevInput(udev_factory=Udev, ioctl=Kernel().ioctl, ready=lambda fds: state["ready"])
    monkeypatch.setattr(reader, "_identity", lambda fd, row: (1, 2, 3, 0, 4, 5, 6))
    try:
        if fault:
            with pytest.raises((BusinessError, PermissionError)):
                reader.open("seat0", 1000)
            if fault == "query_permission":
                assert len(reader.descriptors) == len(reader.identities) == 1 and not reader.state_descriptors
                descriptor = reader.descriptors[0].fileno()
                assert reader.close()
                with pytest.raises(OSError):
                    os.fstat(descriptor)
            else:
                assert not reader.descriptors and not reader.identities
            return
        reader.open("seat0", 1000)
        fd = reader.descriptors[0].fileno()
        assert calls[:3] == ["monitors", "catalog", "device-open"] and calls.count("catalog") == 2
        assert calls.count("device-open") == 2 and len(reader.state_descriptors) == 1
        query = reader.state_descriptors[0].fileno()
        assert query != fd and query not in reader.fds
        assert not os.get_inheritable(fd)
        os.write(pipes[2][1], b"event-content-canary")
        state["ready"] = True
        with pytest.raises(BusinessError):
            reader.check()
        # Readiness is the invalidation. Production never reads event contents.
        assert os.read(pipes[2][0], 20) == b"event-content-canary"
        state["ready"], state["catalog"] = False, ()
        with pytest.raises(BusinessError):
            reader.check()
        assert reader.close()
        with pytest.raises(OSError):
            os.fstat(fd)
        with pytest.raises(OSError):
            os.fstat(query)
    finally:
        reader.close()
        for read, write in pipes:
            os.close(read)
            os.close(write)


def test_native_readiness_polls_every_fd_including_errors_without_consuming_any_body(monkeypatch):
    registrations = []

    class Poll:
        def register(self, fd, flags):
            registrations.append((fd, flags))

        def poll(self, timeout):
            assert timeout == 0
            return [(17, 16)]

    monkeypatch.setattr(native, "importlib", SimpleNamespace(import_module=lambda name: SimpleNamespace(poll=Poll)))
    assert native.native_ready((17, 18, 19)) and registrations == [(17, 3), (18, 3), (19, 3)]


def test_independent_readiness_client_survives_key_state_ioctl_flushing_its_own_queue(monkeypatch):
    from omni.adapters.platform.desktop.linux.linux_fds import OwnedFd

    pipes = [os.pipe() for _ in range(4)]
    watch_fd, query_fd = pipes[2][0], pipes[3][0]
    device = InputDevice("/dev/input/event0", "/sys/devices/input/event0", 3, False)
    pending = {watch_fd: False, query_fd: False}
    calls = []
    kernel = Kernel()

    def ioctl(fd, command, data):
        if command & 255 == 0x18:
            # A complete physical press/release happens between the pre-poll
            # and current-state query. Kernel EVIOCGKEY flushes only this
            # evdev client's EV_KEY events; current bitmap is already neutral.
            pending[watch_fd] = pending[query_fd] = True
            pending[fd] = False
            calls.append(fd)
        return kernel.ioctl(fd, command, data)

    class Udev:
        fds = (pipes[0][0], pipes[1][0])

        def catalog(self, seat):
            return (device,)

        def close(self):
            return True

    reader = native.EvdevInput(ioctl=ioctl, ready=lambda fds: any(pending.get(fd, False) for fd in fds))
    reader.udev, reader.devices, reader.seat = Udev(), (device,), "seat0"
    reader.descriptors = [OwnedFd(watch_fd)]
    reader.state_descriptors = [OwnedFd(query_fd)]
    reader.identities = [(1, 2, 3, 0, 4, 5, 6)]
    monkeypatch.setattr(reader, "_identity", lambda fd, row: reader.identities[0])
    try:
        with pytest.raises(BusinessError) as caught:
            reader.check()
        assert caught.value.code == "portal_input_changed"
        assert calls == [query_fd] and pending[watch_fd] and not pending[query_fd]
    finally:
        reader.close()
        reader.state_descriptors[0].close()
        for index, (read, write) in enumerate(pipes):
            if index < 2:
                os.close(read)
            os.close(write)
