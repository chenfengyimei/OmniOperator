"""Independent EI seat/device server fixtures with batched real temp-file FDs; no compositor or input."""

import array
import os
import socket
import struct
import tempfile

from v2_eis_helpers import CONNECTION, ORDER, Peer, frame, injected_fds, string, word

from omni.adapters.platform.linux_eis_devices import EisDevices

MASKS = {
    "ei_keyboard": 1 << 47,
    "ei_pointer": 1 << 5,
    "ei_pointer_absolute": 1 << 11,
    "ei_button": 1 << 60,
    "ei_scroll": 1 << 33,
}
KEYMAP = b"xkb_keymap { /* fixture bytes, not a parsed layout */ };\0"


def ports(monkeypatch):
    injected_fds(monkeypatch)
    monkeypatch.setattr(socket, "SCM_RIGHTS", 1, raising=False)
    if not hasattr(os, "pread"):
        # Windows-only test shim. Native Linux runtime uses os.pread; this
        # emulates that port on isolated files, without claiming Linux proof.
        def pread(descriptor, size, offset):
            before = os.lseek(descriptor, 0, os.SEEK_CUR)
            try:
                os.lseek(descriptor, offset, os.SEEK_SET)
                return os.read(descriptor, size)
            finally:
                os.lseek(descriptor, before, os.SEEK_SET)

        monkeypatch.setattr(os, "pread", pread, raising=False)


