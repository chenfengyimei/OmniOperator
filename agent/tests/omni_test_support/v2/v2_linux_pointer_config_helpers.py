"""Independent XI2 axes/scroll and XI1 feedback/property receipts, composed with actual point/map/XKB readers."""

import struct

from omni_test_support.v2.v2_linux_button_helpers import button_topology
from omni_test_support.v2.v2_linux_pointer_action_helpers import PointerActionPeer, action_context
from omni_test_support.v2.v2_x11_helpers import reply

MATRIX = b"Coordinate Transformation Matrix"
IDENTITY = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


class ConfigurationPeer(PointerActionPeer):
    def __init__(self):
        super().__init__()
        self.axes = {
            identity: [
                dict(number=number, source=identity, label=200 + number, low=(0, 0), high=(-1, 0), resolution=0, mode=0)
                for number in (0, 1)
            ]
            for identity in (2, 4)
        }
        self.labels = {2: (90, 91, 92), 4: (90, 91, 92)}
        self.axis_padding, self.axis_value = 0, (0, 0)
        self.scroll = False
        self.scroll_axis, self.scroll_increment = 2, (1, 0)
        self.feedbacks = {2: [(0, 2, 1, 4)], 4: [(0, 2, 1, 4)]}
        self.matrices = {2: IDENTITY, 4: IDENTITY}
        self.config_atoms = {MATRIX: 400, b"FLOAT": 401}
        self.config_fault = None
        self.config_reads = []
        self.on_config_read = None
        self.refresh_topology()

    def refresh_topology(self):
        original = button_topology()
        result, position = bytearray(original[:32]), 32
        for _ in range(struct.unpack_from("<H", original, 8)[0]):
            identity, _kind, _attachment, count, size = struct.unpack_from("<HHHHH", original, position)
            end = position + 12 + (size + 3) // 4 * 4
            header = bytearray(original[position:end])
            position = end
            classes = []
            for _ in range(count):
                words = struct.unpack_from("<H", original, position + 2)[0]
                classes.append(original[position : position + words * 4])
                position += words * 4
            if identity in self.axes:
                classes[0] = classes[0][:-12] + struct.pack("<III", *self.labels[identity])
                for axis in self.axes[identity]:
                    classes.append(
                        struct.pack("<HHHHI", 2, 11, axis["source"], axis["number"], axis["label"])
                        + struct.pack(
                            "<iIiIiIIB", *axis["low"], *axis["high"], *self.axis_value, axis["resolution"], axis["mode"]
                        )
                        + bytes([self.axis_padding]) * 3
                    )
                if self.scroll:
                    classes.append(
                        struct.pack("<HHHHH", 3, 6, identity, self.scroll_axis, 1)
                        + bytes([self.axis_padding]) * 2
                        + struct.pack("<IiI", 2, *self.scroll_increment)
                    )
                struct.pack_into("<H", header, 6, len(classes))
            result += header + b"".join(classes)
        assert position == len(original)
        struct.pack_into("<I", result, 4, (len(result) - 32) // 4)
        self.topology = bytes(result)

    async def sock_sendall(self, connection, packet):
        if not self.authenticated:
            return await super().sock_sendall(connection, packet)
        opcode, minor, words = struct.unpack_from("<BBH", packet)
        body = packet[4:]
        name = body[4 : 4 + struct.unpack_from("<H", body)[0]] if opcode == 16 else None
        property_atom = struct.unpack_from("<I", body, 4)[0] if opcode == 130 and minor == 59 else None
        if not (
            opcode == 16
            and name in self.config_atoms
            or opcode == 130
            and minor == 22
            or property_atom == self.config_atoms[MATRIX]
        ):
            return await super().sock_sendall(connection, packet)
        self.sequence += 1
        self.sent.append((opcode, minor, body))
        assert words * 4 == len(packet)
        if opcode == 16:
            assert minor == 1
            result = reply(self.sequence, fields=struct.pack("<I", self.config_atoms[name]))
        else:
            identity = body[0] if minor == 22 else struct.unpack_from("<H", body)[0]
            category = "feedback" if minor == 22 else "transform"
            self.config_reads.append((identity, category))
            if minor == 22:
                assert body == struct.pack("<Bxxx", identity)
                values = self.feedbacks[identity]
                payload = b"".join(
                    struct.pack(
                        "<BBHBBHHH", 1, key, 12, self.axis_padding, self.axis_padding, numerator, denominator, threshold
                    )
                    for key, numerator, denominator, threshold in values
                )
                result = bytearray(
                    reply(self.sequence, detail=22, fields=struct.pack("<H", len(values)), extra=payload)
                )
                if self.config_fault == "feedback_class":
                    result[32] = 0
                elif self.config_fault == "feedback_size":
                    result[34] = 8
                elif self.config_fault == "feedback_trailing":
                    result += b"four"
                    struct.pack_into("<I", result, 4, (len(result) - 32) // 4)
            else:
                assert struct.unpack("<HBxIIII", body) == (identity, 0, self.config_atoms[MATRIX], 0, 0, 9)
                result = bytearray(
                    reply(
                        self.sequence,
                        fields=struct.pack("<IIIB", self.config_atoms[b"FLOAT"], 0, 9, 32),
                        extra=struct.pack("<9f", *self.matrices[identity]),
                    )
                )
                if self.config_fault == "property_type":
                    struct.pack_into("<I", result, 8, 19)
                elif self.config_fault == "property_remaining":
                    result[12] = 4
                elif self.config_fault == "property_count":
                    result[16] = 8
                elif self.config_fault == "property_format":
                    result[20] = 8
            if self.on_config_read is not None:
                await self.on_config_read(identity, category)
        if self.event is not None:
            self.incoming.extend(self.event)
            self.event = None
        self.incoming.extend(result)


async def configuration_context(peer=None):
    context = await action_context(peer or ConfigurationPeer())
    await context[-1].prepare_pointer_actions()
    return context


def property_event(device, sequence):
    event = bytearray(32)
    struct.pack_into("<BBH", event, 0, 35, 130, sequence)
    struct.pack_into("<HHIIB", event, 8, 12, device, 1000, 400, 1)
    return bytes(event)
