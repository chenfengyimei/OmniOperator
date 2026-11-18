"""Independent libudev ABI/catalog boundary peers; actual pipe ownership, no native enumeration."""

import ctypes
import os

import pytest
from v2_seat_input_helpers import UdevLibrary

from omni.adapters.platform.desktop.linux.linux_udev_input import UdevInput
from omni.domain.contracts import BusinessError


def test_both_change_groups_precede_exact_seat_catalog_with_pointer_and_devnum_widths():
    library = UdevLibrary(
        [
            {"node": b"/dev/input/event9", "number": 0x100000003, b"ID_INPUT_TOUCHSCREEN": b"1"},
            {"node": b"/dev/input/event2", "number": 0x100000004, b"ID_SEAT": b"seat2"},
            dict(node=None),
        ]
    )
    native = UdevInput(loader=lambda name: library)
    try:
        native.open()
        descriptors = native.fds
        assert len(descriptors) == 2 and all(not os.get_inheritable(fd) for fd in descriptors)
        catalog = native.catalog("seat0")
        assert len(catalog) == 1 and catalog[0].node == "/dev/input/event9" and catalog[0].touch
        assert catalog[0].number == 0x100000003
        assert library.functions["udev_device_get_devnum"].restype is ctypes.c_uint64
        assert library.functions["udev_device_new_from_syspath"].restype is ctypes.c_void_p
        groups = [args[1] for name, args in library.calls if name == "udev_monitor_new_from_netlink"]
        assert groups == [b"kernel", b"udev"]
        assert [name for name, _ in library.calls].index("udev_enumerate_scan_devices") > [
            name for name, _ in library.calls
        ].index("udev_monitor_enable_receiving")
        assert not any("receive_device" in name for name, _ in library.calls)
        assert native.close()
        for fd in descriptors:
            with pytest.raises(OSError):
                os.fstat(fd)
    finally:
        native.close()
        library.cleanup()


@pytest.mark.parametrize(
    "fault",
    [
        "empty",
        "cycle",
        "duplicate",
        "path",
        "uninitialized",
        "unknown_node",
        "flag",
        "input_tag",
        "seat",
        "udev_enumerate_scan_devices",
    ],
)
def test_missing_incomplete_or_ambiguous_catalog_is_never_accepted(fault):
    row = dict(node=b"/dev/input/event0", number=2)
    rows = [row]
    if fault == "empty":
        rows = []
    if fault == "duplicate":
        rows = [row, row.copy()]
    if fault == "path":
        row["path"] = b"/sys/devices/../input/event0"
    if fault == "uninitialized":
        row["initialized"] = 0
    if fault == "unknown_node":
        row["node"] = b"/tmp/event0"
    if fault == "flag":
        row[b"ID_INPUT_TOUCHPAD"] = b"yes"
    if fault == "input_tag":
        row[b"ID_INPUT"] = b"0"
    if fault == "seat":
        row[b"ID_SEAT"] = b"seat2"
    library = UdevLibrary(rows, fault=fault)
    native = UdevInput(loader=lambda name: library)
    try:
        native.open()
        with pytest.raises(BusinessError):
            native.catalog("seat0")
        assert native.close()
    finally:
        native.close()
        library.cleanup()


@pytest.mark.parametrize("count", [64, 65])
def test_exact_device_bound_is_complete_or_refused_without_truncation(count):
    library = UdevLibrary([dict(node=f"/dev/input/event{i}".encode(), number=i + 1) for i in range(count)])
    native = UdevInput(loader=lambda name: library)
    try:
        native.open()
        if count == 64:
            assert len(native.catalog("seat0")) == 64
        else:
            with pytest.raises(BusinessError):
                native.catalog("seat0")
        assert native.close()
    finally:
        native.close()
        library.cleanup()


@pytest.mark.parametrize("fault", ["udev_new", "udev_monitor_new_from_netlink", "udev_monitor_enable_receiving"])
def test_partial_native_setup_keeps_original_objects_for_single_owner_cleanup(fault):
    library = UdevLibrary(fault=fault)
    native = UdevInput(loader=lambda name: library)
    try:
        with pytest.raises(BusinessError):
            native.open()
        assert native.close() and not library.monitors
    finally:
        native.close()
        library.cleanup()


def test_ambiguous_unref_is_retained_pending_without_double_release():
    library = UdevLibrary(fault="udev_monitor_unref")
    native = UdevInput(loader=lambda name: library)
    try:
        native.open()
        assert not native.close()
        original = [entry for entry in library.calls if entry[0].endswith("_unref")]
        assert not native.close() and [entry for entry in library.calls if entry[0].endswith("_unref")] == original
    finally:
        library.cleanup()  # fake C handles on pipes, never a clean native receipt


@pytest.mark.parametrize("count", [256, 257])
def test_exact_total_enumeration_bound_includes_parent_objects_without_truncating(count):
    library = UdevLibrary([dict(node=b"/dev/input/event0", number=7)] + [dict(node=None) for _ in range(count - 1)])
    native = UdevInput(loader=lambda name: library)
    try:
        native.open()
        if count == 256:
            assert len(native.catalog("seat0")) == 1
        else:
            with pytest.raises(BusinessError):
                native.catalog("seat0")
        assert native.close()
    finally:
        native.close()
        library.cleanup()
