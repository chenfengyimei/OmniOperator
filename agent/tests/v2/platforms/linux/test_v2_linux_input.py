"""Input protocol on injected Xorg/logind peers; no keyboard, pointer, X socket or cookie accessed."""

from omni_test_support.v2.linux_input import (
    ROOT as ROOT,
)
from omni_test_support.v2.linux_input import (
    WINDOW as WINDOW,
)
from omni_test_support.v2.linux_input import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.linux_input import (
    InputPeer as InputPeer,
)
from omni_test_support.v2.linux_input import (
    InputWire as InputWire,
)
from omni_test_support.v2.linux_input import (
    OwnedConnection as OwnedConnection,
)
from omni_test_support.v2.linux_input import (
    Peer as Peer,
)
from omni_test_support.v2.linux_input import (
    SessionInspector as SessionInspector,
)
from omni_test_support.v2.linux_input import (
    X11Input as X11Input,
)
from omni_test_support.v2.linux_input import (
    asyncio as asyncio,
)
from omni_test_support.v2.linux_input import (
    device_bytes as device_bytes,
)
from omni_test_support.v2.linux_input import (
    devices as devices,
)
from omni_test_support.v2.linux_input import (
    linux_factory as linux_factory,
)
from omni_test_support.v2.linux_input import (
    monitor_factory as monitor_factory,
)
from omni_test_support.v2.linux_input import (
    pytest as pytest,
)
from omni_test_support.v2.linux_input import (
    reply as reply,
)
from omni_test_support.v2.linux_input import (
    struct as struct,
)


@pytest.mark.parametrize("fault", [None, "held", "extra_master", "touch", "malformed"])
def test_complete_released_device_state_is_required_and_names_or_padding_do_not_grant_control(fault):
    options = {} if fault is None else {fault: True}
    if fault is None:
        assert devices(device_bytes()) == devices(device_bytes(padding=233))
    else:
        with pytest.raises(BusinessError):
            devices(device_bytes(**options))


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "raw_key",
        "raw_motion",
        "hierarchy",
        "focus_event",
        "focus_changed",
        "held_key",
        "synthetic",
        "wrong_extension",
        "oversize",
    ],
)
def test_actual_guard_parses_events_at_barriers_and_latches_user_changes(fault):
    async def scenario():
        f, peer, opened = linux_factory(), InputPeer(), []
        inspector = SessionInspector(f.ops, f)
        await inspector.open(accessibility=False)
        monitor = monitor_factory(peer, opened)(inspector)
        try:
            await monitor.open()
            await monitor.arm()
            if fault in {"raw_key", "raw_motion", "hierarchy", "synthetic", "wrong_extension", "oversize"}:
                event = bytearray(32)
                event[0], event[1] = (163 if fault == "synthetic" else 35), (131 if fault == "wrong_extension" else 130)
                struct.pack_into("<H", event, 8, {"raw_key": 13, "raw_motion": 17, "hierarchy": 11}.get(fault, 13))
                struct.pack_into("<I", event, 4, 1025 if fault == "oversize" else 0)
                peer.event = bytes(event)
            elif fault == "focus_event":
                event = bytearray(32)
                event[0] = 10
                struct.pack_into("<I", event, 4, WINDOW)
                peer.event = bytes(event)
            elif fault == "focus_changed":
                peer.focus += 1
            elif fault == "held_key":
                peer.keys = True
            if fault is None:
                await monitor.check()
            else:
                with pytest.raises(BusinessError):
                    await monitor.check()
            assert all(opcode not in {73, 100, 101, 104} for opcode, _, _ in peer.sent)
            assert not any(opcode == 130 and minor not in {47, 48, 46} for opcode, minor, _ in peer.sent)
        finally:
            assert await monitor.close() and await inspector.close()
            assert all(c.closed for c in opened)

    asyncio.run(scenario())
