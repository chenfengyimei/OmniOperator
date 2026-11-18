"""Read actual pointer XKB actions/state, shared subscriptions and drift/cancellation over injected binary replies."""

import asyncio
import struct

import pytest
from test_v2_linux_pointer_target import close
from v2_linux_button_helpers import button_context
from v2_linux_pointer_action_helpers import PointerActionPeer, action_context, action_event, device_info

from omni.adapters.platform.desktop.linux.linux_input import InputWire
from omni.adapters.platform.desktop.linux.linux_pointer_actions import action_identity
from omni.adapters.platform.desktop.linux.linux_x11_wire import Wire
from omni.domain.contracts import BusinessError


@pytest.mark.parametrize("mode", ["null", "trimmed", "explicit"])
def test_no_action_encodings_padding_and_shared_keyboard_client_preserve_the_actual_button_plan(mode):
    async def scenario():
        peer = PointerActionPeer(source=(3, 2, 1))
        context = await action_context(peer)
        focus = context[-1]
        if mode != "null":
            peer.pointer_actions = {2: [0, 0, 0], 4: [0, 0, 0]}
            peer.info_trim = mode == "trimmed"
        try:
            plan = await focus.prepare_pointer_actions()
            assert (plan.physical, plan.master_code, plan.source_buttons, plan.master_buttons) == (3, 1, 3, 3)
            assert peer.pointer_selects == [2, 4] and peer.info_reads == [2, 4] * 2
            peer.pointer_actions = {2: [0, 0, 0], 4: None}
            peer.info_trim, peer.info_padding = True, 0xA5
            assert await focus.verify_pointer_actions() == plan
            assert context[5].wire.xkb_pointer_devices == {2, 4} and not context[5].wire.changed
            assert context[5].wire.sequence < context[5].wire.request_limit <= 8192
            assert all(minor == 0 for opcode, minor, _body in peer.sent if opcode == 132)
            assert all(minor in {0, 1, 4, 6, 24} for opcode, minor, _body in peer.sent if opcode == 131)
            with pytest.raises(BusinessError):
                await focus.prepare_pointer_actions()
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize("device,kind", [(2, 1), (4, 5), (2, 17), (4, 18)])
def test_real_button_actions_on_either_device_are_not_plain_primary_buttons(device, kind):
    async def scenario():
        peer = PointerActionPeer()
        peer.pointer_actions[device] = [0, kind, 0]
        context = await action_context(peer)
        try:
            with pytest.raises(BusinessError):
                await context[-1].prepare_pointer_actions()
            peer.pointer_actions[device] = None
            with pytest.raises(BusinessError):
                await context[-1].verify_pointer_actions()
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault",
    [
        "unsupported",
        "not_present",
        "led_payload",
        "own_state",
        "total",
        "incomplete",
        "wrong_device",
        "out_of_range",
        "name_size",
        "trailing",
    ],
)
def test_incomplete_or_conflicting_native_action_receipts_do_not_produce_a_ready_plan(fault):
    async def scenario():
        peer = PointerActionPeer()
        context = await action_context(peer)
        peer.info_fault = fault
        try:
            with pytest.raises(BusinessError):
                await context[-1].prepare_pointer_actions()
            peer.info_fault = None
            with pytest.raises(BusinessError):
                await context[-1].verify_pointer_actions()
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize("device", [2, 4])
def test_an_action_change_during_either_void_subscription_is_observed_before_the_first_action_read(device):
    async def scenario():
        peer = PointerActionPeer()
        context = await action_context(peer)

        async def changed(selected):
            if selected == device:
                peer.event = action_event(device)

        peer.on_pointer_select = changed
        try:
            with pytest.raises(BusinessError):
                await context[-1].prepare_pointer_actions()
            assert not peer.info_reads and context[5].wire.changed
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize("device", [2, 4, 9])
def test_changed_or_unbound_device_notifications_do_not_allow_a_restored_action_plan(device):
    async def scenario():
        context = await action_context()
        peer, focus = context[0], context[-1]
        try:
            await focus.prepare_pointer_actions()
            peer.event = action_event(device)
            with pytest.raises(BusinessError):
                await focus.verify_pointer_actions()
            with pytest.raises(BusinessError):
                await focus.verify_pointer_actions()
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["name", "controls", "latch", "source_action", "master_action"])
def test_later_configuration_changes_invalidate_the_held_pointer_even_without_notifications(change):
    async def scenario():
        context = await action_context()
        peer, focus = context[0], context[-1]
        try:
            await focus.prepare_pointer_actions()
            if change == "name":
                peer.pointer_names[4] = b"another source"
            elif change == "controls":
                peer.xkb.enabled ^= 1
            elif change == "latch":
                peer.xkb.state = b"\0\0\1" + bytes(15)
            else:
                peer.pointer_actions[4 if change == "source_action" else 2] = [0, 17, 0]
            with pytest.raises(BusinessError):
                await focus.verify_pointer_actions()
            peer.pointer_names[4], peer.xkb.enabled, peer.xkb.state = b"source", 513, bytes(18)
            peer.pointer_actions = {2: None, 4: None}
            with pytest.raises(BusinessError):
                await focus.verify_pointer_actions()
        finally:
            await close(context)

    asyncio.run(scenario())


