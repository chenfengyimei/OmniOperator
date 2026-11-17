"""Independent complete XKB GetMap encoder with variable padding and all eight components."""

import struct


def complete_map(sequence, device, rows=None, *, minimum=8, maximum=255, padding=0):
    rows = {40: (0xFF0D,)} if rows is None else rows
    count = maximum - minimum + 1
    data = bytearray(40)
    data[0:2] = bytes([1, device])
    struct.pack_into("<H", data, 2, sequence)
    data[8:10], data[37] = bytes([padding]) * 2, padding
    data[10:12] = bytes([minimum, maximum])
    struct.pack_into("<H", data, 12, 255)
    data[14:17] = bytes([0, 4, 4])
    for start, length in ((17, 20), (21, 24), (25, 26), (28, 29), (31, 32), (34, 35)):
        data[start], data[length] = minimum, count
    data[27], data[30], data[33], data[36] = 1, 1, 1, 1
    struct.pack_into("<H", data, 38, 65535)
    types = (
        struct.pack("<BBHBBBB", 0, 0, 0, 1, 0, 0, padding)
        + struct.pack("<BBHBBBB", 1, 1, 0, 2, 1, 1, padding)
        + struct.pack("<BBBBHBB", 1, 1, 1, 1, 0, padding, padding)
        + struct.pack("<BBH", 0, 0, 0)
        + struct.pack("<BBHBBBB", 1, 1, 0, 2, 0, 0, padding) * 2
    )
    symbols = bytearray()
    total = 0
    for code in range(minimum, maximum + 1):
        value = rows.get(code, (0,))[0]
        if value:
            symbols += struct.pack("<BBBBBBH", 0, 0, 0, 0, 1, 1, 1) + struct.pack("<I", value)
            total += 1
        else:
            symbols += b"\0" * 8
    struct.pack_into("<H", data, 18, total)
    # The single explicit NoAction row exercises both its ignored bytes and
    # count-array alignment. Other noncandidate descriptors remain meaningful.
    actions = bytearray(count)
    if 40 in range(minimum, maximum + 1):
        actions[40 - minimum] = 1
        struct.pack_into("<H", data, 22, 1)
        action_data = bytes([0]) + bytes([padding]) * 7
    else:
        action_data = b""
    data += types + symbols + actions + bytes([padding]) * (-count % 4) + action_data
    data += bytes([100, 1, 0, padding])  # one noncandidate lock behavior
    data += bytes(16)  # full virtual-modifier bindings
    data += bytes([41, 1, padding, padding])  # explicit entry then alignment
    data += bytes([60, 1, padding, padding])  # real modifier entry then alignment
    data += bytes([61, padding, 2, 0])  # virtual modifier entry
    struct.pack_into("<I", data, 4, (len(data) - 32) // 4)
    return bytes(data)
