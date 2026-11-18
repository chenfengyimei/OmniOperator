"""One serial-targeted GStreamer PipeWire frame inside an owned helper, never the default remote or display."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import importlib.metadata
import io
import os
import stat
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

from PIL import Image

from omni.adapters.platform.desktop.linux.linux_fds import OwnedFd
from omni.domain.contracts import BusinessError
from omni.domain.desktop import MAX_CAPTURE_BYTES

MAX_EDGE, MAX_PIXELS, MAX_RAW = 4096, 16_000_000, 64 * 1024 * 1024


def failure(code: str = "portal_frame_invalid") -> BusinessError:
    return BusinessError(code, "Window frame is unavailable or could not be verified", status=503)


def rgb_png(width: int, height: int, stride: int, offset: int, raw: bytes) -> bytes:
    if (
        any(type(n) is not int for n in (width, height, stride, offset))
        or not 1 <= width <= MAX_EDGE
        or not 1 <= height <= MAX_EDGE
        or width * height > MAX_PIXELS
        or not width * 3 <= stride <= width * 3 + 4096
        or offset < 0
        or len(raw) > MAX_RAW
        or offset + (height - 1) * stride + width * 3 > len(raw)
    ):
        raise failure()
    with Image.frombytes("RGB", (width, height), raw[offset:], "raw", "RGB", stride, 1) as image:
        if all(low == high for low, high in cast(tuple[tuple[int, int], ...], image.getextrema())):
            raise failure("desktop_capture_blank")
        output = io.BytesIO()
        image.save(output, format="PNG")
        content = output.getvalue()
    if not 0 < len(content) <= MAX_CAPTURE_BYTES:
        raise failure("portal_frame_too_large")
    return content


def publish(path: Path, content: bytes) -> None:
    # The parent is an exclusive host-owned UUID staging directory. No mkdir,
    # arbitrary output file, link traversal or overwrite is allowed here.
    if not path.is_absolute() or path.name != "frame.png" or str(uuid.UUID(path.parent.name)) != path.parent.name:
        raise failure()
    uid = getattr(os, "geteuid", lambda: None)()
    for item in (path.parent, *path.parent.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise failure()
        if item == path.parent and (info.st_uid != uid or info.st_mode & 0o077):
            raise failure()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW"), 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


class GstConsumer:
    def __init__(self) -> None:
        self.Gst: Any = None
        self.Video: Any = None

    def probe(self) -> None:
        try:
            if importlib.metadata.version("PyGObject") != "3.48.2":
                raise failure("portal_frame_dependency_incompatible")
            gi = importlib.import_module("gi")
            for namespace in ("Gst", "GstApp", "GstVideo"):
                gi.require_version(namespace, "1.0")
            self.Gst = importlib.import_module("gi.repository.Gst")
            importlib.import_module("gi.repository.GstApp")
            self.Video = importlib.import_module("gi.repository.GstVideo")
            self.Gst.init(None)
            if self.Gst.version()[:2] < (1, 20):
                raise failure("portal_frame_dependency_incompatible")
            for factory in ("pipewiresrc", "capsfilter", "videoconvert", "appsink"):
                if self.Gst.ElementFactory.find(factory) is None:
                    raise failure("portal_frame_adapter_missing")
            source = self.Gst.ElementFactory.make("pipewiresrc", None)
            for key in ("fd", "target-object", "always-copy", "dont-reconnect", "min-buffers", "max-buffers"):
                if source is None or source.find_property(key) is None:
                    raise failure("portal_frame_dependency_incompatible")
        except (ImportError, ValueError, importlib.metadata.PackageNotFoundError):
            raise failure("portal_frame_adapter_missing") from None

    def read(self, remote: OwnedFd, serial: int, path: Path, stopped: threading.Event) -> dict[str, Any]:
        if self.Gst is None or self.Video is None or type(serial) is not int or not 0 < serial < 1 << 64:
            raise failure()
        Gst, Video = self.Gst, self.Video
        pipeline = Gst.Pipeline.new(None)
        if pipeline is None:
            raise failure()
        try:
            if stopped.is_set():
                raise failure("portal_revoked")
            source, bounded, convert, rgb, sink = [
                Gst.ElementFactory.make(name, None)
                for name in ("pipewiresrc", "capsfilter", "videoconvert", "capsfilter", "appsink")
            ]
            if any(part is None for part in (source, bounded, convert, rgb, sink)):
                raise failure("portal_frame_adapter_missing")
            source.set_property("fd", remote.fileno())
            source.set_property("target-object", str(serial))
            source.set_property("always-copy", True)
            source.set_property("dont-reconnect", True)
            source.set_property("min-buffers", 2)
            source.set_property("max-buffers", 2)
            # No 'path', node ID, default device, reconnect or command parser.
            bounded.set_property("caps", Gst.Caps.from_string("video/x-raw,width=(int)[1,4096],height=(int)[1,4096]"))
            convert.set_property("n-threads", 1)
            rgb.set_property("caps", Gst.Caps.from_string("video/x-raw,format=(string)RGB"))
            for name, value in {
                "max-buffers": 1,
                "drop": True,
                "sync": False,
                "emit-signals": False,
                "wait-on-eos": False,
                "enable-last-sample": False,
            }.items():
                sink.set_property(name, value)
            parts = [source, bounded, convert, rgb, sink]
            for part in parts:
                pipeline.add(part)
            if not all(left.link(right) for left, right in zip(parts, parts[1:])):
                raise failure("portal_frame_format_unavailable")
            if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
                raise failure("portal_frame_stream_unavailable")
            sample = None
            for _ in range(30):
                if stopped.is_set():
                    raise failure("portal_revoked")
                message = pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.EOS)
                if message is not None:
                    raise failure("portal_frame_stream_unavailable")
                sample = sink.try_pull_sample(100 * Gst.MSECOND)
                if sample is not None:
                    break
            if sample is None:
                raise failure("portal_frame_timeout")
            info = Video.VideoInfo.new_from_caps(sample.get_caps())
            buffer = sample.get_buffer()
            if (
                info is None
                or buffer is None
                or info.finfo.format != Video.VideoFormat.RGB
                or info.finfo.n_planes != 1
                or not 1 <= info.width <= MAX_EDGE
                or not 1 <= info.height <= MAX_EDGE
                or info.width * info.height > MAX_PIXELS
                or not 0 < buffer.get_size() <= MAX_RAW
            ):
                raise failure()
            meta = Video.buffer_get_video_meta(buffer)
            if meta is not None and (
                meta.n_planes != 1
                or meta.width != info.width
                or meta.height != info.height
                or meta.format != Video.VideoFormat.RGB
                or meta.stride[0] != info.stride[0]
                or meta.offset[0] != info.offset[0]
            ):
                raise failure()
            mapped, mapping = buffer.map(Gst.MapFlags.READ)
            if not mapped:
                raise failure()
            try:
                content = rgb_png(info.width, info.height, info.stride[0], info.offset[0], mapping.data)
            finally:
                buffer.unmap(mapping)
            if stopped.is_set():
                raise failure("portal_revoked")
            publish(path, content)
            return {
                "width": info.width,
                "height": info.height,
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "method": "pipewire_window",
            }
        finally:
            # A blocking native teardown remains in this owned worker. The host
            # retains the process/capacity and kills/reaps it after its deadline.
            pipeline.set_state(Gst.State.NULL)
            state, current, _pending = pipeline.get_state(Gst.SECOND)
            if state != Gst.StateChangeReturn.SUCCESS or current != Gst.State.NULL:
                raise failure("portal_cleanup_pending")


class FrameExecutor:
    def __init__(self, consumer: GstConsumer | None = None) -> None:
        self.consumer: GstConsumer = consumer or GstConsumer()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="portal-frame")
        self.stopped = threading.Event()
        self.pending: asyncio.Future[Any] | None = None
        self.available: bool = False
        self.error_code: str | None = "portal_frame_adapter_missing"

    async def _work(self, operation: Callable[[], Any]) -> Any:
        if self.stopped.is_set() or self.pending is not None and not self.pending.done():
            raise failure("portal_frame_busy")
        self.pending = asyncio.get_running_loop().run_in_executor(self.pool, operation)
        self.pending.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
        return await asyncio.shield(self.pending)

    async def probe(self) -> None:
        try:
            await self._work(self.consumer.probe)
            self.available, self.error_code = True, None
        except BusinessError as error:
            self.available, self.error_code = False, error.code

    async def read(self, remote: OwnedFd, serial: int, path: Path) -> dict[str, Any]:
        if self.stopped.is_set() or self.pending is not None and not self.pending.done():
            remote.close()
            raise failure("portal_frame_busy")
        if not self.available:
            remote.close()
            raise failure(self.error_code or "portal_frame_adapter_missing")

        def actual() -> dict[str, Any]:
            try:
                return self.consumer.read(remote, serial, path, self.stopped)
            finally:
                remote.close()

        try:
            return await self._work(actual)
        finally:
            # If admitted, actual owns the fd until its real native work ends.
            if self.pending is None or self.pending.done():
                remote.close()

    async def close(self, timeout: float = 2) -> bool:
        self.stopped.set()
        if self.pending is not None and not self.pending.done():
            _, pending = await asyncio.wait((self.pending,), timeout=timeout)
            if pending:
                return False
        self.pool.shutdown(wait=False, cancel_futures=False)
        return True
