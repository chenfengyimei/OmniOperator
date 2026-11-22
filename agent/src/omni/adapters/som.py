"""Deterministic bounded SoM PNG rendering; no capture, database or model calls.

The renderer consumes an already-authorized screenshot and a pure selection; it never reads files,
creates objects or chooses nodes. Output is a same-size PNG with red target boxes and black/yellow
number badges. Only numbers are drawn into pixels, never node text, ids or paths.
"""

from __future__ import annotations

import io
import warnings
from contextlib import closing

from PIL import Image, ImageDraw, ImageFont

from ..domain.contracts import MAX_BYTES, BusinessError
from ..domain.som import SomSelection

BADGE = 16


def _invalid(message: str) -> BusinessError:
    return BusinessError("android_som_invalid", message, status=422)


def _badge_position(left: int, top: int, width: int, height: int) -> tuple[int, int]:
    x = min(max(0, left), max(0, width - BADGE))
    y = min(max(0, top), max(0, height - BADGE))
    return x, y


def render_som(png: bytes, selection: SomSelection) -> bytes:
    """Draw the selected marks and return a bounded same-size PNG; zero marks keep the original."""
    if type(png) is not bytes or not 1 <= len(png) <= MAX_BYTES:
        raise BusinessError("android_som_too_large", "SoM input must be a bounded PNG", status=413)
    if not selection.marks:
        return png
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with io.BytesIO(png) as source, closing(Image.open(source)) as image:
                if image.format != "PNG" or image.size != (selection.width, selection.height):
                    raise _invalid("SoM input must be a PNG matching the selection dimensions")
                image.load()
                with closing(image.convert("RGBA")) as canvas:
                    draw = ImageDraw.Draw(canvas)
                    font = ImageFont.load_default()
                    for mark in selection.marks:
                        left, top, right, bottom = mark.bounds
                        draw.rectangle(
                            [left, top, max(left, right - 1), max(top, bottom - 1)],
                            outline=(255, 0, 0, 255),
                            width=2,
                        )
                        x, y = _badge_position(left, top, selection.width, selection.height)
                        draw.rectangle(
                            [x, y, x + BADGE - 1, y + BADGE - 1],
                            fill=(0, 0, 0, 255),
                            outline=(255, 255, 0, 255),
                        )
                        draw.text((x + 2, y + 2), str(mark.number), fill=(255, 255, 255, 255), font=font)
                    with io.BytesIO() as output:
                        canvas.save(output, format="PNG")
                        data = output.getvalue()
    except BusinessError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError, Warning):
        raise _invalid("SoM input must be a valid bounded PNG") from None
    if len(data) > MAX_BYTES:
        raise BusinessError("android_som_too_large", "SoM output exceeds the image limit", status=413)
    return data