class DevicePeer(Peer):
    def __init__(
        self,
        folder,
        *,
        requested=3,
        fault=None,
        split=False,
        partial=4096,
        seats=1,
        keyboards=1,
        device_version=2,
        regions=1,
    ):
        super().__init__(partial=partial)
        self.folder, self.requested, self.fault = folder, requested, fault
        self.split, self.seat_count, self.keyboards, self.device_version = split, seats, keyboards, device_version
        self.region_count = regions
        self.next_id = CONNECTION
        self.offers, self.seat_ids, self.device_ids, self.child_ids, self.bindings = {}, [], [], {}, []
        self.pending_rights, self.delivered, self.files = [], [], []
        self.serial = 0xFFFFFFFF

    def allocate(self):
        self.next_id += 1
        return self.next_id

    def emit(self, identity, opcode, payload=b""):
        self.incoming.extend(frame(identity, opcode, payload))

    def send(self, data):
        count = min(self.partial, len(data))
        self.outgoing.extend(data[:count])
        while len(self.outgoing) >= 16:
            identity, size, opcode = struct.unpack_from(ORDER + "QII", self.outgoing)
            if len(self.outgoing) < size:
                break
            payload = bytes(self.outgoing[16:size])
            del self.outgoing[:size]
            self.requests.append((identity, opcode, payload))
            if identity == 0:
                assert opcode in {0, 1, 2, 3, 4}
                if opcode == 4:
                    length = struct.unpack_from(ORDER + "I", payload)[0]
                    self.offers[payload[4 : 4 + length - 1].decode()] = struct.unpack_from(
                        ORDER + "I", payload, len(payload) - 4
                    )[0]
                elif opcode == 1:
                    self.finish()
            elif identity == CONNECTION:
                assert opcode == 0 and len(payload) == 12
                callback, version = struct.unpack(ORDER + "QI", payload)
                assert version == 1
                self.emit(callback, 0, bytes(8))
            elif identity in self.seat_ids:
                assert opcode == 1 and len(payload) == 8
                mask = struct.unpack(ORDER + "Q", payload)[0]
                expected = 0
                for key in MASKS:
                    if key in self.offers:
                        expected |= MASKS[key]
                assert mask == expected, "Use actual sparse seat masks, never assumed keyboard/pointer bits"
                self.bindings.append((identity, mask))
                self.devices(identity)
            else:
                raise AssertionError("Inventory must never request device emulation, key, button, motion or frame")
        return count

    def recvmsg(self, length, ancillary_size, flags):
        data, ancillary, status, address = super().recvmsg(length, ancillary_size, flags)
        if self.pending_rights:
            descriptors = self.pending_rights[:]
            self.pending_rights.clear()
            self.delivered.extend(descriptors)
            ancillary = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", descriptors).tobytes())]
        return data, ancillary, status, address

    def finish(self):
        self.emit(0, 2, word(self.serial) + struct.pack(ORDER + "Q", CONNECTION) + word(1))
        for index in range(self.seat_count):
            seat = self.allocate()
            self.seat_ids.append(seat)
            self.emit(CONNECTION, 1, struct.pack(ORDER + "QI", seat, 1))
            self.emit(seat, 1, string("EI seat " + str(index)))
            for key, mask in MASKS.items():
                if key in self.offers:
                    actual = 0 if self.fault == "zero_mask" else mask
                    if self.fault == "overlap" and key == "ei_pointer":
                        actual = MASKS["ei_keyboard"]
                    self.emit(seat, 2, struct.pack(ORDER + "Q", actual) + string(key))
                    if self.fault == "duplicate_capability":
                        self.emit(seat, 2, struct.pack(ORDER + "Q", actual) + string(key))
            self.emit(seat, 3)
            if self.fault == "repeat_seat_done":
                self.emit(seat, 3)

    def keymap_fd(self, data=KEYMAP):
        value = tempfile.TemporaryFile(dir=self.folder)
        value.write(data)
        value.flush()
        value.seek(7)
        self.files.append(value)
        descriptor = os.dup(value.fileno())
        os.set_inheritable(descriptor, True)
        self.pending_rights.append(descriptor)
        return descriptor

    def devices(self, seat):
        keyboard = ["ei_keyboard"] if "ei_keyboard" in self.offers else []
        pointer = [key for key in MASKS if key != "ei_keyboard" and key in self.offers]
        groups = (
            ([keyboard] * self.keyboards + ([pointer] if pointer else []))
            if self.split or self.keyboards > 1
            else [keyboard + pointer]
        )
        for group in groups:
            if not group:
                continue
            device = self.allocate()
            self.device_ids.append(device)
            version = 3 if self.fault == "device_version" else self.device_version
            self.emit(seat, 4, struct.pack(ORDER + "QI", device, version))
            self.emit(device, 1, string("virtual fixture"))
            if self.fault != "missing_type":
                self.emit(device, 2, word(2 if self.fault == "physical" else 1))
            if "ei_pointer_absolute" in group and self.fault != "missing_region":
                for index in range(self.region_count):
                    if version >= 2:
                        self.emit(device, 12, string("mapping-display-" + str(index + 1)))
                    self.emit(
                        device,
                        4,
                        struct.pack(
                            ORDER + "IIIIf",
                            1920 + index * 1920,
                            0,
                            1920,
                            1080,
                            float("nan") if self.fault == "scale_nan" else 2.0,
                        ),
                    )
            for key in group:
                child = self.allocate()
                self.child_ids[(device, key)] = child
                self.emit(device, 5, struct.pack(ORDER + "Q", child) + string(key) + word(1))
                if self.fault == "duplicate_interface":
                    self.emit(device, 5, struct.pack(ORDER + "Q", self.allocate()) + string(key) + word(1))
                if key == "ei_keyboard" and self.fault != "no_keymap":
                    data = b"bad\xff\0" if self.fault == "keymap_utf8" else KEYMAP
                    if self.keyboards > 1:
                        data = ("distinct-keymap-" + str(len(self.files))).encode() + b"\0"
                    if self.fault != "missing_fd":
                        self.keymap_fd(data)
                    size = 1048577 if self.fault == "keymap_size" else len(data)
                    self.emit(child, 1, word(1) + word(size))
            self.emit(device, 6)
            if self.fault == "repeat_device_done":
                self.emit(device, 6)
            self.serial = (self.serial + 1) % (1 << 32)
            if self.fault != "initial_paused":
                self.emit(device, 7, word(self.serial))

    def connection(self, guard):
        wire = EisDevices(self.owned, guard, self.requested, socket_factory=lambda descriptor: self)
        wire._wait = self.wait
        return wire

    def release_files(self):
        for value in self.files:
            value.close()
