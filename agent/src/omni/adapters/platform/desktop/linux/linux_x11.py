"""XRes-bound local X11 targets and existing Composite pixmaps; never captures the root or sends input."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import struct
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

from PIL import Image

from omni.adapters.platform.desktop.linux.linux_bus import failure
from omni.adapters.platform.desktop.linux.linux_session import SessionInspector
from omni.adapters.platform.desktop.linux.linux_x11_socket import Connection
from omni.adapters.platform.desktop.linux.linux_x11_wire import Wire, XError, unpack
from omni.domain.contracts import BusinessError
from omni.domain.desktop import (
    MAX_CAPTURE_BYTES,
    MAX_CAPTURE_EDGE,
    MAX_CAPTURE_PIXELS,
    validate_capture,
    validate_target,
    validate_targets,
)


def publish(path: Path, content: bytes) -> None:
    if (
        path.name != "frame.png"
        or str(uuid.UUID(path.parent.name)) != path.parent.name
        or not path.is_absolute()
        or ".." in path.parts
    ):
        raise failure("desktop_capture_invalid")
    for parent in path.parents:
        if not stat.S_ISDIR(parent.lstat().st_mode):
            raise failure("desktop_capture_invalid")
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(directory)
        if info.st_uid != getattr(os, "geteuid")() or info.st_mode & 0o077:
            raise failure("desktop_capture_invalid")
        fd = os.open(
            "frame.png", os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=directory
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(directory)
        after = path.parent.lstat()
        if (after.st_dev, after.st_ino) != (info.st_dev, info.st_ino):
            raise failure("desktop_capture_invalid")
    finally:
        os.close(directory)


class X11Desktop:
    def __init__(
        self,
        inspector: SessionInspector,
        *,
        connection_factory: Callable[[SessionInspector], Awaitable[Connection]] = Connection.open,
        wire_factory: Callable[..., Wire] = Wire,
        publish_file: Callable[[Path, bytes], None] = publish,
    ) -> None:
        self.inspector = inspector
        self._connect, self._wire, self._publish = connection_factory, wire_factory, publish_file
        self.connection: Any = None
        self.wire: Any = None
        self.scope: str | None = None
        self.res: int | None = None
        self.composite: int | None = None
        self.capture_error: str | None = "desktop_x11_extension_unavailable"

    async def open(self) -> None:
        self.connection = await self._connect(self.inspector)
        self.wire = self._wire(self.connection.socket, self.connection.check)
        setup = await self.wire.authenticate(self.connection.cookie(), self.connection.screen)
        self.res = await self.wire.extension(b"X-Resource")
        version = await self.wire.request(self.res, struct.pack("<BBxx", 1, 2), 0)
        if unpack("HH", version, 8) != (1, 2):
            raise failure("desktop_x11_extension_unavailable")
        try:
            self.composite = await self.wire.extension(b"Composite")
            version = await self.wire.request(self.composite, struct.pack("<II", 0, 4))
            major, minor = unpack("II", version, 8)
            if major != 0 or not 2 <= minor <= 4:
                raise failure("desktop_x11_extension_unavailable")
            self.capture_error = None
        except BusinessError as error:
            if error.code != "desktop_x11_extension_unavailable":
                raise
            self.composite = None
        assert self.inspector.session is not None and self.inspector.system is not None
        payload: dict[str, object] = {
            "session": vars(self.inspector.session),
            "system": self.inspector.system.fingerprint,
            "server": vars(self.connection.process),
            "socket": self.connection.inode,
            "root": setup.root,
            "release": setup.release,
        }
        self.scope = (
            "linux:x11:"
            + hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        )
        await self.connection.verify()

    async def target(self, window: int) -> tuple[dict[str, object], tuple[int, int, int, int, int], int]:
        assert self.res is not None and self.scope is not None and self.wire.setup is not None
        if type(window) is not int or not 1 <= window <= 0xFFFFFFFF or window == self.wire.setup.root:
            raise failure("desktop_x11_target_unverified")
        client, pid = await self.wire.client(self.res, window)
        process = self.inspector.ops.process(pid, executable=True)
        if process.uid != self.inspector.ops.uid:
            raise failure("desktop_x11_target_unverified")
        visual, _category = await self.wire.attributes(window)
        geometry = await self.wire.geometry(window)
        root, width, height, border, _depth = geometry
        if (
            root != self.wire.setup.root
            or border
            or not 1 <= width <= MAX_CAPTURE_EDGE
            or not 1 <= height <= MAX_CAPTURE_EDGE
            or width * height > MAX_CAPTURE_PIXELS
        ):
            raise failure("desktop_x11_target_unverified")
        # This identifies current read metadata, not a durable XID generation or
        # authorization to act. Structural notifications fence an active capture.
        proof = hashlib.sha256(json.dumps([client, visual, geometry], separators=(",", ":")).encode()).hexdigest()
        target = validate_target(
            {
                "platform": "linux",
                "backend": "x11",
                "native_id": f"x11:{window:08x}:{proof}",
                "pid": pid,
                "process_created": process.created,
                "application_id": process.executable,
                "session_id": self.scope,
            }
        )
        return target, geometry, visual

    @staticmethod
    def window(target: dict[str, object]) -> int:
        value = target.get("native_id", "")
        if (
            target.get("platform") != "linux"
            or target.get("backend") != "x11"
            or re.fullmatch(r"x11:[0-9a-f]{8}:[0-9a-f]{64}", cast(str, value)) is None
        ):
            raise failure("desktop_target_changed")
        return int(cast(str, value).split(":")[1], 16)

    async def verified(self, target: dict[str, object]) -> tuple[tuple[int, int, int, int, int], int]:
        await self.connection.verify()
        actual, geometry, visual = await self.target(self.window(target))
        if actual != target:
            raise failure("desktop_target_changed")
        return geometry, visual

    async def targets(self, limit: int) -> dict[str, object]:
        await self.connection.verify()
        candidates, truncated = await self.wire.windows()
        items: list[dict[str, object]] = []
        for window in candidates:
            try:
                item, _, _ = await self.target(window)
            except XError as error:
                if error.code not in {3, 9}:
                    raise
                truncated = True
                continue
            except BusinessError as error:
                if error.code not in {"desktop_x11_target_unverified", "desktop_target_changed"}:
                    raise
                truncated = True
                continue
            if len(items) == limit:
                truncated = True
                break
            items.append(item)
        for item in items:
            await self.verified(item)
        await self.connection.verify()
        return validate_targets({"items": items, "truncated": truncated}, limit)

    async def verify(self, target: dict[str, object]) -> dict[str, object]:
        try:
            await self.verified(target)
        except XError:
            return {"matches": False, "target": None, "reason": "desktop_target_changed"}
        except BusinessError as error:
            if error.code not in {"desktop_target_changed", "desktop_x11_target_unverified"}:
                raise
            return {"matches": False, "target": None, "reason": error.code}
        return {"matches": True, "target": target, "reason": None}

    async def capture(self, target: dict[str, object], path: Path) -> dict[str, object]:
        if self.composite is None:
            raise failure(self.capture_error or "desktop_capture_unavailable")
        geometry, visual = await self.verified(target)
        window = self.window(target)
        await self.wire.watch(window)
        if await self.verified(target) != (geometry, visual):
            raise failure("desktop_target_changed")
        _root, width, height, _border, depth = geometry
        setup = self.wire.setup
        bits, pad = setup.formats.get(depth, (0, 0))
        if setup.visuals.get(visual) != (depth, 4, 0xFF0000, 0xFF00, 0xFF) or bits not in {24, 32} or pad != 32:
            raise failure("desktop_x11_pixel_format_unsupported")
        stride = (width * bits + 31) // 32 * 4
        raw = await self.wire.image(self.composite, window, geometry, stride)
        mode = {(0, 24): "BGR", (1, 24): "RGB", (0, 32): "BGRX", (1, 32): "XRGB"}[(setup.order, bits)]
        with Image.frombytes("RGB", (width, height), raw, "raw", mode, stride, 1) as image:
            extrema = cast(tuple[tuple[int, int], ...], image.getextrema())
            if all(low == high for low, high in extrema):
                raise failure("desktop_capture_blank")
            output = io.BytesIO()
            image.save(output, format="PNG")
            content = output.getvalue()
        if len(content) > MAX_CAPTURE_BYTES:
            raise failure("desktop_capture_too_large")
        await self.verified(target)
        self._publish(path, content)
        await self.verified(target)
        return validate_capture(
            {
                "target": target,
                "width": width,
                "height": height,
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "method": "x11_composite",
            },
            target,
        )

    async def close(self) -> bool:
        return self.connection is None or await self.connection.close()
