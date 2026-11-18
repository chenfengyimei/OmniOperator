"""Independent XKB native reply layouts; geometry covers every standard doodad and nested record."""

import struct


def reply(sequence, device, fields, payload, padding=0):
    header = bytearray(32)
    header[0:2] = bytes([1, device])
    struct.pack_into("<HI", header, 2, sequence, len(payload) // 4)
    header[8:32] = bytes([padding]) * 24
    for offset, value in fields.items():
        header[offset : offset + len(value)] = value
    return bytes(header) + payload


def counted(value, padding):
    return struct.pack("<H", len(value)) + value + bytes([padding]) * (-(len(value) + 2) % 4)


def decoration(kind, padding):
    common = struct.pack("<IBBhhh", 800 + kind, kind, 0, 0, 0, 0)
    if kind in {1, 2, 5}:
        tail = bytes([1, 0]) + bytes([padding]) * 6
        extra = counted(b"OMNI", padding) if kind == 5 else b""
    elif kind == 3:
        tail = struct.pack("<HHB", 20, 10, 1) + bytes([padding]) * 3
        extra = counted(b"Enter", padding) + counted(b"Sans", padding)
    else:
        assert kind == 4
        tail, extra = bytes([0, 0, 1]) + bytes([padding]) * 5, b""
    return common + tail + extra


def copied_reply(minor, sequence, device, *, padding=0, found=True):
    if minor == 10:
        fields = {8: bytes([15]), 10: struct.pack("<HHH", 0, 1, 1)}
        si = struct.pack("<IBBBB", 0xFF0D, 0, 0, 255, 0) + bytes([0]) + bytes([padding]) * 7
        return reply(sequence, device, fields, si + bytes(16), padding)
    if minor == 13:
        return reply(sequence, device, {8: struct.pack("<IIB", 0xFFFFFFFF, 7, 32)}, bytes(32 * 12), padding)
    if minor == 17:
        fields = {
            8: struct.pack("<I", 0x3FFF),
            12: bytes([8, 255, 4, 15]),
            16: struct.pack("<HBBI", 3, 8, 248, 3),
            24: struct.pack("<BBH", 1, 1, 7),
        }
        atoms = struct.pack("<6I", *range(101, 107)) + struct.pack("<4I", *range(201, 205))
        atoms += bytes([1, 2, 2, 2]) + struct.pack("<7I", *range(301, 308))
        atoms += struct.pack("<8I", *range(401, 409))  # 2 indicator, 2 virtual, 4 group names
        keys = b"".join(f"{code:04X}".encode() for code in range(8, 256))
        return reply(sequence, device, fields, atoms + keys + b"ENTRRTRN" + struct.pack("<I", 410), padding)
    assert minor == 19
    if not found:
        return reply(sequence, device, {8: bytes(5), 14: bytes(18)}, b"", padding)
    fields = {8: struct.pack("<IB", 500, 1), 14: struct.pack("<8HBB", 450, 150, 1, 2, 1, 1, 5, 1, 0, 1)}
    body = counted(b"Sans", padding)
    body += counted(b"vendor", padding) + counted(b"fixture", padding)
    body += counted(b"#000", padding) + counted(b"#fff", padding)
    body += struct.pack("<IBBBB", 600, 1, 0, 255, padding)
    body += bytes([2, 1, padding, padding]) + struct.pack("<hhhh", 0, 0, 10, 10)
    body += struct.pack("<IhhHHhBBBBH", 700, 1, 2, 40, 30, 0, 0, 1, 1, 1, padding * 257)
    body += struct.pack("<hhBBH", 0, 0, 1, 0, padding * 257) + b"RTRN" + struct.pack("<hBB", 0, 0, 1)
    body += decoration(3, padding)
    body += struct.pack("<IBBBB", 701, 1, padding, padding, padding)
    body += bytes([0, 1, padding, padding]) + b"KPENRTRN"
    body += b"".join(decoration(kind, padding) for kind in range(1, 6))
    body += b"ENTRRTRN"
    return reply(sequence, device, fields, body, padding)
