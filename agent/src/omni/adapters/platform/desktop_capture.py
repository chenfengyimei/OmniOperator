"""Host-owned screenshot staging. File decoding runs only on the desktop I/O worker."""

from __future__ import annotations

import io
import os
import stat
from pathlib import Path
from typing import cast

from PIL import Image

from ...domain.contracts import BusinessError
from ...domain.desktop import MAX_CAPTURE_BYTES, CapturedFrame
from ..object_store import checked_path


def prepare(root: Path, ticket: str) -> Path:
    # The instance already owns and protects the parent run directory. Never follow links.
    path = checked_path(root, ticket + "/frame.png")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.mkdir(mode=0o700, exist_ok=False)
    return path


def inventory(root: Path, owned: set[str]) -> dict:
    checked_path(root, ".check")
    count = 0
    if root.exists():
        with os.scandir(root) as entries:
            for entry in entries:
                if entry.name not in owned:
                    count += 1
                if count >= 100:
                    return {"residual_count": count, "truncated": True, "error_code": "desktop_staging_residual"}
    return {"residual_count": count, "truncated": False, "error_code": "desktop_staging_residual" if count else None}


def read(root: Path, path: Path, receipt: dict) -> CapturedFrame:
    path = checked_path(root, path.relative_to(root).as_posix())
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != receipt["bytes"]:
            raise BusinessError("desktop_capture_invalid", "Screenshot file identity or size changed", status=503)
        content = stream.read(MAX_CAPTURE_BYTES + 1)
    frame = CapturedFrame(content, receipt["width"], receipt["height"], receipt["sha256"], receipt["method"])
    try:
        frame.validate()
    except BusinessError:
        raise BusinessError(
            "desktop_capture_invalid", "Screenshot content does not match its receipt", status=503
        ) from None
    try:
        with Image.open(io.BytesIO(content)) as picture:
            if picture.format != "PNG" or picture.size != (frame.width, frame.height) or picture.mode != "RGB":
                raise ValueError("invalid screenshot encoding")
            picture.verify()
        with Image.open(io.BytesIO(content)) as picture:
            picture.load()
            extrema = cast(tuple[tuple[int, int], ...], picture.getextrema())  # RGB was verified above.
            if all(low == high for low, high in extrema):
                raise BusinessError(
                    "desktop_capture_blank", "Uniform screenshot cannot verify window content", status=503
                )
    except (ValueError, OSError, SyntaxError, Image.DecompressionBombError):
        raise BusinessError("desktop_capture_invalid", "Screenshot is not a verified bounded PNG", status=503) from None
    return frame


def discard(root: Path, path: Path) -> None:
    path = checked_path(root, path.relative_to(root).as_posix())
    # Only this request's fixed file and empty private directory. Never recursive cleanup.
    path.unlink(missing_ok=True)
    if path.parent.exists():
        path.parent.rmdir()
