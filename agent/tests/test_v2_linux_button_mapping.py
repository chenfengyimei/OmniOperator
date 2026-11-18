"""Native two-stage primary-button maps and subscription races; actual input is never dispatched."""

import asyncio

import pytest
from test_v2_linux_pointer_target import close
from v2_linux_button_helpers import ButtonPeer, button_context, button_topology, mapping_event

from omni.adapters.platform.desktop.linux.linux_button_mapping import primary
from omni.adapters.platform.desktop.linux.linux_xtest import Sources
from omni.domain.contracts import BusinessError


@pytest.mark.parametrize(
    "source,master,physical,intermediate",
    [
        ((1, 2, 3), (1, 2, 3), 1, 1),
        ((3, 2, 1), (1, 2, 3), 3, 1),
        ((1, 2, 3), (3, 2, 1), 3, 3),
        ((3, 2, 1), (3, 2, 1), 1, 3),
        ((2, 3, 1), (2, 1, 3), 1, 2),
        ((1, 1, 3), (1, 2, 3), 1, 1),
        ((0, 3, 1), (1, 0, 3), 3, 1),
        ((255, 0, 1), (1, 2, 3), 3, 1),
        ((0,) * 254 + (1,), (1,), 255, 1),
    ],
)
def test_actual_native_maps_compose_slave_then_master_without_assuming_physical_button_one(
    source, master, physical, intermediate
):
    async def scenario():
        context = await button_context(ButtonPeer(source, master))
        peer, focus = context[0], context[-1]
        try:
            plan = await focus.prepare_button()
            assert (plan.physical, plan.master_code, plan.logical, plan.source, plan.master) == (
                physical,
                intermediate,
                1,
                4,
                2,
            )
            assert await focus.verify_button() == plan
            assert peer.map_reads == [2, 4, None] * 3
            wire = context[5].wire
            assert wire.button_mapping_event == 75 and wire.button_mapping_devices == {2, 4}
            assert not wire.changed
            assert all(minor == 0 for opcode, minor, _body in peer.sent if opcode == 132)
            assert all(minor in {6, 28, 45, 46, 47, 48, 59} for opcode, minor, _body in peer.sent if opcode == 130)
            with pytest.raises(BusinessError):
                await focus.prepare_button()
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "source,master",
    [
        (b"", b"\1"),
        (b"\1", b""),
        (bytes(256), b"\1"),
        (b"\1", bytes(256)),
        (b"\0\0", b"\1\2"),
        (b"\2\3", b"\1"),
        (b"\1\2", b"\2\3"),
    ],
)
def test_disabled_missing_or_unreadable_mapping_cannot_produce_a_primary_plan(source, master):
    with pytest.raises(BusinessError):
        primary(source, master, Sources(2, 3, 4, 5))


