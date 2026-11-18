"""Actual ctypes ABI boundaries, mapping/state failures and compiler ownership; native parser availability reported separately."""

import asyncio
import ctypes
import hashlib
import sys
import threading

import pytest
from v2_xkbcommon_helpers import MINIMAL, SYMBOLS, Library

from omni.adapters.platform.desktop.linux.linux_eis_keymap import KeymapInfo
from omni.adapters.platform.desktop.linux.linux_xkbcommon import NativeXkb, XkbCompiler
from omni.domain.contracts import BusinessError


def info(data=MINIMAL):
    return KeymapInfo(len(data), hashlib.sha256(data).hexdigest())


@pytest.mark.parametrize(
    "key", ["Enter", "Tab", "Escape", "Backspace", "Delete", "Home", "End", "Left", "Right", "Up", "Down"]
)
def test_received_map_interpretation_uses_actual_unique_row_not_fixed_us_evdev_code(key):
    name = "Return" if key == "Enter" else key
    library = Library(key=name, code=167)
    native = NativeXkb(loader=lambda soname: library)
    meaning = native.interpret(MINIMAL, info(), key)
    assert meaning.key == key and meaning.keysym == SYMBOLS[name]
    assert meaning.xkb_code == 167 and meaning.evdev_code == 159 and meaning.keymap == info()
    assert library.released() == ["xkb_state_unref", "xkb_keymap_unref", "xkb_context_unref"]
    assert library.functions["xkb_context_new"].restype is ctypes.c_void_p
    assert library.functions["xkb_state_new"].restype is ctypes.c_void_p
    assert library.functions["xkb_keymap_new_from_buffer"].argtypes[2] is ctypes.c_size_t
    assert [entry[1][-1] for entry in library.calls if entry[0] == "xkb_state_update_key"] == [1, 0]


@pytest.mark.parametrize(
    "fault",
    [
        "context",
        "parse",
        "state",
        "initial_mask",
        "initial_state",
        "symbol",
        "count",
        "null",
        "after_down",
        "silent_state",
        "down_effect",
        "up_effect",
        "cleanup",
    ],
)
def test_parse_pointer_and_state_failures_do_not_publish_a_meaning_or_abandon_other_handles(fault):
    library = Library(fault=fault)
    native = NativeXkb(loader=lambda soname: library)
    with pytest.raises(BusinessError) as caught:
        native.interpret(MINIMAL, info(), "Enter")
    assert "private-canary" not in str(caught.value)
    expected = (
        []
        if fault == "context"
        else ["xkb_context_unref"]
        if fault == "parse"
        else ["xkb_keymap_unref", "xkb_context_unref"]
        if fault == "state"
        else ["xkb_state_unref", "xkb_keymap_unref", "xkb_context_unref"]
    )
    assert library.released() == expected


@pytest.mark.parametrize(
    "fault", ["duplicate", "missing", "multi", "range", "layouts", "mods", "hash", "internal_null", "key"]
)
def test_ambiguous_maps_bad_bounds_or_input_identity_are_never_accepted(fault):
    library = Library()
    data, identity, key = MINIMAL, info(), "Enter"
    if fault == "duplicate":
        library.rows[50] = library.rows[38]
    elif fault == "missing":
        library.rows = {}
    elif fault == "multi":
        library.rows[38] = [SYMBOLS["Return"], SYMBOLS["Tab"]]
    elif fault == "range":
        library.maximum = 776
    elif fault == "layouts":
        library.layouts = 17
    elif fault == "mods":
        library.mods = 33
    elif fault == "hash":
        identity = KeymapInfo(len(data), "0" * 64)
    elif fault == "internal_null":
        data = data[:-1] + b"\0x"
        identity = info(data)
    else:
        key = "Ctrl+Enter"
    native = NativeXkb(loader=lambda soname: library)
    with pytest.raises(BusinessError):
        native.interpret(data, identity, key)


def test_missing_native_library_or_required_symbol_is_explicit_and_does_not_choose_a_default_layout():
    def absent(soname):
        assert soname == "libxkbcommon.so.0"
        raise OSError("private-canary")

    for loader in (absent, lambda soname: Library(fault="xkb_state_key_get_syms")):
        with pytest.raises(BusinessError) as caught:
            NativeXkb(loader=loader)
        assert caught.value.code == "portal_xkb_unavailable" and "private-canary" not in str(caught.value)


def test_finished_compile_with_uncertain_native_unref_stays_pending_without_repeating_release():
    async def scenario():
        library = Library(fault="cleanup")
        compiler = XkbCompiler(lambda: NativeXkb(loader=lambda name: library))
        with pytest.raises(BusinessError) as caught:
            await compiler.compile(MINIMAL, info(), "Enter")
        assert caught.value.code == "portal_xkb_cleanup_pending" and compiler.task.done()
        owner = compiler.task
        assert not await compiler.close() and not await compiler.close()
        assert compiler.task is owner
        assert library.released() == ["xkb_state_unref", "xkb_keymap_unref", "xkb_context_unref"]
        # All handles here are injected integers. A real uncertain release
        # retains its helper owner until process reclamation, not another unref.

    asyncio.run(scenario())


def test_native_offline_parser_or_explicit_platform_availability_contract(request):
    if sys.platform != "linux":
        with pytest.raises(BusinessError) as unavailable:
            NativeXkb()
        assert unavailable.value.code == "portal_xkb_unavailable"
        request.node.user_properties.append(("native_xkb_executed", False))
        return
    try:
        native = NativeXkb()
    except BusinessError as unavailable:
        assert unavailable.code == "portal_xkb_unavailable"
        request.node.user_properties.append(("native_xkb_executed", False))
        return
    meaning = native.interpret(MINIMAL, info(), "Enter")
    assert (meaning.xkb_code, meaning.evdev_code, meaning.keysym) == (38, 30, 0xFF0D)
    with pytest.raises(BusinessError):
        native.interpret(b"invalid keymap\0", info(b"invalid keymap\0"), "Enter")
    request.node.user_properties.append(("native_xkb_executed", True))


@pytest.mark.parametrize("cancel_actual", [False, True])
def test_cancelled_compile_retains_original_executor_and_native_handles_until_actual_cleanup(cancel_actual):
    async def scenario():
        library = Library()
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        native = NativeXkb(loader=lambda soname: library)
        original = native.interpret

        def interpret(data, identity, key):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(10)
            return original(data, identity, key)

        native.interpret = interpret
        compiler = XkbCompiler(lambda: native)
        task = asyncio.create_task(compiler.compile(MINIMAL, info(), "Enter"))
        try:
            await entered.wait()
            if cancel_actual:
                compiler.task.cancel()
            else:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            await asyncio.sleep(0)
            assert not compiler.task.done() and not await compiler.close() and not library.released()
            release.set()
            with pytest.raises(asyncio.CancelledError if cancel_actual else BusinessError):
                await compiler.task
            assert await compiler.close()
            assert library.released() == ["xkb_state_unref", "xkb_keymap_unref", "xkb_context_unref"]
        finally:
            release.set()
            await asyncio.gather(task, compiler.task, return_exceptions=True)
            assert await compiler.close()

    asyncio.run(scenario())
