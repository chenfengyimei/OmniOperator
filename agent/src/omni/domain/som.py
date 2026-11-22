"""Pure Android SoM node admission and stable numbering: bounded, password-excluding; no image I/O.

One selection describes which nodes of an already-authorized observation get a 1-based mark on the
same screenshot. This module never captures, decodes or draws; the adapter renders the PNG and the
application/owner surface stays behind K14 D0.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from .contracts import BusinessError
from .desktop import MAX_CAPTURE_EDGE, MAX_CAPTURE_PIXELS

MAX_MARKS = 50
MAX_NODES = 128


def _invalid(code: str, message: str, *, status: int = 422) -> BusinessError:
    return BusinessError(code, message, status=status)


@dataclass(frozen=True)
class SomNode:
    """One observation node in observe order; text is never needed for numbering."""

    node_id: str
    bounds: tuple[int, int, int, int]
    visible: bool = True
    password: bool = False


@dataclass(frozen=True)
class SomMark:
    number: int
    node_id: str
    bounds: tuple[int, int, int, int]


@dataclass(frozen=True)
class SomSelection:
    width: int
    height: int
    marks: tuple[SomMark, ...]
    skipped: int
    truncated: bool


def _bounds(value: object) -> tuple[int, int, int, int]:
    if not isinstance(value, (list, tuple)):
        raise _invalid("android_som_invalid", "node bounds must be [left, top, right, bottom]")
    items = list(cast(list[object] | tuple[object, ...], value))
    if len(items) != 4:
        raise _invalid("android_som_invalid", "node bounds must be [left, top, right, bottom]")
    if any(type(item) is not int for item in items):
        raise _invalid("android_som_invalid", "node bounds must contain integers")
    left, top, right, bottom = (cast(int, item) for item in items)
    if left >= right or top >= bottom:
        raise _invalid("android_som_invalid", "node bounds must have positive width and height")
    return left, top, right, bottom


def normalize_nodes(value: object) -> list[SomNode]:
    if not isinstance(value, (list, tuple)):
        raise _invalid("android_som_invalid", "nodes must be an observation list")
    items = list(cast(list[object] | tuple[object, ...], value))
    if len(items) > MAX_NODES:
        raise _invalid("android_som_invalid", f"nodes must be at most {MAX_NODES}")
    result: list[SomNode] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise _invalid("android_som_invalid", "each node must be an object")
        row = cast(dict[str, object], item)
        node_id = row.get("node_id")
        if not isinstance(node_id, str):
            raise _invalid("android_som_invalid", "each node needs a canonical UUID")
        try:
            canonical = str(uuid.UUID(node_id))
        except (ValueError, AttributeError):
            raise _invalid("android_som_invalid", "each node needs a canonical UUID") from None
        if canonical != node_id or node_id in seen:
            raise _invalid("android_som_invalid", "node ids must be canonical UUIDs and unique")
        visible = row.get("visible", True)
        password = row.get("password", False)
        if type(visible) is not bool or type(password) is not bool:
            raise _invalid("android_som_invalid", "visible and password must be booleans")
        seen.add(node_id)
        result.append(SomNode(node_id, _bounds(row.get("bounds")), visible, password))
    return result


def frame_size(width: object, height: object) -> tuple[int, int]:
    if (
        type(width) is not int
        or type(height) is not int
        or not 1 <= width <= MAX_CAPTURE_EDGE
        or not 1 <= height <= MAX_CAPTURE_EDGE
        or width * height > MAX_CAPTURE_PIXELS
    ):
        raise _invalid("android_som_invalid", "frame dimensions exceed the capture limits")
    return width, height


def select_marks(nodes: Sequence[SomNode], width: object, height: object) -> SomSelection:
    checked_width, checked_height = frame_size(width, height)
    marks: list[SomMark] = []
    skipped = 0
    truncated = False
    for node in nodes:
        if not node.visible or node.password:
            skipped += 1
            continue
        left, top, right, bottom = node.bounds
        left, top = max(0, left), max(0, top)
        right, bottom = min(checked_width, right), min(checked_height, bottom)
        if left >= right or top >= bottom:
            skipped += 1
            continue
        if len(marks) >= MAX_MARKS:
            truncated = True
            break
        marks.append(SomMark(len(marks) + 1, node.node_id, (left, top, right, bottom)))
    return SomSelection(checked_width, checked_height, tuple(marks), skipped, truncated)


def mark_map(selection: SomSelection) -> list[dict[str, object]]:
    """Bounded owner/internal mapping; node ids never enter the model-visible image pixels."""
    return [{"number": mark.number, "node_id": mark.node_id, "bounds": list(mark.bounds)} for mark in selection.marks]
