"""Independent framed EI sender peer on isolated pipe FDs; injected state is not compositor/application proof."""

import asyncio
import struct
from contextlib import asynccontextmanager

from v2_eis_device_helpers import DevicePeer, ports
from v2_eis_helpers import CONNECTION, ORDER, frame, word
from v2_xkbcommon_helpers import Library

from omni.adapters.platform.linux_xkbcommon import NativeXkb, XkbCompiler


class KeyPeer(DevicePeer):
    def __init__(self, folder, *, partial=4096):
        super().__init__(folder, requested=1, partial=partial)
        self.armed = self.started = self.emulating = False
        self.stream = bytearray()
        self.keys, self.frames, self.effects = [], [], []
        self.pending = []
        self.logical = set()
        self.boundary = None
        self.write_mode = "block"
        self.write_entered, self.write_release = asyncio.Event(), asyncio.Event()
        self.read_entered, self.read_release = asyncio.Event(), asyncio.Event()
        self.hold_barrier = False
        self.saved_barrier = b""
        self.barrier_fault = None
        self.barrier_hook = None
        self.syncs_after_input = 0

    def send(self, data):
        if not self.armed:
            return super().send(data)
        if (
            not self.started
            and not self.outgoing
            and len(data) >= 16
            and struct.unpack_from(ORDER + "Q", data)[0] == self.device_ids[0]
        ):
            self.started = True
        if self.started and self.boundary is not None and len(self.stream) >= self.boundary:
            self.write_entered.set()
            if not self.write_release.is_set():
                if self.write_mode == "zero":
                    return 0
                if self.write_mode == "invalid":
                    return len(data) + 1
                raise BlockingIOError
            self.boundary = None
        count = min(self.partial, len(data))
        if self.started and self.boundary is not None:
            count = min(count, self.boundary - len(self.stream))
        if self.started:
            self.stream.extend(data[:count])
        self.outgoing.extend(data[:count])
        while len(self.outgoing) >= 16:
            identity, size, opcode = struct.unpack_from(ORDER + "QII", self.outgoing)
            assert 16 <= size <= 4096 and size % 4 == 0
            if len(self.outgoing) < size:
                break
            payload = bytes(self.outgoing[16:size])
            del self.outgoing[:size]
            self.requests.append((identity, opcode, payload))
            self.request(identity, opcode, payload)
        return count

    def request(self, identity, opcode, payload):
        device = self.device_ids[0]
        keyboard = self.child_ids[(device, "ei_keyboard")]
        if identity == CONNECTION:
            assert opcode == 0 and len(payload) == 12
            callback, version = struct.unpack(ORDER + "QI", payload)
            assert version == 1
            if not self.started:
                self.emit(callback, 0, bytes(8))
                return
            self.syncs_after_input += 1
            if self.barrier_hook:
                self.barrier_hook(self)
            body = word(1) + bytes(4) if self.barrier_fault == "value" else bytes(8)
            if self.barrier_fault == "object":
                callback += 100
            if self.barrier_fault == "disconnect":
                value = frame(CONNECTION, 0, word(1) + word(1))
            else:
                value = frame(callback, 0, body)
            if self.hold_barrier:
                # Preserve an actual partial header/body at the peer. The
                # original reader must resume it, never start a second parser.
                self.incoming.extend(value[:19])
                self.saved_barrier = value[19:]
            else:
                self.incoming.extend(value)
        elif identity == device:
            if opcode == 1:
                serial, sequence = struct.unpack(ORDER + "II", payload)
                assert serial == self.serial and sequence == 1 and not self.emulating
                self.emulating = True
            elif opcode == 3:
                serial, stamp = struct.unpack(ORDER + "IQ", payload)
                assert self.emulating and serial == self.serial and stamp == 123456789
                self.frames.append(tuple(self.pending))
                for code, state in self.pending:
                    (self.logical.add if state else self.logical.discard)(code)
                    self.effects.append((code, state))
                self.pending.clear()
            else:
                assert opcode == 2 and payload == word(self.serial) and self.emulating and not self.pending
                self.emulating = False
        else:
            assert identity == keyboard and opcode == 1 and self.emulating
            code, state = struct.unpack(ORDER + "II", payload)
            assert code == 159 and state in {0, 1}
            assert not any(before[0] == code for before in self.pending), "one transition per key per frame"
            self.pending.append((code, state))
            self.keys.append((code, state))

    async def wait(self, write):
        if write:
            self.write_entered.set()
            await self.write_release.wait()
        else:
            self.read_entered.set()
            await self.read_release.wait()

    def finish_barrier(self):
        self.hold_barrier = False
        self.incoming.extend(self.saved_barrier)
        self.saved_barrier = b""
        self.read_release.set()


async def guard():
    pass


@asynccontextmanager
async def prepared(monkeypatch, folder, *, partial=4096):
    ports(monkeypatch)
    peer = KeyPeer(folder, partial=partial)
    wire = peer.connection(guard)
    try:
        await wire.prepare()
        inventory = await wire.bind(wire.seats()[0].identity)
        plan = await wire.prepare_key(
            inventory.devices[0].identity,
            "Enter",
            compiler_factory=lambda: XkbCompiler(lambda: NativeXkb(loader=lambda name: Library(code=167))),
        )
        packet = await wire.key_packet(clock=lambda: 123456789)
        assert packet.plan is plan
        peer.armed = True
        yield peer, wire, packet
    finally:
        # Some negative tests deliberately retain an uncertain release. These
        # are fake sockets on pipes, with no native input. Dispose only the
        # isolated fixture after assertions, without manufacturing a receipt.
        peer.write_release.set()
        peer.finish_barrier()
        if wire._input_lease is not None:
            owner = wire._input_lease
            if owner._writer is not None:
                await asyncio.gather(owner._writer, return_exceptions=True)
            await owner.settle()
            if owner._query is not None and not owner._query.done():
                owner._query.cancel()
            if owner._query is not None:
                await asyncio.gather(owner._query, return_exceptions=True)
        if not await wire.close():
            if wire._keyboard is not None:
                await wire._keyboard.close()
            for keymap in wire._keymaps:
                await keymap.close()
            wire._close_handles()
        peer.release_files()
