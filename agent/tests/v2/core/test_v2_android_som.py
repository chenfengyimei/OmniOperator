"""K14 Android SoM offline core: bounded node selection, stable numbering and bounded PNG rendering.

No device, capture, object publication, model or route is used. Real Pillow encodes the fixture PNGs.
"""

from __future__ import annotations

import io
import uuid

import pytest
from PIL import Image

from omni.adapters.som import render_som
from omni.domain.contracts import BusinessError
from omni.domain.som import (
    MAX_MARKS,
    MAX_NODES,
    SomSelection,
    frame_size,
    mark_map,
    normalize_nodes,
    select_marks,
)


def _uuid(index: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"omni:som:{index}"))


def _node(index: int, bounds: tuple[int, int, int, int], *, visible: bool = True, password: bool = False):
    return {"node_id": _uuid(index), "bounds": list(bounds), "visible": visible, "password": password}


def _png(width: int = 100, height: int = 80) -> bytes:
    with io.BytesIO() as output:
        Image.new("RGB", (width, height), "white").save(output, format="PNG")
        return output.getvalue()


def test_zero_one_and_many_nodes_produce_ordered_marks():
    empty = select_marks(normalize_nodes([]), 100, 80)
    assert empty.marks == () and empty.skipped == 0 and not empty.truncated
    nodes = normalize_nodes([_node(index, (index, index, index + 10, index + 10)) for index in range(3)])
    selection = select_marks(nodes, 100, 80)
    assert [mark.number for mark in selection.marks] == [1, 2, 3]
    assert [mark.node_id for mark in selection.marks] == [_uuid(0), _uuid(1), _uuid(2)]
    assert [mark.bounds for mark in selection.marks] == [(0, 0, 10, 10), (1, 1, 11, 11), (2, 2, 12, 12)]


def test_marks_are_capped_at_fifty_and_truncation_is_explicit():
    nodes = normalize_nodes([_node(index, (index, 0, index + 5, 5)) for index in range(MAX_NODES)])
    selection = select_marks(nodes, 200, 80)
    assert len(selection.marks) == MAX_MARKS and selection.truncated
    assert selection.marks[-1].number == MAX_MARKS
    assert [mark.number for mark in selection.marks] == list(range(1, MAX_MARKS + 1))


def test_password_invisible_and_out_of_frame_nodes_are_skipped():
    nodes = normalize_nodes(
        [
            _node(0, (0, 0, 10, 10), password=True),
            _node(1, (0, 0, 10, 10), visible=False),
            _node(2, (200, 200, 210, 210)),
            _node(3, (0, 0, 10, 10)),
        ]
    )
    selection = select_marks(nodes, 100, 80)
    assert [mark.node_id for mark in selection.marks] == [_uuid(3)]
    assert selection.skipped == 3 and not selection.truncated


def test_bounds_are_clipped_to_the_frame():
    nodes = normalize_nodes([_node(0, (-10, -10, 20, 20)), _node(1, (90, 70, 120, 100))])
    selection = select_marks(nodes, 100, 80)
    assert [mark.bounds for mark in selection.marks] == [(0, 0, 20, 20), (90, 70, 100, 80)]


def test_invalid_ids_bounds_flags_and_duplicates_are_rejected():
    bad_rows = [
        [{**_node(0, (0, 0, 10, 10)), "node_id": "not-a-uuid"}],
        [_node(0, (10, 0, 0, 10))],
        [_node(0, (0, 0, 10))],
        [_node(0, (0, 0, True, 10))],
        [{**_node(0, (0, 0, 10, 10)), "visible": "true"}],
        [_uuid(0)],
        [_node(0, (0, 0, 10, 10)), _node(0, (10, 10, 20, 20))],
    ]
    for rows in bad_rows:
        with pytest.raises(BusinessError) as caught:
            normalize_nodes(rows)
        assert caught.value.code == "android_som_invalid"


def test_frame_and_node_counts_are_bounded():
    for width, height in ((0, 10), (10, 0), (8193, 10), (4001, 4001)):
        with pytest.raises(BusinessError):
            frame_size(width, height)
    assert frame_size(100, 80) == (100, 80)
    with pytest.raises(BusinessError):
        normalize_nodes([_node(index, (0, 0, 1, 1)) for index in range(MAX_NODES + 1)])


def test_mark_map_is_bounded_and_keeps_node_ids_out_of_the_image():
    selection = select_marks(normalize_nodes([_node(0, (1, 2, 11, 12))]), 100, 80)
    mapped = mark_map(selection)
    assert mapped == [{"number": 1, "node_id": _uuid(0), "bounds": [1, 2, 11, 12]}]
    image = render_som(_png(), selection)
    assert _uuid(0).encode() not in image


def test_renderer_keeps_dimensions_changes_pixels_and_is_deterministic():
    selection = select_marks(normalize_nodes([_node(0, (10, 10, 40, 30)), _node(1, (50, 40, 90, 70))]), 100, 80)
    original = _png()
    first = render_som(original, selection)
    second = render_som(original, selection)
    assert first == second and first != original
    with Image.open(io.BytesIO(first)) as image:
        assert image.format == "PNG" and image.size == (100, 80)
        assert image.getpixel((39, 10)) == (255, 0, 0, 255)
        assert image.getpixel((0, 0)) == (255, 255, 255, 255)


def test_renderer_without_marks_returns_the_original_and_rejects_mismatches():
    original = _png()
    empty = SomSelection(100, 80, (), 0, False)
    assert render_som(original, empty) == original
    with pytest.raises(BusinessError):
        render_som(b"not a png", select_marks(normalize_nodes([_node(0, (0, 0, 10, 10))]), 100, 80))
    with pytest.raises(BusinessError):
        render_som(_png(20, 20), select_marks(normalize_nodes([_node(0, (0, 0, 10, 10))]), 100, 80))
