"""Bound XKB state/control/action reads through the actual focus/keymap path, with no native input or display."""

import asyncio

import pytest
from test_v2_linux_foreground import prepared
from test_v2_linux_keyboard import KeyboardPeer, close

from omni.domain.contracts import BusinessError


@pytest.mark.parametrize("explicit_actions,explicit_components", [(False, False), (True, True)])
def test_both_implicit_and_explicit_no_action_preserve_a_stable_proof_despite_unused_padding(
    explicit_actions, explicit_components
):
    async def scenario():
        peer = KeyboardPeer()
        peer.xkb.actions = [0] if explicit_actions else None
        peer.xkb.explicit = explicit_components
        _factory, inspector, leases, alias, _monitor, _connections, focus = await prepared(peer)
        try:
            await focus.prepare(alias)
            assert await focus.prepare_key("Enter") == 40
            assert await focus.verify_key() == 40 and peer.xkb.reads == 3
            assert {minor for opcode, minor, _ in peer.sent if opcode == 131} == {0, 1, 4, 6, 8}
        finally:
            await close(inspector, leases, focus)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault",
    [
        "unsupported",
        "event_base",
        "latched",
        "slow_keys",
        "sticky_keys",
        "overlay",
        "server_action",
        "controls_device",
        "internal_modifier",
        "groups",
        "controls_length",
        "different_symbol",
        "missing_symbol",
        "parts",
        "range",
        "bounds",
        "behavior",
        "modifier",
        "virtual_modifier",
        "action_count",
        "oversized_symbols",
        "trailing",
    ],
)
def test_xkb_side_effects_incomplete_maps_and_unproved_state_cannot_become_a_key_plan(fault):
    async def scenario():
        peer = KeyboardPeer()
        if fault == "unsupported":
            peer.xkb.supported = False
        elif fault == "event_base":
            peer.xkb.event_base = 0
        elif fault == "latched":
            peer.xkb.state = b"\0\0\x01" + b"\0" * 15
        elif fault in {"slow_keys", "sticky_keys", "overlay"}:
            peer.xkb.enabled |= {"slow_keys": 2, "sticky_keys": 8, "overlay": 1024}[fault]
        elif fault == "server_action":
            peer.xkb.actions = [17]  # a server redirection action must not be used as a plain special key
        else:
            peer.xkb.fault = fault
        _factory, inspector, leases, alias, _monitor, _connections, focus = await prepared(peer)
        try:
            await focus.prepare(alias)
            with pytest.raises(BusinessError):
                await focus.prepare_key("Enter")
            before = len(peer.sent)
            with pytest.raises(BusinessError):
                await focus.verify_key()
            with pytest.raises(BusinessError):
                await focus.check()
            assert len(peer.sent) == before
        finally:
            await close(inspector, leases, focus)

    asyncio.run(scenario())


@pytest.mark.parametrize("subtype", [0, 1, 2, 3, 7])
def test_xkb_notifications_invalidate_the_proof_even_after_core_rows_and_server_state_are_restored(subtype):
    async def scenario():
        peer = KeyboardPeer()
        _factory, inspector, leases, alias, _monitor, _connections, focus = await prepared(peer)
        try:
            await focus.prepare(alias)
            await focus.prepare_key("Enter")
            value = bytearray(32)
            value[0:2] = bytes([80, subtype])
            peer.event = bytes(value)
            with pytest.raises(BusinessError):
                await focus.verify_key()
            with pytest.raises(BusinessError):
                await focus.verify_key()
        finally:
            await close(inspector, leases, focus)

    asyncio.run(scenario())


def test_control_change_between_the_snapshot_and_final_barrier_is_rejected_without_a_notification():
    async def scenario():
        peer = KeyboardPeer()
        _factory, inspector, leases, alias, _monitor, _connections, focus = await prepared(peer)
        try:
            await focus.prepare(alias)

            def change(_peer, minor):
                if minor == 8:
                    peer.xkb.enabled ^= 1  # even an otherwise supported repeat change invalidates this snapshot

            peer.xkb.hook = change
            with pytest.raises(BusinessError) as denied:
                await focus.prepare_key("Enter")
            assert denied.value.code == "desktop_user_intervened"
        finally:
            await close(inspector, leases, focus)

    asyncio.run(scenario())
