"""In-memory X11 server protocol and logind fixtures; no socket, display or credentials accessed."""

import struct

from v2_linux_desktop_helpers import SESSION, Factory, variant

from omni.adapters.platform.desktop.linux.linux_bus import Process, failure
from omni.adapters.platform.desktop.linux.linux_x11 import X11Desktop
from omni.adapters.platform.desktop.linux.linux_x11_socket import Connection
from omni.adapters.platform.desktop.linux.linux_x11_wire import Wire

ROOT, WINDOW, VISUAL, CLIENT = 0x100, 0x400001, 0x21, 0x400000
COOKIE = b"PRIVATE-X-COOKIE"


def setup_bytes(order=0, bits=32):
    data = bytearray(32)
    struct.pack_into("<III", data, 0, 123, 0x2000000, 0x1FFFFF)
    struct.pack_into("<HHBBBB", data, 16, 0, 4096, 1, 1, order, 0)
    data[26:28] = bytes([8, 255])
    data += bytes([24, bits, 32, 0, 0, 0, 0, 0])
    screen = bytearray(40)
    struct.pack_into("<I", screen, 0, ROOT)
    struct.pack_into("<HH", screen, 20, 1920, 1080)
    struct.pack_into("<I", screen, 32, VISUAL)
    screen[38:40] = bytes([24, 1])
    data += screen
    data += struct.pack("<BxHxxxx", 24, 1)
    data += struct.pack("<IBBHIIIxxxx", VISUAL, 4, 8, 256, 0xFF0000, 0xFF00, 0xFF)
    return bytes(data)