@pytest.mark.parametrize(
    "fault",
    [
        "size",
        "reply_type",
        "trailing",
        "truncated",
        "core_mismatch",
        "class_count",
        "logical_lock",
        "client_unset",
        "other_client_pointer",
        "missing_primary",
        "event_base",
    ],
)
def test_bad_native_receipts_counts_or_client_state_leave_no_reusable_button_plan(fault):
    async def scenario():
        context = await button_context()
        peer, focus = context[0], context[-1]
        if fault in {"size", "reply_type", "trailing", "truncated"}:
            peer.mapping_fault = fault
        elif fault == "core_mismatch":
            peer.core = b"\3\2\1"
        elif fault == "class_count":
            peer.maps[4] = b"\1\2"
        elif fault == "logical_lock":
            peer.logical_mask = 2
        elif fault == "client_unset":
            peer.client_set, peer.client_pointer = 0, 0
        elif fault == "other_client_pointer":
            peer.client_pointer = 4
        elif fault == "missing_primary":
            peer.maps[4] = bytes(3)
        else:
            peer.event_base = 127
        try:
            with pytest.raises(BusinessError):
                await focus.prepare_button()
            peer.maps, peer.core, peer.mapping_fault, peer.logical_mask = {2: b"\1\2\3", 4: b"\1\2\3"}, None, None, 0
            peer.client_set, peer.client_pointer, peer.event_base = 1, 2, 64
            with pytest.raises(BusinessError):
                await focus.verify_button()
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize("core,device", [(False, 2), (False, 4), (True, 0)])
def test_mapping_notification_during_void_subscription_is_not_missed(core, device):
    async def scenario():
        context = await button_context()
        peer, focus = context[0], context[-1]

        async def change():
            peer.event = mapping_event(core=core, device=device)

        peer.on_subscribe = change
        try:
            with pytest.raises(BusinessError):
                await focus.prepare_button()
            assert context[5].wire.changed
            assert not peer.map_reads
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "changed", ["source_unused", "master_unused", "both_preserve_effect", "count", "primary_removed", "client"]
)
def test_later_mapping_changes_cannot_rebind_an_old_primary_even_when_same_effect_would_be_possible(changed):
    async def scenario():
        context = await button_context()
        peer, focus = context[0], context[-1]
        try:
            await focus.prepare_button()
            if changed == "source_unused":
                peer.maps[4] = b"\1\3\2"
            elif changed == "master_unused":
                peer.maps[2] = b"\1\3\2"
            elif changed == "both_preserve_effect":
                peer.maps[2] = peer.maps[4] = b"\3\2\1"
            elif changed == "count":
                peer.topology = button_topology(4, 3)
            elif changed == "primary_removed":
                peer.maps[4] = bytes(3)
            else:
                peer.client_pointer = 4
            with pytest.raises(BusinessError):
                await focus.verify_button()
            peer.maps, peer.topology, peer.client_pointer = {2: b"\1\2\3", 4: b"\1\2\3"}, button_topology(), 2
            with pytest.raises(BusinessError):
                await focus.verify_button()
        finally:
            await close(context)

    asyncio.run(scenario())


def test_mapping_change_between_double_reads_is_not_accepted_as_a_new_baseline():
    async def scenario():
        context = await button_context()
        peer = context[0]

        async def change(identity):
            if identity is None and len(peer.map_reads) == 3:
                peer.maps[2] = peer.maps[4] = b"\3\2\1"

        peer.on_mapping = change
        try:
            with pytest.raises(BusinessError):
                await context[-1].prepare_button()
        finally:
            await close(context)

    asyncio.run(scenario())


def test_padding_changes_do_not_change_the_actual_mapping_identity():
    async def scenario():
        context = await button_context()
        peer, focus = context[0], context[-1]
        try:
            peer.mapping_padding = 0xAA
            plan = await focus.prepare_button()
            peer.mapping_padding = 0x55
            assert await focus.verify_button() == plan
        finally:
            await close(context)

    asyncio.run(scenario())


def test_a_late_primary_mapping_notification_revokes_the_plan_even_if_maps_are_restored():
    async def scenario():
        context = await button_context()
        peer, focus = context[0], context[-1]
        try:
            await focus.prepare_button()
            peer.event = mapping_event(device=4)
            with pytest.raises(BusinessError):
                await focus.verify_button()
            assert context[5].wire.changed
            with pytest.raises(BusinessError):
                await focus.verify_button()
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize("core,device,kind", [(False, 9, 2), (False, 4, 3), (True, 0, 3)])
def test_unbound_or_malformed_mapping_notifications_cannot_be_treated_as_our_mapping(core, device, kind):
    async def scenario():
        context = await button_context()
        peer, focus = context[0], context[-1]
        try:
            await focus.prepare_button()
            peer.event = mapping_event(core=core, device=device, request=kind)
            with pytest.raises(BusinessError):
                await focus.verify_button()
            with pytest.raises(BusinessError):
                await focus.verify_button()
        finally:
            await close(context)

    asyncio.run(scenario())


def test_cancellation_keeps_the_observer_owned_and_does_not_revive_a_mapping():
    async def scenario():
        context = await button_context()
        peer, focus = context[0], context[-1]
        entered, release = asyncio.Event(), asyncio.Event()

        async def blocked(_identity):
            entered.set()
            await release.wait()

        peer.on_mapping = blocked
        try:
            task = asyncio.create_task(focus.prepare_button())
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not context[6][0].closed
            peer.on_mapping = None
            with pytest.raises(BusinessError):
                await focus.verify_button()
        finally:
            release.set()
            await close(context)

    asyncio.run(scenario())
