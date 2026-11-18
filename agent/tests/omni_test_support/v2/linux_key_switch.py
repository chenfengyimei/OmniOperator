"""Reusable linux_key_switch fixtures, extracted without collecting test cases."""

import asyncio
import struct

import pytest

from omni.adapters.platform.desktop.linux.linux_input import devices
from omni.domain.contracts import BusinessError
from omni_test_support.v2.linux_keystroke import KeyPeer, cleanup, packet_context
from omni_test_support.v2.linux_xkb_map import context


def classes(data):
    position = 32
    for _ in range(struct.unpack_from("<H", data, 8)[0]):
        identity, _kind, _attachment, count, size = struct.unpack_from("<HHHHH", data, position)
        position += 12 + (size + 3) // 4 * 4
        for _ in range(count):
            words = struct.unpack_from("<H", data, position + 2)[0]
            yield identity, position, data[position : position + words * 4]
            position += words * 4
    assert position == len(data)


def key_count(data, count):
    result, previous = bytearray(), 0
    for identity, offset, value in classes(data):
        if identity in {3, 5}:
            result.extend(data[previous:offset])
            result.extend(struct.pack("<HHHH", 0, count + 2, identity, count))
            result.extend(struct.pack("<" + "I" * count, *range(8, 8 + count)))
            previous = offset + len(value)
    return bytes(result) + data[previous:]


def notifications(sequence, source, *, found=True):
    keyboard = bytearray(32)
    keyboard[:2] = bytes([80, 0])
    struct.pack_into("<HI", keyboard, 2, sequence, 1000)
    keyboard[8:16] = bytes([3, 3, 8, 255, 8, 255, 131, 9])
    struct.pack_into("<H", keyboard, 16, 3 if found else 1)
    device = bytearray(32)
    device[:2] = bytes([35, 130])
    struct.pack_into("<HIHHIHHB", device, 2, sequence, len(source) // 4, 1, 3, 1000, 1, 5, 1)
    return keyboard, device


class SwitchPeer(KeyPeer):
    def __init__(self, *, count=1, found=True):
        super().__init__()
        self.topology = key_count(self.topology, count)
        self.xkb.geometry_present = found
        self.switch_fault = None
        self.switched = False
        self.after_copy = None

    def send(self, data):
        start, requests, sequence = len(self.incoming), len(self.input_requests), self.sequence
        sent = super().send(data)
        if self.switched or len(self.input_requests) == requests:
            return sent
        self.switched = True
        emitted = bytes(self.incoming[start:])
        del self.incoming[start:]
        source = next(value for identity, _, value in classes(self.topology) if identity == 5)
        keyboard, device = notifications(sequence + 1, source, found=self.xkb.geometry_present)
        changed = bytearray(self.topology)
        master = next(offset for identity, offset, _ in classes(self.topology) if identity == 3)
        struct.pack_into("<H", changed, master + 4, 5)
        fault = self.switch_fault
        if fault == "keyboard_sequence":
            struct.pack_into("<H", keyboard, 2, sequence)
        elif fault == "keyboard_device":
            keyboard[8] = 5
        elif fault == "keyboard_old_device":
            keyboard[9] = 5
        elif fault == "keyboard_range":
            keyboard[10] = 9
        elif fault == "keyboard_old_range":
            keyboard[12] = 9
        elif fault == "keyboard_major":
            keyboard[14] = 132
        elif fault == "keyboard_minor":
            keyboard[15] = 8
        elif fault == "keyboard_changed":
            keyboard[16] ^= 2
        elif fault == "device_sequence":
            struct.pack_into("<H", device, 2, sequence)
        elif fault == "device_master":
            struct.pack_into("<H", device, 10, 5)
        elif fault == "device_classes":
            struct.pack_into("<H", device, 16, 2)
        elif fault == "device_source":
            struct.pack_into("<H", device, 18, 6)
        elif fault == "device_reason":
            device[20] = 2
        elif fault == "device_payload":
            source = source[:-4] + bytes(4)
        if fault == "missing_keyboard":
            keyboard = b""
        if fault == "missing_device":
            device, source = b"", b""
        prefix = bytes(keyboard) + bytes(device) + source
        if fault == "duplicate_keyboard":
            prefix = bytes(keyboard) + prefix
        elif fault == "duplicate_device":
            prefix += bytes(device) + source
        elif fault == "reverse":
            prefix = bytes(device) + source + bytes(keyboard)
        elif fault == "no_notifications":
            prefix = b""
        elif fault == "topology_other":
            changed[44] ^= 1  # master pointer name, outside the permitted key class
        elif fault == "topology_missing":
            changed = self.topology
        if fault == "late":
            self.incoming.extend(emitted + prefix)
        else:
            self.incoming.extend(prefix + emitted)
        self.topology = bytes(changed)
        if self.after_copy is not None:
            self.after_copy()
        return sent


__all__ = [
    "BusinessError",
    "KeyPeer",
    "SwitchPeer",
    "asyncio",
    "classes",
    "cleanup",
    "context",
    "devices",
    "key_count",
    "notifications",
    "packet_context",
    "pytest",
    "struct",
]
