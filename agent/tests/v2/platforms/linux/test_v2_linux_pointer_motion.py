"""Independent analytic coordinate cases and actual native RandR/point/configuration planning; never sends input."""

import asyncio
import math
import struct
from dataclasses import replace
from fractions import Fraction

import pytest
from omni_test_support.v2.linux_pointer_target import close
from omni_test_support.v2.v2_linux_motion_helpers import MotionPeer, motion_context, randr_event
from omni_test_support.v2.v2_linux_pointer_config_helpers import IDENTITY, configuration_context
from omni_test_support.v2.v2_x11_helpers import ROOT, WINDOW

from omni.adapters.platform.desktop.linux.linux_button_mapping import ButtonPlan
from omni.adapters.platform.desktop.linux.linux_pointer_config import Axis, PointerDevice, PointerState
from omni.adapters.platform.desktop.linux.linux_pointer_motion import plan
from omni.adapters.platform.desktop.linux.linux_pointer_target import PointerPoint
from omni.adapters.platform.desktop.linux.linux_x11_wire import setup_reply
from omni.domain.contracts import BusinessError


def state(matrix=IDENTITY, bounded=False):
    low, high = (0, 65535) if bounded else (-1, -1)
    master = PointerDevice(
        2, 4, (90, 91, 92), tuple(Axis(i, 4, 200 + i, low << 32, high << 32, 0, int(bounded)) for i in (0, 1)), ()
    )
    source = replace(master, identity=4)
    return PointerState(
        ButtonPlan(1, 1, 1, 4, 2, 3, 3), master, source, ((0, 2, 1, 4),), ((0, 2, 1, 4),), IDENTITY, matrix
    )


def point(x=110, y=212):
    return PointerPoint(ROOT, WINDOW, x, y, (ROOT, WINDOW))


@pytest.mark.parametrize(
    "matrix,target,expected_request",
    [
        (IDENTITY, (110, 212), (110, 212)),
        ((1.0, 0.0, 10.0, 0.0, 1.0, -4.0, 0.0, 0.0, 1.0), (110, 212), (100, 216)),
        ((-1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0), (110, 212), (-112, 212)),
        ((0.0, 1.0, 0.0, -1.0, 0.0, 0.0, 0.0, 0.0, 1.0), (110, 212), (-214, 110)),
        ((1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, -1.0), (110, 212), (-112, -214)),
    ],
)
def test_native_unbounded_axis_normalization_translation_rotation_and_negative_denominator(
    matrix, target, expected_request
):
    result = plan(point(*target), state(matrix), 1920, 1080)
    assert result.request == expected_request
    assert result.raw == tuple(value << 32 for value in expected_request)
    assert result.processed == tuple(value << 32 for value in target)
    assert result.screen == target


def test_real_bounded_axes_scale_processed_raw_values_while_absolute_input_stays_in_root_units():
    matrix = (1.0, 0.0, 0.125, 0.0, 1.0, 0.25, 0.0, 0.0, 1.0)
    result = plan(point(350, 482), state(matrix, bounded=True), 1024, 1024)
    assert result.request == (222, 226)
    assert result.raw == (222 << 32, 226 << 32)
    assert result.processed == (22400 << 32, 30848 << 32)
    assert result.screen == (350.0, 482.0)


def test_a_reachable_pixel_beyond_nearest_rounding_candidates_uses_the_bounded_integer_search():
    matrix = (1.0, 0.0, 0.0, 0.0, 1 / 65536, 0.9, 0.0, 0.0, 1.0)
    result = plan(point(110, 0), state(matrix), 1920, 1080)
    assert result.request == (110, 32767)
    assert 0 < result.screen[1] < 1


