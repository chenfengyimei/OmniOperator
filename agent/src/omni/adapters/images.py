"""Bounded image verification and metadata-free low-detail PNG encoding; no database or remote URLs."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import warnings
from typing import cast

from PIL import Image, ImageOps

from ..domain.contracts import BusinessError
from ..domain.images import MAX_BYTES, ImageInfo


def inspect_image(content: bytes, wire: bool = False) -> ImageInfo | str:
    if not 1 <= len(content) <= MAX_BYTES:
        raise BusinessError("image_too_large", "Images must contain at most 16 MiB", status=413)
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(content)) as image:
            mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(image.format or "")
            if mime is None or getattr(image, "n_frames", 1) != 1:
                raise ValueError("Unsupported or animated image")
            metadata = ImageInfo(mime, image.width, image.height, hashlib.sha256(content).hexdigest(), len(content))
            metadata.validate()
            image.verify()
        with Image.open(io.BytesIO(content)) as image:
            image.load()
            if not wire:
                return metadata
            normalized = ImageOps.exif_transpose(image).convert("RGB")
            normalized.thumbnail((512, 512), Image.Resampling.LANCZOS)
            pixels = Image.frombytes("RGB", normalized.size, normalized.tobytes())
            output = io.BytesIO()
            pixels.save(output, format="PNG")
            return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


class ImageCodec:
    def __init__(self) -> None:
        self._pending: set[asyncio.Task[object]] = set()
        self._slots = asyncio.Semaphore(2)
        self._closing = False

    async def execute(self, content: bytes, *, wire: bool = False) -> ImageInfo | str:
        if self._closing or len(self._pending) >= 8:
            raise BusinessError("image_codec_busy", "Image processing admission is closed or full", status=503)

        async def perform() -> ImageInfo | str:
            async with self._slots:
                try:
                    return await asyncio.to_thread(inspect_image, content, wire)
                except (ValueError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
                    raise BusinessError(
                        "image_invalid", "Image format, dimensions or content cannot be verified", status=422
                    ) from None

        task = asyncio.create_task(perform(), name="image-codec")
        self._pending.add(task)

        def done(future: asyncio.Task[object]) -> None:
            self._pending.discard(future)
            if not future.cancelled():
                future.exception()

        task.add_done_callback(lambda future: done(cast(asyncio.Task[object], future)))
        return await asyncio.shield(task)

    def stop_accepting(self) -> None:
        self._closing = True

    async def close(self, timeout_s: float = 30) -> bool:
        self.stop_accepting()
        if self._pending:
            await asyncio.wait(self._pending, timeout=max(0, timeout_s))
        return not self._pending
