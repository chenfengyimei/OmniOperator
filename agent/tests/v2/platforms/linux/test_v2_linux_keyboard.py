"""Actual bounded X11 binary replies over injected peers; no X server, layout change or input dispatch."""

from omni_test_support.v2.linux_keyboard import (
    KEYS as KEYS,
)
from omni_test_support.v2.linux_keyboard import (
    ROOT as ROOT,
)
from omni_test_support.v2.linux_keyboard import (
    SYMBOLS as SYMBOLS,
)
from omni_test_support.v2.linux_keyboard import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.linux_keyboard import (
    FocusPeer as FocusPeer,
)
from omni_test_support.v2.linux_keyboard import (
    KeyboardPeer as KeyboardPeer,
)
from omni_test_support.v2.linux_keyboard import (
    XkbFixture as XkbFixture,
)
from omni_test_support.v2.linux_keyboard import (
    asyncio as asyncio,
)
from omni_test_support.v2.linux_keyboard import (
    close as close,
)
from omni_test_support.v2.linux_keyboard import (
    prepared as prepared,
)
from omni_test_support.v2.linux_keyboard import (
    pytest as pytest,
)
from omni_test_support.v2.linux_keyboard import (
    reply as reply,
)
from omni_test_support.v2.linux_keyboard import (
    setup_bytes as setup_bytes,
)
from omni_test_support.v2.linux_keyboard import (
    setup_reply as setup_reply,
)
from omni_test_support.v2.linux_keyboard import (
    struct as struct,
)


@pytest.mark.parametrize("key", KEYS)
def test_one_exact_native_special_key_is_read_twice_bound_to_focus_and_never_synthesized(key):
    async def scenario():
        peer = KeyboardPeer()
        _factory, inspector, leases, alias, _monitor, _connections, focus = await prepared(peer)
        try:
            await focus.prepare(alias)
            expected = 40 + list(SYMBOLS).index(key)
            assert await focus.prepare_key(key) == expected and await focus.verify_key() == expected
            assert len([row for row in peer.sent if row[0] == 101]) == 3
            assert all(row[0] in {2, 15, 38, 43, 44, 98, 101, 119, 128, 130, 131} for row in peer.sent)
            assert not any(opcode == 130 and minor not in {46, 47, 48} for opcode, minor, _ in peer.sent)
            assert not any(opcode == 131 and minor not in {0, 1, 4, 6, 8} for opcode, minor, _ in peer.sent)
            before = len(peer.sent)
            with pytest.raises(BusinessError):
                await focus.prepare_key(key)
            assert len(peer.sent) == before
        finally:
            await close(inspector, leases, focus)
        with pytest.raises(BusinessError):
            await focus.verify_key()

    assert set(SYMBOLS) == set(KEYS)
    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "ambiguous",
        "shifted",
        "modifier_key",
        "modifier_range",
        "key_stride",
        "length",
        "modifier_stride",
        "logical_lock",
        "screen",
    ],
)
def test_incomplete_ambiguous_or_modified_maps_cannot_be_used_and_failed_preparation_never_revives(fault):
    async def scenario():
        peer = KeyboardPeer()
        _factory, inspector, leases, alias, _monitor, _connections, focus = await prepared(peer)
        try:
            await focus.prepare(alias)
            if fault == "missing":
                peer.rows[40] = (0, 0, 0, 0)
            elif fault == "ambiguous":
                peer.rows[100] = peer.rows[40]
            elif fault == "shifted":
                peer.rows[40] = (ord("x"), SYMBOLS["Enter"], 0, 0)
            elif fault == "modifier_key":
                peer.modifiers = bytes([40, 0, 61, 0, 62, 0, 63, 0])
            elif fault == "modifier_range":
                peer.modifiers = bytes([7, 0, 61, 0, 62, 0, 63, 0])
            elif fault == "key_stride":
                peer.key_stride = 17
            elif fault == "length":
                peer.map_extra = b"four"
            elif fault == "modifier_stride":
                peer.modifier_stride = 33
            elif fault == "logical_lock":
                peer.logical_mask = 2
            else:
                peer.same_screen = 0
            with pytest.raises(BusinessError):
                await focus.prepare_key("Enter")
            peer.rows[40], peer.rows[100] = (SYMBOLS["Enter"], 0, 0, 0), (0, 0, 0, 0)
            peer.modifiers, peer.key_stride, peer.modifier_stride = b"\0" * 8, 4, 1
            peer.map_extra, peer.logical_mask, peer.same_screen = b"", 0, 1
            before = len(peer.sent)
            with pytest.raises(BusinessError):
                await focus.verify_key()
            with pytest.raises(BusinessError):
                await focus.check()
            assert len(peer.sent) == before
        finally:
            await close(inspector, leases, focus)

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["other_key", "modifiers", "mapping_event", "held", "focus"])
def test_prepared_mapping_latches_later_changes_and_does_not_restore_a_previous_plan(fault):
    async def scenario():
        peer = KeyboardPeer()
        _factory, inspector, leases, alias, _monitor, _connections, focus = await prepared(peer)
        try:
            await focus.prepare(alias)
            await focus.prepare_key("Left")
            if fault == "other_key":
                peer.rows[100] = (ord("x"), 0, 0, 0)
            elif fault == "modifiers":
                peer.modifiers = bytes([60, 0, 61, 0, 62, 0, 64, 0])
            elif fault == "mapping_event":
                value = bytearray(32)
                value[0] = 34
                peer.event = bytes(value)
            elif fault == "held":
                peer.keys = True
            else:
                peer.focus += 10
            with pytest.raises(BusinessError):
                await focus.verify_key()
            peer.rows.pop(100, None)
            peer.modifiers, peer.keys, peer.focus = bytes([60, 0, 61, 0, 62, 0, 63, 0]), False, focus._lineage[0]
            before = len(peer.sent)
            with pytest.raises(BusinessError):
                await focus.verify_key()
            assert len(peer.sent) == before
        finally:
            await close(inspector, leases, focus)

    asyncio.run(scenario())