def test_projective_coordinate_prediction_matches_an_independent_exact_fraction_evaluation():
    matrix = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 1 / 1024, 0.0, 1.0)
    result = plan(point(10, 20), state(matrix), 1920, 1080)
    x, y = result.request
    denominator = Fraction(x + 1, 1024) + 1
    expected = (Fraction(x + 1) / denominator - 1, Fraction(y + 1) / denominator - 1)
    assert tuple(math.floor(value) for value in expected) == (10, 20)
    assert result.screen == pytest.approx(tuple(float(value) for value in expected), abs=1e-10)
    assert tuple(value / (1 << 32) for value in result.processed) == pytest.approx(result.screen, abs=1 / (1 << 31))


@pytest.mark.parametrize(
    "case", ["integer_lattice", "signed_request", "singular", "zero_normalization", "outside", "scroll_xy"]
)
def test_unsupported_or_unreachable_input_is_not_rounded_to_another_pixel(case):
    original, target = state(), point()
    if case == "integer_lattice":
        original = state((2.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0))
    elif case == "signed_request":
        original = state((1.0, 0.0, 1000000.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0))
    elif case == "singular":
        original = state((0.0,) * 9)
    elif case == "zero_normalization":
        original = replace(
            original,
            source=replace(
                original.source, axes=(replace(original.source.axes[0], minimum=0), original.source.axes[1])
            ),
        )
    elif case == "outside":
        target = point(1920, 1)
    else:
        scroll = struct.pack("<HHHHH", 3, 6, 4, 0, 1) + struct.pack("<IiI", 2, 1, 0)
        original = replace(original, source=replace(original.source, scrolls=(scroll,)))
    with pytest.raises(BusinessError):
        plan(target, original, 1920, 1080)


@pytest.mark.parametrize("multi", [False, True])
def test_actual_randr_resources_active_scanouts_and_immutable_point_produce_a_read_only_plan(multi):
    async def scenario():
        peer = MotionPeer()
        if multi:
            peer.crtcs = {700: (0, 0, 960, 1080, 900, (800,)), 701: (960, 0, 960, 1080, 900, (801,))}
        context = await motion_context(peer)
        focus, wire = context[-1], context[5].wire
        try:
            result = await focus.prepare_motion()
            assert result.request == (110, 212) and result.point.root == ROOT
            assert result.raw == result.processed == (110 << 32, 212 << 32)
            assert peer.rr_selected and wire.randr_event == 90 and not wire.changed
            peer.rr_padding = 0xA5
            for _ in range(3):
                assert await focus.verify_motion() == result
            assert wire.sequence < wire.request_limit <= 8192
            before = wire.sequence
            await focus.verify_pointer_configuration()
            assert 0 < wire.sequence - before <= 256
            assert all(minor == 0 for opcode, minor, _body in peer.sent if opcode == 132)
            with pytest.raises(BusinessError):
                await focus.prepare_motion()
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "old_version",
        "event_collision",
        "event_range",
        "empty",
        "name_count",
        "duplicate",
        "resource_trailing",
        "status",
        "rotation",
        "foreign_mode",
        "gap",
    ],
)
def test_bad_or_unproved_randr_state_cannot_become_a_motion_plan(fault):
    async def scenario():
        context = await motion_context()
        peer, focus = context[0], context[-1]
        if fault == "missing":
            peer.rr_missing = True
        elif fault == "old_version":
            peer.rr_version = (1, 2)
        elif fault == "event_collision":
            peer.rr_event = 80
        elif fault == "event_range":
            peer.rr_event = 127
        elif fault == "gap":
            peer.crtcs = {700: (300, 300, 200, 200, 900, (800,))}
        else:
            peer.layout_fault = fault
        try:
            with pytest.raises(BusinessError):
                await focus.prepare_motion()
            peer.rr_missing, peer.rr_version, peer.rr_event, peer.layout_fault = False, (1, 6), 90, None
            peer.crtcs = {700: (0, 0, 1920, 1080, 900, (800,))}
            with pytest.raises(BusinessError):
                await focus.verify_motion()
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["time", "mode_name", "clock", "crtc", "root"])
def test_layout_drift_without_notifications_cannot_rebind_a_prepared_motion(change):
    async def scenario():
        context = await motion_context()
        peer, focus = context[0], context[-1]
        try:
            await focus.prepare_motion()
            if change == "time":
                peer.resource_times = (11, 12)
            elif change == "mode_name":
                peer.mode_name = b"other"
            elif change == "clock":
                peer.mode_clock += 1
            elif change == "crtc":
                peer.crtcs = {700: (1, 0, 1919, 1080, 900, (800,))}
            else:
                peer.nodes[ROOT][2] -= 1
            with pytest.raises(BusinessError):
                await focus.verify_motion()
            with pytest.raises(BusinessError):
                await focus.verify_motion()
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", [0, 1])
def test_randr_notification_during_void_selection_is_latched_before_the_first_layout_read(kind):
    async def scenario():
        context = await motion_context()
        peer, focus = context[0], context[-1]

        async def changed(minor, _identity):
            if minor == 4:
                peer.event = randr_event(kind)

        peer.on_randr = changed
        try:
            with pytest.raises(BusinessError):
                await focus.prepare_motion()
            assert context[5].wire.changed and not peer.rr_reads
        finally:
            await close(context)

    asyncio.run(scenario())


