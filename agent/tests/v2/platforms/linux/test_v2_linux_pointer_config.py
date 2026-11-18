"""Canonical native pointer configuration and integrated drift/ownership behavior; no native input or property writes."""

import asyncio

import pytest
from omni_test_support.v2.linux_pointer_target import close
from omni_test_support.v2.v2_linux_pointer_action_helpers import action_context, action_event
from omni_test_support.v2.v2_linux_pointer_config_helpers import (
    IDENTITY,
    MATRIX,
    ConfigurationPeer,
    configuration_context,
    property_event,
)

from omni.domain.contracts import BusinessError


@pytest.mark.parametrize("bounded,projected", [(False, False), (True, False), (True, True)])
def test_actual_configuration_preserves_real_axes_feedback_and_transform_without_default_guesses(bounded, projected):
    async def scenario():
        peer = ConfigurationPeer()
        if bounded:
            for identity in (2, 4):
                for axis in peer.axes[identity]:
                    axis.update(low=(-20, 0x80000000), high=(4000, 0x40000000), resolution=2400, mode=1)
        if projected:
            peer.matrices[4] = (0.75, 0.25, 0.1, 0.0, 0.5, 0.25, 0.0, 0.0, 1.0)
        peer.feedbacks[4] = [(i, 5, 2, 65535) for i in range(8)] if projected else [(3, 5, 2, 7), (4, 0, 3, 0)]
        peer.refresh_topology()
        context = await configuration_context(peer)
        focus = context[-1]
        try:
            state = await focus.prepare_pointer_configuration()
            assert (state.source.identity, state.master.identity) == (4, 2)
            assert state.source_feedback == tuple(peer.feedbacks[4])
            assert len(state.source_feedback) == (8 if projected else 2)
            assert len(state.source.axes) == len(state.master.axes) == 2
            assert state.source.axes[0].minimum == (-20 * (1 << 32) + 0x80000000 if bounded else 0)
            assert state.source.axes[0].mode == int(bounded)
            assert state.source_transform != IDENTITY if projected else state.source_transform == IDENTITY
            expected = [(2, "feedback"), (2, "transform"), (4, "feedback"), (4, "transform")]
            assert peer.config_reads == expected * 2
            # Current positions and padding are not copied configuration.
            peer.axis_value, peer.axis_padding = (513, 12345), 0xA5
            peer.refresh_topology()
            peer.matrices[2] = (1.0, -0.0, 0.0, 0.0, 1.0, 0.0, -0.0, 0.0, 1.0)
            assert await focus.verify_pointer_configuration() == state
            assert peer.config_reads == expected * 3
            assert context[5].wire.sequence < context[5].wire.request_limit <= 8192
            assert all(minor == 0 for opcode, minor, _body in peer.sent if opcode == 132)
            with pytest.raises(BusinessError):
                await focus.prepare_pointer_configuration()
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault",
    [
        "feedback_class",
        "feedback_size",
        "feedback_trailing",
        "property_type",
        "property_remaining",
        "property_count",
        "property_format",
    ],
)
def test_incomplete_native_feedback_or_transform_frames_cannot_produce_a_configuration(fault):
    async def scenario():
        context = await configuration_context()
        peer, focus = context[0], context[-1]
        peer.config_fault = fault
        try:
            with pytest.raises(BusinessError):
                await focus.prepare_pointer_configuration()
            assert peer.config_reads
            peer.config_fault = None
            with pytest.raises(BusinessError):
                await focus.verify_pointer_configuration()
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault",
    [
        "duplicate_feedback",
        "denominator",
        "no_feedback",
        "too_many",
        "singular",
        "nan",
        "infinite",
        "missing_atom",
        "same_atom",
    ],
)
def test_unreadable_or_unusable_native_configuration_never_becomes_a_ready_plan(fault):
    async def scenario():
        context = await configuration_context()
        peer, focus = context[0], context[-1]
        if fault == "duplicate_feedback":
            peer.feedbacks[4] *= 2
        elif fault == "denominator":
            peer.feedbacks[4] = [(0, 2, 0, 4)]
        elif fault == "no_feedback":
            peer.feedbacks[4] = []
        elif fault == "too_many":
            peer.feedbacks[4] = [(i, 2, 1, 4) for i in range(9)]
        elif fault == "singular":
            peer.matrices[4] = (0.0,) * 9
        elif fault in {"nan", "infinite"}:
            peer.matrices[4] = (float("nan" if fault == "nan" else "inf"),) + IDENTITY[1:]
        elif fault == "missing_atom":
            peer.config_atoms[MATRIX] = 0
        else:
            peer.config_atoms[b"FLOAT"] = peer.config_atoms[MATRIX]
        try:
            with pytest.raises(BusinessError):
                await focus.prepare_pointer_configuration()
            with pytest.raises(BusinessError):
                await focus.verify_pointer_configuration()
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "change", ["range", "resolution", "mode", "label", "buttons", "feedback", "matrix", "master_matrix"]
)
def test_configuration_drift_cannot_rebind_an_old_point_after_visible_state_is_restored(change):
    async def scenario():
        context = await configuration_context()
        peer, focus = context[0], context[-1]
        try:
            await focus.prepare_pointer_configuration()
            if change == "range":
                peer.axes[4][0]["high"] = (4096, 0)
            elif change == "resolution":
                peer.axes[4][0]["resolution"] = 100
            elif change == "mode":
                peer.axes[4][1]["mode"] = 1
            elif change == "label":
                peer.axes[4][1]["label"] = 600
            elif change == "buttons":
                peer.labels[2] = (90, 93, 92)
            elif change == "feedback":
                peer.feedbacks[4] = [(0, 3, 1, 4)]
            else:
                peer.matrices[2 if change == "master_matrix" else 4] = (1.0, 0.0, 0.25) + IDENTITY[3:]
            peer.refresh_topology()
            with pytest.raises(BusinessError) as changed:
                await focus.verify_pointer_configuration()
            assert changed.value.code == "desktop_user_intervened"
            peer.axes[4][0].update(high=(-1, 0), resolution=0)
            peer.axes[4][1].update(mode=0, label=201)
            peer.labels[2], peer.feedbacks[4] = (90, 91, 92), [(0, 2, 1, 4)]
            peer.matrices = {2: IDENTITY, 4: IDENTITY}
            peer.refresh_topology()
            with pytest.raises(BusinessError):
                await focus.verify_pointer_configuration()
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault", ["missing_xy", "duplicate_axis", "axis_number", "mode", "wrong_source", "orphan_scroll", "zero_scroll"]
)
def test_full_native_classes_require_real_unique_axes_and_resolved_scroll_metadata(fault):
    async def scenario():
        peer = ConfigurationPeer()
        if fault == "missing_xy":
            peer.axes[4] = peer.axes[4][:1]
        elif fault == "duplicate_axis":
            peer.axes[4].append(dict(peer.axes[4][0]))
        elif fault == "axis_number":
            peer.axes[4][1]["number"] = 32
        elif fault == "mode":
            peer.axes[4][0]["mode"] = 2
        elif fault == "wrong_source":
            peer.axes[4][0]["source"] = 5
        else:
            peer.scroll = True
            if fault == "zero_scroll":
                peer.scroll_axis, peer.scroll_increment = 0, (0, 0)
        peer.refresh_topology()
        context = await configuration_context(peer)
        try:
            with pytest.raises(BusinessError):
                await context[-1].prepare_pointer_configuration()
        finally:
            await close(context)

    asyncio.run(scenario())


