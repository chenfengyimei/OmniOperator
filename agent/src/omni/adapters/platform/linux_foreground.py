"""Read-only X11 focus ancestry proof for future explicit foreground delivery; never maps AT-SPI nodes or sends input."""

from __future__ import annotations

import struct
from typing import cast

from .linux_bus import failure
from .linux_button_mapping import ButtonMapping, ButtonPlan
from .linux_input import X11Input
from .linux_keyboard import KeyboardMapping
from .linux_keystroke import KeyPacket
from .linux_pointer_actions import PointerActions
from .linux_pointer_target import PointerPoint, PointerTarget
from .linux_window_leases import WindowLeases
from .linux_x11 import X11Desktop
from .linux_x11_wire import unpack
from .linux_xtest import Sources, XTestSources

Payload = dict[str, object]
MAX_LINEAGE = 16
MAX_CHILDREN = 4096


class WindowFocus:
    """Own one input-change monitor while its selected continuous window lease remains external."""

    def __init__(self, leases: WindowLeases, monitor: X11Input) -> None:
        self.leases, self.monitor = leases, monitor
        self._attempted = False
        self._target: Payload | None = None
        self._original: Payload | None = None
        self._lineage: tuple[int, ...] | None = None
        self._resource: int | None = None
        self._closed = False
        self._ready = False
        self._keyboard: KeyboardMapping | None = None
        self._sources: XTestSources | None = None
        self._pointer: PointerTarget | None = None
        self._buttons: ButtonMapping | None = None
        self._pointer_actions: PointerActions | None = None

    def _same_server(self) -> None:
        desktop = self.leases.desktop
        source, observed = desktop.connection, self.monitor.connection
        source_wire, observed_wire = desktop.wire, self.monitor.wire
        if (
            self.monitor.inspector is not desktop.inspector
            or source is None
            or observed is None
            or source_wire is None
            or observed_wire is None
            or source_wire.setup is None
            or observed_wire.setup is None
            or source_wire.setup.root != observed_wire.setup.root
            or any(
                getattr(source, field) != getattr(observed, field)
                for field in ("process", "inode", "screen", "display")
            )
        ):
            raise failure("desktop_target_changed")

    async def _read_lineage(self) -> tuple[int, ...]:
        assert self._original is not None and self._resource is not None
        wire = self.monitor.wire
        assert wire is not None and wire.setup is not None
        window = wire.focus
        if window is None or window < 2 or window == wire.setup.root:
            raise failure("desktop_input_unverified")
        # XRes describes the actual receiving client; a self-reported PID or
        # a window-manager focus proxy cannot stand in for target ownership.
        _client, pid = await wire.client(self._resource, window)
        process = self.leases.desktop.inspector.ops.process(pid, executable=True)
        if (
            pid != self._original["pid"]
            or process.created != self._original["process_created"]
            or process.executable != self._original["application_id"]
            or process.uid != self.leases.desktop.inspector.ops.uid
        ):
            raise failure("desktop_target_changed")
        selected = X11Desktop.window(self._original)
        lineage: list[int] = []
        for _ in range(MAX_LINEAGE):
            if window < 2 or window == wire.setup.root or window in lineage:
                raise failure("desktop_target_changed")
            lineage.append(window)
            if window == selected:
                return tuple(lineage)
            reply = await wire.request(15, struct.pack("<I", window), maximum=MAX_CHILDREN * 4)
            root, parent, children = unpack("IIH", reply, 8)
            if (
                len(reply) != 32 + children * 4
                or children > MAX_CHILDREN
                or root != wire.setup.root
                or parent == window
            ):
                raise failure("desktop_x11_protocol_invalid")
            window = cast(int, parent)
        raise failure("desktop_input_unverified")

    async def prepare(self, target: Payload) -> None:
        if self._attempted or self._closed:
            raise failure("desktop_input_unverified")
        self._attempted = True
        self._original = await self.leases.verify(target)
        self._target = dict(target)
        if self.monitor.inspector is not self.leases.desktop.inspector:
            raise failure("desktop_target_changed")
        await self.monitor.open()
        self._same_server()
        await self.monitor.arm()
        wire = self.monitor.wire
        assert wire is not None
        self._resource = await wire.extension(b"X-Resource")
        version = await wire.request(self._resource, struct.pack("<BBxx", 1, 2), 0)
        if len(version) != 32 or unpack("HH", version, 8) != (1, 2):
            raise failure("desktop_x11_extension_unavailable")
        self._lineage = await self._read_lineage()
        await self.monitor.watch_lineage(self._lineage)
        await self._validate()
        self._ready = True

    async def _validate(self) -> None:
        if self._closed or self._target is None or self._original is None or self._lineage is None:
            raise failure("desktop_input_unverified")
        if await self.leases.verify(self._target) != self._original:
            raise failure("desktop_target_changed")
        await self.monitor.check()
        self._same_server()
        if await self._read_lineage() != self._lineage:
            raise failure("desktop_target_changed")
        await self.monitor.check()

    async def check(self) -> None:
        if not self._ready:
            raise failure("desktop_input_unverified")
        try:
            await self._validate()
        except BaseException:
            self._ready = False
            raise

    async def close(self) -> bool:
        self._closed = True
        return await self.monitor.close()

    @property
    def selected_window(self) -> int:
        if not self._ready or self._closed or self._original is None:
            raise failure("desktop_input_unverified")
        return X11Desktop.window(self._original)

    async def prepare_pointer(self, point: tuple[int, int]) -> PointerPoint:
        """Resolve physical window coordinates to a native recipient; this is not an input permit."""
        if self._pointer is not None or self._original is None or self._resource is None:
            raise failure("desktop_input_unverified")
        try:
            self._pointer = PointerTarget(self, self._original, self._resource, point)
            return await self._pointer.prepare()
        except BaseException:
            self._ready = False
            raise

    async def verify_pointer(self) -> PointerPoint:
        if self._pointer is None:
            raise failure("desktop_input_unverified")
        try:
            return await self._pointer.verify()
        except BaseException:
            self._ready = False
            raise

    async def prepare_key(self, key: object) -> int:
        """Read one key's mapping while the native window/focus proof remains held; no input permission."""
        if self._keyboard is not None:
            raise failure("desktop_input_unverified")
        self._keyboard = KeyboardMapping(self)
        try:
            return await self._keyboard.prepare(key)
        except BaseException:
            self._ready = False
            raise

    async def prepare_button(self) -> ButtonPlan:
        """Read a primary-button plan for the prepared point/source pair; never permits mouse input."""
        if self._buttons is not None:
            raise failure("desktop_input_unverified")
        self._buttons = ButtonMapping(self)
        try:
            return await self._buttons.prepare()
        except BaseException:
            self._ready = False
            raise

    async def verify_button(self) -> ButtonPlan:
        if self._buttons is None:
            raise failure("desktop_input_unverified")
        try:
            return await self._buttons.verify()
        except BaseException:
            self._ready = False
            raise

    async def verify_key(self) -> int:
        if self._keyboard is None:
            raise failure("desktop_input_unverified")
        try:
            return await self._keyboard.verify()
        except BaseException:
            self._ready = False
            raise

    async def prepare_pointer_actions(self) -> ButtonPlan:
        """Read XKB button actions/state for an existing mapped point; no mouse dispatch permission."""
        if self._pointer_actions is not None:
            raise failure("desktop_input_unverified")
        self._pointer_actions = PointerActions(self)
        try:
            return await self._pointer_actions.prepare()
        except BaseException:
            self._ready = False
            raise

    async def verify_pointer_actions(self) -> ButtonPlan:
        if self._pointer_actions is None:
            raise failure("desktop_input_unverified")
        try:
            return await self._pointer_actions.verify()
        except BaseException:
            self._ready = False
            raise

    async def prepare_sources(self) -> Sources:
        """Read the server's virtual pair on the proven connection; never grants input permission."""
        if self._sources is not None:
            raise failure("desktop_input_unverified")
        self._sources = XTestSources(self.monitor)
        try:
            await self.check()
            sources = await self._sources.prepare()
            await self.check()
            return sources
        except BaseException:
            self._ready = False
            raise

    async def verify_sources(self) -> Sources:
        if self._sources is None:
            raise failure("desktop_input_unverified")
        try:
            await self.check()
            sources = await self._sources.verify()
            await self.check()
            return sources
        except BaseException:
            self._ready = False
            raise

    async def key_packet(self) -> KeyPacket:
        """Prepare one internal wire pair; workers still require target association and business permit."""
        try:
            code = await self.verify_key()
            sources = await self.verify_sources()
            if self._keyboard is None:
                raise failure("desktop_input_unverified")
            transition = await self._keyboard.switch_proof(sources)
            await self.check()
            if self._sources is None:
                raise failure("desktop_input_unverified")
            return KeyPacket(self.monitor, self._sources.opcode, sources, code, transition)
        except BaseException:
            self._ready = False
            raise
