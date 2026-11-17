"""Read-only X11 focus ancestry proof for future explicit foreground delivery; never maps AT-SPI nodes or sends input."""

from __future__ import annotations

import struct
from typing import cast

from .linux_bus import failure
from .linux_input import X11Input
from .linux_window_leases import WindowLeases
from .linux_x11 import X11Desktop
from .linux_x11_wire import unpack

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
