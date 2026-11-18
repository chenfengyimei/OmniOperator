"""Reusable media_frames fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from omni.adapters.workers import media as worker
from omni.domain.contracts import BusinessError
from omni.domain.media import MAX_SHEET_BYTES, sheet_result


@pytest.fixture
def frames(tmp_path, monkeypatch):
    from PIL import Image, ImageDraw

    fault = RuntimeError("injected frame failure")
    state = SimpleNamespace(
        phase=None,
        metadata=True,
        source_size=(640, 360),
        images=[],
        buffers=[],
        conversions=0,
        container_closed=0,
        retained=0,
        chunks=[b"fixture PNG bytes"],
        closes_fail=False,
    )
    output = tmp_path / "artifacts" / "sheet.png"
    request = {"video": str(tmp_path / "owned.mp4"), "sheet_output": str(output), "count": 3, "width": 320}
    (tmp_path / "owned.mp4").write_bytes(b"injected decoder input")

    def check(phase):
        if state.phase == phase:
            raise fault

    class Picture:
        def __init__(self, kind, size):
            self.kind, self.size, self.closed = kind, size, 0
            self.width, self.height = size
            state.images.append(self)

        def resize(self, size):
            check("resize")
            return Picture("resized", size)

        def paste(self, image, position):
            check("paste")

        def save(self, stream, *, format):
            assert format == "PNG"
            state.buffers.append(stream)
            for chunk in state.chunks:
                stream.write(chunk)
                state.retained = max(state.retained, stream.tell())
                check("encode")

        def close(self):
            self.closed += 1
            if state.closes_fail and self.kind == "resized":
                raise OSError("injected image close failure")

    class Frame:
        def __init__(self, index):
            self.width, self.height = state.source_size
            self.time = index + 0.25

        def to_image(self):
            state.conversions += 1
            if state.conversions == 2:
                check("convert")
            if self.width != request["width"]:
                assert all(image.closed == 1 for image in state.images if image.kind == "source"), (
                    "Unscaled sources must close before converting another frame"
                )
            return Picture("source", (self.width, self.height))

    class Container:
        duration = 0

        def __enter__(self):
            stream = SimpleNamespace(duration=10 if state.metadata else None, time_base=1)
            self.streams = SimpleNamespace(video=[stream])
            return self

        def __exit__(self, *args):
            state.container_closed += 1

        def decode(self, stream):
            for index in range(request["count"]):
                if index == 1:
                    check("decode")
                yield Frame(index)

    iterator = []

    def sample(container, stream, target):
        index = len(iterator)
        iterator.append(index)
        if index == 1:
            check("decode")
        return Frame(index)

    def canvas(mode, size, color):
        assert mode == "RGB"
        check("canvas")
        return Picture("canvas", size)

    def draw(image):
        return SimpleNamespace(text=lambda *args, **kwargs: check("text"))

    original_mkdir, original_write = Path.mkdir, Path.write_bytes

    def mkdir(path, *args, **kwargs):
        if path == output.parent:
            check("mkdir")
        return original_mkdir(path, *args, **kwargs)

    def write(path, content):
        if path == output:
            check("write")
        return original_write(path, content)

    av = ModuleType("av")

    def opened(path, *, mode, options):
        assert mode == "r" and options == {"protocol_whitelist": ""} and not path.closed
        return Container()

    av.open = opened
    monkeypatch.setitem(sys.modules, "av", av)
    monkeypatch.setattr(worker, "_frame_at", sample)
    monkeypatch.setattr(Image, "new", canvas)
    monkeypatch.setattr(ImageDraw, "Draw", draw)
    monkeypatch.setattr(Path, "mkdir", mkdir)
    monkeypatch.setattr(Path, "write_bytes", write)
    return SimpleNamespace(state=state, request=request, output=output, fault=fault)


__all__ = [
    "BusinessError",
    "MAX_SHEET_BYTES",
    "ModuleType",
    "Path",
    "SimpleNamespace",
    "asyncio",
    "frames",
    "hashlib",
    "json",
    "pytest",
    "sheet_result",
    "sys",
    "worker",
]
