"""Observe real Pillow resource ownership across encoding faults and cancelled waiters."""

import asyncio
import base64
import hashlib
import io
import threading
from contextlib import closing
from types import SimpleNamespace

import pytest
from PIL import Image, PngImagePlugin

from omni.adapters import images
from omni.domain.images import ImageInfo


def source_image(mode="RGB", *, rotated=False):
    color = {"RGB": (21, 80, 140), "RGBA": (21, 80, 140, 70), "L": 83}[mode]
    with closing(Image.new(mode, (900, 600), color)) as image, io.BytesIO() as output:
        if rotated:
            image.paste((180, 40, 20), (0, 0, 450, 600))
            exif = image.getexif()
            exif[274] = 6
            image.save(output, format="JPEG", exif=exif)
        else:
            metadata = PngImagePlugin.PngInfo()
            metadata.add_text("private", "not-for-model")
            image.save(output, format="PNG", pnginfo=metadata)
        return output.getvalue()


class Resources:
    def __init__(self, monkeypatch, *, fault=None, close_fault=False, thumbnail_gate=None):
        self.fault = fault
        self.close_fault = close_fault
        self.thumbnail_gate = thumbnail_gate
        self.error = RuntimeError("injected image operation failure")
        self.images = {}
        self.buffers = []
        self.closed = {}
        self.before_thumbnail = None
        real_open, real_transpose, real_pixels = Image.open, images.ImageOps.exif_transpose, Image.frombytes
        owner = self

        class Buffer(io.BytesIO):
            def __init__(self, *args):
                super().__init__(*args)
                owner.buffers.append(self)

            def getvalue(self):
                if len(owner.buffers) == 3 and self is owner.buffers[-1]:
                    owner.hit("getvalue")
                return super().getvalue()

        def opened(*args, **kwargs):
            role = "verified" if "verified" not in self.images else "decoded"
            self.hit("open_verify" if role == "verified" else "open_decode")
            return self.track(real_open(*args, **kwargs), role)

        def transposed(*args, **kwargs):
            self.hit("transpose")
            return self.track(real_transpose(*args, **kwargs), "oriented")

        def pixels(*args, **kwargs):
            self.hit("pixels")
            return self.track(real_pixels(*args, **kwargs), "pixels")

        def encoded(value):
            self.hit("base64")
            return base64.b64encode(value)

        monkeypatch.setattr(images, "io", SimpleNamespace(BytesIO=Buffer))
        monkeypatch.setattr(images, "base64", SimpleNamespace(b64encode=encoded))
        monkeypatch.setattr(Image, "open", opened)
        monkeypatch.setattr(images.ImageOps, "exif_transpose", transposed)
        monkeypatch.setattr(Image, "frombytes", pixels)
        if fault == "metadata":
            monkeypatch.setattr(ImageInfo, "validate", lambda _: self.hit("metadata"))

    def hit(self, phase):
        if phase == self.fault:
            raise self.error

    def track(self, image, role):
        self.images[role] = image  # Strong references prevent GC from satisfying the cleanup assertions.
        self.closed[role] = 0
        original_close = image.close

        def close():
            self.closed[role] += 1
            original_close()
            if self.close_fault and role == "normalized":
                raise OSError("injected image close failure")

        image.close = close
        if role in {"verified", "decoded"}:
            method = "verify" if role == "verified" else "load"
            original = getattr(image, method)

            def read(*args, **kwargs):
                self.hit(method)
                return original(*args, **kwargs)

            setattr(image, method, read)
        elif role == "oriented":
            original_convert = image.convert

            def convert(*args, **kwargs):
                self.hit("convert")
                return self.track(original_convert(*args, **kwargs), "normalized")

            image.convert = convert
        elif role == "normalized":
            original_thumbnail, original_bytes = image.thumbnail, image.tobytes

            def thumbnail(*args, **kwargs):
                self.before_thumbnail = {name: self.closed[name] for name in ("verified", "decoded", "oriented")}
                if self.thumbnail_gate:
                    self.thumbnail_gate()
                self.hit("thumbnail")
                return original_thumbnail(*args, **kwargs)

            def tobytes(*args, **kwargs):
                self.hit("tobytes")
                return original_bytes(*args, **kwargs)

            image.thumbnail, image.tobytes = thumbnail, tobytes
        elif role == "pixels":
            original_save = image.save

            def save(*args, **kwargs):
                self.hit("save")
                return original_save(*args, **kwargs)

            image.save = save
        return image

    def assert_released(self):
        assert all(buffer.closed for buffer in self.buffers), "adapter buffer remains open"
        assert self.closed == dict.fromkeys(self.images, 1), "image core was not explicitly closed once"
        for image in self.images.values():
            with pytest.raises(ValueError, match="closed image"):
                _ = image.im
        if self.before_thumbnail is not None:
            assert self.before_thumbnail == dict.fromkeys(("verified", "decoded", "oriented"), 1)


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "open_verify",
        "metadata",
        "verify",
        "open_decode",
        "load",
        "transpose",
        "convert",
        "thumbnail",
        "tobytes",
        "pixels",
        "save",
        "getvalue",
        "base64",
    ],
)
def test_every_encoding_exit_releases_owned_images_and_buffers(monkeypatch, fault):
    content = source_image()
    resources = Resources(monkeypatch, fault=fault)
    if fault is None:
        assert images.inspect_image(content, wire=True).startswith("data:image/png;base64,")
        assert set(resources.images) == {"verified", "decoded", "oriented", "normalized", "pixels"}
        assert len(resources.buffers) == 3
    else:
        with pytest.raises(RuntimeError) as caught:
            images.inspect_image(content, wire=True)
        assert caught.value is resources.error
    resources.assert_released()