def reply(sequence, *, detail=0, extra=b"", fields=b""):
    header = bytearray(32)
    struct.pack_into("<BBHI", header, 0, 1, detail, sequence, len(extra) // 4)
    header[8 : 8 + len(fields)] = fields
    return bytes(header) + extra


class Peer:
    """Emulates documented X server wire layouts, including asynchronous void requests."""

    def __init__(self, fault=None, order=0, bits=32):
        self.fault, self.order, self.bits = fault, order, bits
        self.incoming, self.sent = bytearray(), []
        self.authenticated, self.sequence = False, 0
        self.pixmap = None
        self.image_read = False
        self.width = 2

    async def sock_sendall(self, connection, packet):
        if not self.authenticated:
            assert connection.checked and COOKIE in packet and packet[0] == ord("l")
            self.authenticated = True
            body = setup_bytes(self.order, self.bits)
            self.incoming.extend(struct.pack("<BBHHH", 1, 0, 11, 0, len(body) // 4) + body)
            return
        opcode, minor, length = struct.unpack_from("<BBH", packet)
        assert length * 4 == len(packet)
        self.sequence += 1
        body, extra, fields, detail = packet[4:], b"", b"", 0
        self.sent.append((opcode, minor, body))
        if opcode == 98:
            name = body[4 : 4 + struct.unpack_from("<H", body)[0]]
            fields = bytes(
                [
                    0 if self.fault == "composite_missing" and name == b"Composite" else 1,
                    128 if name == b"X-Resource" else 129,
                    0,
                    0,
                ]
            )
        elif opcode == 128:
            if minor == 0:
                fields = struct.pack("<HH", 1, 2)
            elif minor == 1:
                fields = struct.pack("<I", 1)
                extra = struct.pack("<II", CLIENT, 0x1FFFFF)
            elif minor == 4:
                assert struct.unpack("<III", body) == (1, CLIENT, 2)
                fields = struct.pack("<I", 0 if self.fault == "remote_pid" else 1)
                extra = struct.pack("<IIII", CLIENT, 2, 4, 20) if self.fault != "remote_pid" else b""
            else:
                raise AssertionError(minor)
        elif opcode == 129:
            if minor == 0:
                fields = struct.pack("<II", 0, 4)
            else:
                assert minor == 6
                selected, self.pixmap = struct.unpack("<II", body)
                assert selected == WINDOW and selected != ROOT
                if self.fault == "not_redirected":
                    error = bytearray(32)
                    error[1] = 8
                    struct.pack_into("<H", error, 2, self.sequence)
                    self.incoming.extend(error)
                return
        elif opcode == 16:
            assert minor == 1 and b"_NET_CLIENT_LIST" in body
            fields = struct.pack("<I", 50)
        elif opcode == 20:
            assert struct.unpack("<IIIII", body) == (ROOT, 50, 33, 0, 128) and minor == 0
            detail = 32
            fields = struct.pack("<III", 33, 0, 1)
            extra = struct.pack("<I", ROOT if self.fault == "root" else WINDOW)
        elif opcode == 3:
            assert struct.unpack("<I", body)[0] == WINDOW
            header = bytearray(44)
            struct.pack_into("<BBHI", header, 0, 1, 0, self.sequence, 3)
            struct.pack_into("<IH", header, 8, VISUAL, 1)
            header[26] = 0 if self.fault == "unmapped" else 2
            self.incoming.extend(header)
            return
        elif opcode == 14:
            resource = struct.unpack("<I", body)[0]
            assert resource in {WINDOW, self.pixmap}
            width = self.width + (1 if self.image_read and self.fault == "resize" else 0)
            detail = 24
            fields = struct.pack("<IhhHHH", ROOT, 0, 0, width, 2, 0)
            if self.image_read and self.fault == "destroy":
                event = bytearray(32)
                event[0] = 17
                struct.pack_into("<II", event, 4, WINDOW, WINDOW)
                self.incoming.extend(event)
        elif opcode == 2:
            assert struct.unpack("<III", body) == (WINDOW, 1 << 11, 1 << 17)
            return
        elif opcode == 73:
            assert struct.unpack("<IhhHHI", body) == (self.pixmap, 0, 0, 2, 2, 0xFFFFFFFF)
            self.image_read = True
            pixels = [(0, 0, 255), (0, 255, 0), (255, 0, 0), (10, 20, 30)]
            rows = []
            for index in range(0, 4, 2):
                row = b""
                for blue, green, red in pixels[index : index + 2]:
                    if self.fault == "blank":
                        blue = green = red = 0
                    row += (
                        bytes((blue, green, red, 0))
                        if self.bits == 32 and self.order == 0
                        else bytes((0, red, green, blue))
                        if self.bits == 32
                        else bytes((blue, green, red))
                        if self.order == 0
                        else bytes((red, green, blue))
                    )
                row += b"\0" * (-len(row) % 4)
                rows.append(row)
            extra = b"".join(rows)
            detail = 24
        elif opcode == 54:
            assert struct.unpack("<I", body)[0] == self.pixmap
            self.pixmap = None
            return
        else:
            raise AssertionError((opcode, minor, body))
        result = reply(self.sequence, detail=detail, fields=fields, extra=extra)
        if self.fault == "length" and opcode == 73:
            result = bytearray(result[:32])
            struct.pack_into("<I", result, 4, 0xFFFFFFFF)
        if self.fault == "sequence" and opcode == 20:
            result = bytearray(result)
            struct.pack_into("<H", result, 2, self.sequence + 1)
        self.incoming.extend(result)

    async def sock_recv(self, connection, size):
        assert not connection.closed
        size = min(size, 7)  # exercise partial reads and protocol framing
        data = bytes(self.incoming[:size])
        del self.incoming[:size]
        return data


class OwnedConnection:
    def __init__(self, inspector):
        self.inspector = inspector
        self.process = inspector.ops.process(30, executable=True)
        self.inode = (5, 9)
        self.screen = self.display = 0
        self.socket = self
        self.checked = self.closed = False

    def check(self):
        assert not self.closed
        if self.inspector.ops.process(30, executable=True) != self.process:
            raise failure("desktop_x11_peer_unverified")
        self.checked = True

    def cookie(self):
        self.check()
        return COOKIE

    async def verify(self):
        self.check()
        await self.inspector.check()
        await Connection.server_session(self.inspector, self.process)

    async def close(self):
        self.closed = True
        return True


def linux_factory():
    f = Factory()
    f.session.update(Type=variant("s", "x11"), Display=variant("s", ":0"))
    f.ops.processes[30] = Process(30, 0, 130.0, "/usr/lib/xorg/Xorg")
    f.sessions[30] = SESSION
    return f


def desktop_factory(peer, *, publish_file=None, opened=None):
    async def connect(inspector):
        connection = OwnedConnection(inspector)
        await connection.verify()
        if opened is not None:
            opened.append(connection)
        return connection

    def wire(connection, check):
        protocol = Wire(connection, check)
        protocol.loop = peer
        return protocol

    def factory(inspector):
        kwargs = {} if publish_file is None else {"publish_file": publish_file}
        return X11Desktop(inspector, connection_factory=connect, wire_factory=wire, **kwargs)

    return factory
