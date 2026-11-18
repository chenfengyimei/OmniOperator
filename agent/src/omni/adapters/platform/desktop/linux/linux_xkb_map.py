"""Bounded canonical identity of a full native XKB map; no key translation, mutation or input dispatch."""

from __future__ import annotations

import hashlib

from omni.adapters.platform.desktop.linux.linux_bus import failure
from omni.adapters.platform.desktop.linux.linux_x11_wire import unpack


def map_identity(data: bytes, device: int, minimum: int, maximum: int) -> bytes:
    """Compare all eight GetMap components, excluding response identity and documented padding."""
    if (
        not 40 <= len(data) <= 65568
        or data[0] != 1
        or len(data) != 32 + unpack("I", data, 4)[0] * 4
        or data[1] != device
        or tuple(data[10:12]) != (minimum, maximum)
        or unpack("H", data, 12)[0] != 0xFF
        or data[14] != 0
        or not 1 <= data[15] == data[16] <= 255
        or unpack("H", data, 38)[0] != 0xFFFF
    ):
        raise failure("desktop_input_unverified")
    count = maximum - minimum + 1
    if not 8 <= minimum <= maximum <= 255 or any(
        (data[start], data[length]) != (minimum, count)
        for start, length in ((17, 20), (21, 24), (25, 26), (28, 29), (31, 32), (34, 35))
    ):
        raise failure("desktop_input_unverified")
    canonical = bytearray(data[10:37] + data[38:40])
    position = 40

    def take(size: int, meaningful: int | None = None) -> bytes:
        nonlocal position
        if position + size > len(data):
            raise failure("desktop_input_unverified")
        value = data[position : position + size]
        canonical.extend(value if meaningful is None else value[:meaningful])
        position += size
        return value

    levels: list[int] = []
    for _ in range(data[16]):
        description = take(8, 7)
        width, entries, preserve = description[4:7]
        if not 1 <= width <= 255 or preserve not in {0, 1}:
            raise failure("desktop_input_unverified")
        levels.append(width)
        for _ in range(entries):
            entry = take(8, 6)
            if entry[0] not in {0, 1} or entry[2] >= width:
                raise failure("desktop_input_unverified")
        if preserve:
            take(entries * 4)
    symbols = 0
    symbol_counts: list[int] = []
    for _ in range(count):
        description = take(8)
        group_info, width, size = unpack("BBH", description, 4)
        groups = group_info & 0x0F
        if (
            groups > 4
            or group_info & 0xC0 == 0xC0
            or size != groups * width
            or size > 1020
            or any(index >= len(levels) or levels[index] > width for index in description[:groups])
        ):
            raise failure("desktop_input_unverified")
        take(size * 4)
        symbols += size
        symbol_counts.append(size)
    if symbols != unpack("H", data, 18)[0]:
        raise failure("desktop_input_unverified")
    actions = take((count + 3) // 4 * 4, count)[:count]
    if sum(actions) != unpack("H", data, 22)[0] or any(
        action not in {0, symbols} for action, symbols in zip(actions, symbol_counts, strict=True)
    ):
        raise failure("desktop_input_unverified")
    for _ in range(sum(actions)):
        # NoAction's remaining bytes are unused. Other action descriptors are
        # compared intact: an unknown vendor action cannot silently disappear.
        action = data[position : position + 8]
        take(8, 0)
        canonical.extend(action if action[0] else b"\0" * 8)

    def sparse(total: int, stride: int, meaningful: int, *, virtual: bool = False) -> None:
        if total > count:
            raise failure("desktop_input_unverified")
        previous = minimum - 1
        for _ in range(total):
            entry = take(stride, 0)
            if not previous < entry[0] <= maximum:
                raise failure("desktop_input_unverified")
            previous = entry[0]
            canonical.extend(entry[:1] + entry[2:4] if virtual else entry[:meaningful])

    sparse(data[27], 4, 3)
    take(16)  # all 16 real-modifier bindings for virtual modifiers
    sparse(data[30], 2, 2)
    take((-data[30] * 2) % 4, 0)
    sparse(data[33], 2, 2)
    take((-data[33] * 2) % 4, 0)
    sparse(data[36], 4, 3, virtual=True)
    if position != len(data):
        raise failure("desktop_input_unverified")
    return hashlib.sha256(canonical).digest()