def test_layout_race_between_mapping_and_modifier_reads_is_rejected_before_a_plan_is_ready():
    async def scenario():
        peer = KeyboardPeer()
        _factory, inspector, leases, alias, _monitor, _connections, focus = await prepared(peer)
        try:
            await focus.prepare(alias)

            def change(opcode):
                if opcode == 101:
                    peer.rows[100] = (ord("x"), 0, 0, 0)

            peer.on_read = change
            with pytest.raises(BusinessError) as denied:
                await focus.prepare_key("Tab")
            assert denied.value.code == "desktop_user_intervened"
        finally:
            await close(inspector, leases, focus)

    asyncio.run(scenario())


def test_unapproved_strings_and_unprepared_focus_do_not_query_native_layout():
    async def scenario():
        for value in ("Ctrl+A", "a", "", True, None):
            peer = KeyboardPeer()
            _factory, inspector, leases, _alias, _monitor, _connections, focus = await prepared(peer)
            try:
                with pytest.raises(BusinessError):
                    await focus.prepare_key(value)
                assert not peer.sent
            finally:
                await close(inspector, leases, focus)

    asyncio.run(scenario())


@pytest.mark.parametrize("minimum,maximum", [(0, 255), (9, 8), (255, 255)])
def test_connection_setup_keeps_real_keycode_bounds_and_rejects_bad_ranges(minimum, maximum):
    data = bytearray(setup_bytes())
    data[26:28] = bytes([minimum, maximum])
    if minimum <= maximum and minimum >= 8:
        parsed = setup_reply(bytes(data), 0)
        assert (parsed.minimum_keycode, parsed.maximum_keycode) == (minimum, maximum)
    else:
        with pytest.raises(BusinessError):
            setup_reply(bytes(data), 0)