def test_metadata_only_decoding_releases_both_inputs(monkeypatch):
    content = source_image()
    resources = Resources(monkeypatch)
    assert images.inspect_image(content) == ImageInfo(
        "image/png", 900, 600, hashlib.sha256(content).hexdigest(), len(content)
    )
    assert set(resources.images) == {"verified", "decoded"} and len(resources.buffers) == 2
    resources.assert_released()


def test_close_failure_still_releases_other_resources_and_retains_original_error(monkeypatch):
    content = source_image()
    resources = Resources(monkeypatch, fault="save", close_fault=True)
    with pytest.raises(OSError, match="injected image close failure") as caught:
        images.inspect_image(content, wire=True)
    assert caught.value.__context__ is resources.error
    resources.assert_released()


def test_cancelled_waiter_keeps_thread_owned_until_resources_are_released(monkeypatch):
    content = source_image()
    entered, release = threading.Event(), threading.Event()

    def gate():
        entered.set()
        assert release.wait(8), "test did not release thumbnail worker"

    resources = Resources(monkeypatch, thumbnail_gate=gate)

    async def scenario():
        codec = images.ImageCodec()
        waiter = asyncio.create_task(codec.execute(content, wire=True))
        try:
            assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 8), 10)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert len(codec._pending) == 1 and not await codec.close(0)
            assert resources.closed["normalized"] == 0
        finally:
            release.set()
            await asyncio.gather(waiter, return_exceptions=True)
            assert await codec.close(8)
        assert not codec._pending
        resources.assert_released()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode,rotated", [("RGB", False), ("RGBA", False), ("L", False), ("RGB", True)])
def test_real_pixels_dimensions_orientation_and_metadata_remain_compatible(mode, rotated):
    content = source_image(mode, rotated=rotated)
    metadata = images.inspect_image(content)
    assert (metadata.width, metadata.height) == (900, 600)
    wire = images.inspect_image(content, wire=True)
    encoded = base64.b64decode(wire.split(",", 1)[1])
    with io.BytesIO(content) as raw, closing(Image.open(raw)) as original:
        original.load()
        expected = [original.getpixel(point) for point in ((225, 300), (675, 300))]
    if mode == "RGBA":
        expected = [value[:3] for value in expected]
    elif mode == "L":
        expected = [(value,) * 3 for value in expected]
    with io.BytesIO(encoded) as raw, closing(Image.open(raw)) as result:
        result.load()
        assert result.format == "PNG" and result.mode == "RGB" and not result.info
        assert result.size == ((341, 512) if rotated else (512, 341))
        points = ((170, 128), (170, 384)) if rotated else ((128, 170), (384, 170))
        assert [result.getpixel(point) for point in points] == expected