def test_actual_scroll_configuration_is_retained_with_only_unused_padding_ignored():
    async def scenario():
        peer = ConfigurationPeer()
        for identity in (2, 4):
            peer.axes[identity].append(dict(peer.axes[identity][0], number=2, label=202))
        peer.scroll = True
        peer.refresh_topology()
        context = await configuration_context(peer)
        try:
            state = await context[-1].prepare_pointer_configuration()
            assert len(state.source.axes) == 3 and len(state.source.scrolls) == 1
            peer.axis_padding = 0x5A
            peer.refresh_topology()
            assert await context[-1].verify_pointer_configuration() == state
            peer.scroll_increment = (2, 0)
            peer.refresh_topology()
            with pytest.raises(BusinessError):
                await context[-1].verify_pointer_configuration()
        finally:
            await close(context)

    asyncio.run(scenario())


def test_feedback_change_between_preparation_reads_is_not_adopted_without_notification():
    async def scenario():
        context = await configuration_context()
        peer = context[0]

        async def change(identity, category):
            if identity == 4 and category == "transform":
                peer.feedbacks[2] = [(0, 3, 1, 4)]

        peer.on_config_read = change
        try:
            with pytest.raises(BusinessError):
                await context[-1].prepare_pointer_configuration()
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize("event_kind", ["xkb", "master_property", "source_property"])
def test_native_change_notification_after_configuration_read_invalidates_the_held_preparation(event_kind):
    async def scenario():
        context = await configuration_context()
        peer, focus = context[0], context[-1]

        async def change(identity, category):
            if identity == 4 and category == "transform":
                peer.event = (
                    action_event(4)
                    if event_kind == "xkb"
                    else property_event(2 if event_kind == "master_property" else 4, peer.sequence)
                )

        peer.on_config_read = change
        try:
            with pytest.raises(BusinessError):
                await focus.prepare_pointer_configuration()
            assert context[5].wire.changed
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize("device", [2, 4])
def test_property_notification_after_preparation_revokes_unchanged_matrices_and_cannot_be_cleared(device):
    async def scenario():
        context = await configuration_context()
        peer, focus = context[0], context[-1]
        try:
            await focus.prepare_pointer_configuration()
            peer.event = property_event(device, peer.sequence)
            with pytest.raises(BusinessError):
                await focus.verify_pointer_configuration()
            assert context[5].wire.changed
            with pytest.raises(BusinessError):
                await focus.verify_pointer_configuration()
        finally:
            await close(context)

    asyncio.run(scenario())


def test_cancellation_during_configuration_read_does_not_abandon_the_external_native_session():
    async def scenario():
        context = await configuration_context()
        peer, focus = context[0], context[-1]
        entered, release = asyncio.Event(), asyncio.Event()

        async def blocked(_identity, _category):
            entered.set()
            await release.wait()

        peer.on_config_read = blocked
        try:
            task = asyncio.create_task(focus.prepare_pointer_configuration())
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not context[6][0].closed
            with pytest.raises(BusinessError):
                await focus.verify_pointer_configuration()
        finally:
            release.set()
            await close(context)

    asyncio.run(scenario())


def test_native_configuration_cannot_be_prepared_without_the_complete_prior_xkb_point_plan():
    async def scenario():
        context = await action_context(ConfigurationPeer())
        try:
            with pytest.raises(BusinessError):
                await context[-1].prepare_pointer_configuration()
            assert not context[0].config_reads
        finally:
            await close(context)

    asyncio.run(scenario())