def test_native_layout_change_between_double_reads_does_not_become_a_new_baseline():
    async def scenario():
        context = await motion_context()
        peer = context[0]

        async def changed(minor, _identity):
            if minor == 20:
                peer.mode_clock += 1

        peer.on_randr = changed
        try:
            with pytest.raises(BusinessError):
                await context[-1].prepare_motion()
        finally:
            await close(context)

    asyncio.run(scenario())


def test_final_aggregate_guard_catches_a_focus_change_after_all_earlier_leaf_reads():
    async def scenario():
        context = await motion_context()
        peer, focus = context[0], context[-1]

        async def changed(identity, category):
            if identity == 4 and category == "transform":
                peer.focus = ROOT

        peer.on_config_read = changed
        try:
            with pytest.raises(BusinessError):
                await focus.verify_pointer_configuration()
            with pytest.raises(BusinessError):
                await focus.verify_pointer_configuration()
        finally:
            await close(context)

    asyncio.run(scenario())


def test_cancellation_during_resources_read_retains_external_session_ownership():
    async def scenario():
        context = await motion_context()
        peer, focus = context[0], context[-1]
        entered, release = asyncio.Event(), asyncio.Event()

        async def blocked(minor, _identity):
            if minor == 25:
                entered.set()
                await release.wait()

        peer.on_randr = blocked
        try:
            task = asyncio.create_task(focus.prepare_motion())
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not context[6][0].closed
            with pytest.raises(BusinessError):
                await focus.verify_motion()
        finally:
            release.set()
            await close(context)

    asyncio.run(scenario())


def test_actual_two_root_setup_is_retained_and_does_not_claim_a_single_desktop_origin(monkeypatch):
    import omni_test_support.v2.v2_x11_helpers as v2_x11_helpers

    original = v2_x11_helpers.setup_bytes

    def multiple(order=0, bits=32):
        raw = bytearray(original(order, bits))
        raw[20] = 2
        other = bytearray(raw[40:])
        struct.pack_into("<I", other, 0, ROOT + 1)
        raw += other
        return bytes(raw)

    raw = multiple()
    assert setup_reply(raw, 0).screens == setup_reply(raw, 1).screens == 2
    assert setup_reply(raw, 1).root == ROOT + 1
    monkeypatch.setattr(v2_x11_helpers, "setup_bytes", multiple)

    async def scenario():
        context = await motion_context()
        try:
            with pytest.raises(BusinessError):
                await context[-1].prepare_motion()
            assert not context[0].rr_selected
        finally:
            await close(context)

    asyncio.run(scenario())


def test_motion_planning_requires_the_full_prior_native_configuration():
    async def scenario():
        context = await configuration_context(MotionPeer())
        try:
            with pytest.raises(BusinessError):
                await context[-1].prepare_motion()
            assert not context[0].rr_selected
        finally:
            await close(context)

    asyncio.run(scenario())