def test_action_metadata_change_between_preparation_reads_is_not_adopted_as_a_new_baseline():
    async def scenario():
        peer = PointerActionPeer()
        context = await action_context(peer)

        async def changed(device):
            if device == 4:
                peer.pointer_names[2] = b"changed master"

        peer.on_action_read = changed
        try:
            with pytest.raises(BusinessError):
                await context[-1].prepare_pointer_actions()
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["unsupported", "event_collision", "keyboard_pair", "mouse_keys", "latched"])
def test_pointer_preparation_requires_the_actual_supported_released_keyboard_environment(fault):
    async def scenario():
        context = await action_context()
        peer, focus = context[0], context[-1]
        if fault == "unsupported":
            peer.xkb.supported = False
        elif fault == "event_collision":
            peer.xkb.event_base = 75
        elif fault == "keyboard_pair":
            peer.xkb.device = 5
        elif fault == "mouse_keys":
            peer.xkb.enabled |= 16
        else:
            peer.xkb.state = b"\0\0\1" + bytes(15)
        try:
            with pytest.raises(BusinessError):
                await focus.prepare_pointer_actions()
            assert not peer.info_reads
        finally:
            await close(context)

    asyncio.run(scenario())


def test_the_full_uint8_button_range_uses_the_real_source_and_master_counts():
    async def scenario():
        context = await action_context(PointerActionPeer(source=(0,) * 254 + (1,), master=(1,)))
        try:
            plan = await context[-1].prepare_pointer_actions()
            assert (plan.physical, plan.source_buttons, plan.master_buttons) == (255, 255, 1)
            assert await context[-1].verify_pointer_actions() == plan
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize("key_first", [False, True])
def test_pointer_and_keyboard_readers_share_the_client_without_replacing_each_others_subscriptions(key_first):
    async def scenario():
        context = await action_context()
        peer, focus = context[0], context[-1]
        try:
            if key_first:
                assert await focus.prepare_key("Enter") == 40
            plan = await focus.prepare_pointer_actions()
            if not key_first:
                assert await focus.prepare_key("Enter") == 40
            assert await focus.verify_key() == 40
            assert await focus.verify_pointer_actions() == plan
            assert peer.pointer_selects == [2, 4] and not context[5].wire.changed
            peer.event = action_event(4)
            with pytest.raises(BusinessError):
                await focus.verify_key()
        finally:
            await close(context)

    asyncio.run(scenario())


def test_cancellation_during_action_read_retains_native_ownership_and_cannot_revive_the_preparation():
    async def scenario():
        context = await action_context()
        peer, focus = context[0], context[-1]
        entered, release = asyncio.Event(), asyncio.Event()

        async def blocked(_device):
            entered.set()
            await release.wait()

        peer.on_action_read = blocked
        try:
            task = asyncio.create_task(focus.prepare_pointer_actions())
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not context[6][0].closed
            with pytest.raises(BusinessError):
                await focus.verify_pointer_actions()
        finally:
            release.set()
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize("size", [0, 256])
def test_an_unrepresentable_button_count_cannot_be_hidden_in_a_valid_native_action_frame(size):
    with pytest.raises(BusinessError):
        action_identity(device_info(), 4, size)


def test_action_preparation_requires_the_already_prepared_native_button_plan():
    async def scenario():
        context = await button_context(PointerActionPeer())
        try:
            with pytest.raises(BusinessError):
                await context[-1].prepare_pointer_actions()
            assert not context[0].info_reads
        finally:
            await close(context)

    asyncio.run(scenario())


@pytest.mark.parametrize("wire_kind", [Wire, InputWire])
def test_lifetime_request_budget_does_not_wrap_or_allow_an_extra_packet(wire_kind):
    async def scenario():
        class Sink:
            def __init__(self):
                self.packets = []

            async def sock_sendall(self, _connection, packet):
                self.packets.append(packet)

        sink = Sink()
        wire = wire_kind(None, lambda: None)
        wire.loop = sink
        expected = 1024 if wire_kind is Wire else 8192
        assert wire.request_limit == expected
        wire.sequence = expected - 1
        await wire.send(43)
        assert wire.sequence == expected and sink.packets == [struct.pack("<BBH", 43, 0, 1)]
        with pytest.raises(BusinessError) as denied:
            await wire.send(43)
        assert denied.value.code == "desktop_x11_protocol_limit" and len(sink.packets) == 1

    asyncio.run(scenario())
