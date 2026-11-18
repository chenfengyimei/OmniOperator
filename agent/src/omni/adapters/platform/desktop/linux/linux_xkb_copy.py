"""Canonical bounded identities for XKB copied compatibility, names, indicator maps and current geometry; no writes."""

from __future__ import annotations

import hashlib

from omni.adapters.platform.desktop.linux.linux_bus import failure
from omni.adapters.platform.desktop.linux.linux_x11_wire import unpack


class Reader:
    def __init__(self, data: bytes, device: int, header: bytes) -> None:
        if (
            not 32 <= len(data) <= 65568
            or data[0] != 1
            or data[1] != device
            or len(data) != 32 + unpack("I", data, 4)[0] * 4
        ):
            raise failure("desktop_input_unverified")
        self.data, self.position, self.canonical = data, 32, bytearray(header)

    def take(self, size: int, meaningful: int | None = None) -> bytes:
        if not 0 <= size <= 65536 or self.position + size > len(self.data):
            raise failure("desktop_input_unverified")
        value = self.data[self.position : self.position + size]
        self.position += size
        self.canonical.extend(value if meaningful is None else value[:meaningful])
        return value

    def string(self) -> None:
        length = unpack("H", self.take(2))[0]
        self.take(length)
        self.take(-(length + 2) % 4, 0)

    def finish(self) -> bytes:
        if self.position != len(self.data):
            raise failure("desktop_input_unverified")
        return hashlib.sha256(self.canonical).digest()


def compatibility(data: bytes, device: int) -> bytes:
    reader = Reader(data, device, data[8:9] + data[10:16])
    first, count, total = unpack("HHH", data, 10)
    if first != 0 or count != total or count > 4096 or data[8] not in {0, 15} or data[8] == 0 and count:
        raise failure("desktop_input_unverified")
    for _ in range(count):
        value = reader.take(16, 0)
        reader.canonical.extend(value if value[8] else value[:9] + b"\0" * 7)
    reader.take(data[8].bit_count() * 4)
    return reader.finish()


def indicators(data: bytes, device: int) -> bytes:
    reader = Reader(data, device, data[8:17])
    which, physical = unpack("II", data, 8)
    if which not in {0, 0xFFFFFFFF} or data[16] != which.bit_count() or not which and physical:
        raise failure("desktop_input_unverified")
    reader.take(data[16] * 12)
    return reader.finish()


def names(data: bytes, device: int, minimum: int, maximum: int) -> bytes:
    reader = Reader(data, device, data[8:28])
    which, virtual = unpack("I", data, 8)[0], unpack("H", data, 16)[0]
    indicator_mask, level_count = unpack("I", data, 20)[0], unpack("H", data, 26)[0]
    if which & ~0x3FFF or tuple(data[12:14]) != (minimum, maximum) or data[15] & ~15:
        raise failure("desktop_input_unverified")
    reader.take((which & 63).bit_count() * 4)
    if which & 64:
        reader.take(data[14] * 4)
    if which & 128:
        levels = reader.take((data[14] + 3) // 4 * 4, data[14])[: data[14]]
        if sum(levels) != level_count:
            raise failure("desktop_input_unverified")
        reader.take(level_count * 4)
    elif level_count:
        raise failure("desktop_input_unverified")
    for flag, count in ((256, indicator_mask.bit_count()), (2048, virtual.bit_count()), (4096, data[15].bit_count())):
        if which & flag:
            reader.take(count * 4)
        elif count:
            raise failure("desktop_input_unverified")
    if which & 512:
        if (data[18], data[19]) != (minimum, maximum - minimum + 1):
            raise failure("desktop_input_unverified")
        reader.take(data[19] * 4)
    else:
        # Xorg keeps the requested full-key range in this header even when
        # key names are absent. Without the mask it is not a payload count.
        reader.canonical[10:12] = b"\0\0"
    for flag, count, stride in ((1024, data[25], 8), (8192, data[24], 4)):
        if which & flag:
            reader.take(count * stride)
        elif count:
            raise failure("desktop_input_unverified")
    return reader.finish()


def geometry(data: bytes, device: int) -> bytes:
    reader = Reader(data, device, data[8:13] + data[14:32])
    if data[12] == 0:
        if any(data[8:13] + data[14:32]):
            raise failure("desktop_input_unverified")
        return reader.finish()
    if data[12] != 1:
        raise failure("desktop_input_unverified")
    properties, colors, shapes, sections, decorations, aliases = unpack("6H", data, 18)
    if any(count > 256 for count in (properties, colors, shapes, sections, decorations, aliases)):
        raise failure("desktop_input_unverified")

    def index(value: int, count: int) -> None:
        if value >= count:
            raise failure("desktop_input_unverified")

    index(data[30], colors)
    index(data[31], colors)
    reader.string()  # label font; no atom or named geometry lookup
    for _ in range(properties):
        reader.string()
        reader.string()
    for _ in range(colors):
        reader.string()
    for _ in range(shapes):
        shape = reader.take(8, 7)
        outlines = shape[4]
        for selected in shape[5:7]:
            if selected != 255:
                index(selected, outlines)
        for _ in range(outlines):
            outline = reader.take(4, 2)
            reader.take(outline[0] * 4)

    def doodads(count: int) -> None:
        for _ in range(count):
            value = reader.take(20, 0)
            kind = value[4]
            if kind in {1, 2, 5}:
                reader.canonical.extend(value[:14])
                index(value[12], colors)
                index(value[13], shapes)
                if kind == 5:
                    reader.string()
            elif kind == 3:
                reader.canonical.extend(value[:17])
                index(value[16], colors)
                reader.string()
                reader.string()
            elif kind == 4:
                reader.canonical.extend(value[:15])
                index(value[12], shapes)
                index(value[13], colors)
                index(value[14], colors)
            else:
                raise failure("desktop_input_unverified")

    for _ in range(sections):
        section = reader.take(20, 18)
        rows, local_doodads, overlays = section[15:18]
        for _ in range(rows):
            row = reader.take(8, 6)
            if row[5] not in {0, 1}:
                raise failure("desktop_input_unverified")
            for _ in range(row[4]):
                key = reader.take(8)
                index(key[6], shapes)
                index(key[7], colors)
        doodads(local_doodads)
        for _ in range(overlays):
            overlay = reader.take(8, 5)
            for _ in range(overlay[4]):
                row = reader.take(4, 2)
                index(row[0], rows)
                reader.take(row[1] * 8)
    doodads(decorations)
    reader.take(aliases * 8)
    return reader.finish()
