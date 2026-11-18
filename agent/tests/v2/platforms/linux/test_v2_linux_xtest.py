"""Xorg source identities on injected native wire peers; no real server, device or synthesized input."""

from omni_test_support.v2.linux_xtest import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.linux_xtest import (
    FocusPeer as FocusPeer,
)
from omni_test_support.v2.linux_xtest import (
    SourcePeer as SourcePeer,
)
from omni_test_support.v2.linux_xtest import (
    Sources as Sources,
)
from omni_test_support.v2.linux_xtest import (
    XTestSources as XTestSources,
)
from omni_test_support.v2.linux_xtest import (
    asyncio as asyncio,
)
from omni_test_support.v2.linux_xtest import (
    close as close,
)
from omni_test_support.v2.linux_xtest import (
    device_topology as device_topology,
)
from omni_test_support.v2.linux_xtest import (
    devices as devices,
)
from omni_test_support.v2.linux_xtest import (
    prepared as prepared,
)
from omni_test_support.v2.linux_xtest import (
    pytest as pytest,
)
from omni_test_support.v2.linux_xtest import (
    reply as reply,
)
from omni_test_support.v2.linux_xtest import (
    struct as struct,
)
from omni_test_support.v2.linux_xtest import (
    topology_bytes as topology_bytes,
)


def test_real_pair_uses_read_only_properties_instead_of_names_and_rechecks_every_device():
    async def scenario():
        peer = SourcePeer()
        _factory, inspector, leases, alias, monitor, _connections, focus = await prepared(peer)
        try:
            await focus.prepare(alias)
            assert await focus.prepare_sources() == Sources(2, 3, 4, 5)
            peer.property_padding = 237
            assert await focus.verify_sources() == Sources(2, 3, 4, 5)
            assert peer.property_reads == [2, 3, 4, 5, 6] * 3
            assert monitor.topology == devices(peer.topology)
            assert device_topology(peer.topology).entries[4].identity == 6
            assert device_topology(peer.topology).fingerprint == devices(topology_bytes(padding=233))
            assert all(opcode in {2, 15, 16, 43, 44, 98, 128, 130, 132} for opcode, _, _ in peer.sent)
            assert not any(opcode == 132 and minor != 0 for opcode, minor, _ in peer.sent)
            assert not any(opcode == 130 and minor not in {46, 47, 48, 59} for opcode, minor, _ in peer.sent)
            before = len(peer.sent)
            with pytest.raises(BusinessError):
                await focus.prepare_sources()
            assert len(peer.sent) == before
        finally:
            await close(inspector, leases, focus)
        with pytest.raises(BusinessError):
            await focus.verify_sources()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault",
    [
        "major_version",
        "minor_version",
        "opcode_collision",
        "missing_atom",
        "missing_pair",
        "duplicate",
        "master_marked",
        "disabled",
        "detached",
        "missing_class",
        "type",
        "remaining",
        "count",
        "format",
        "value",
        "length",
    ],
)
def test_unsupported_or_forged_sources_fail_before_ready_and_cannot_revive(fault):
    async def scenario():
        peer = SourcePeer()
        if fault == "major_version":
            peer.version = (1, 2)
        elif fault == "minor_version":
            peer.version = (2, 1)
        elif fault == "opcode_collision":
            peer.opcode = 130
        elif fault == "missing_atom":
            peer.atom = 0
        elif fault == "missing_pair":
            peer.marked = {4}
        elif fault == "duplicate":
            peer.marked.add(6)
        elif fault == "master_marked":
            peer.marked.add(3)
        elif fault in {"disabled", "detached", "missing_class"}:
            peer.topology = topology_bytes(**{fault: True})
        else:
            peer.property_fault = fault
        _factory, inspector, leases, alias, _monitor, _connections, focus = await prepared(peer)
        try:
            await focus.prepare(alias)
            with pytest.raises(BusinessError):
                await focus.prepare_sources()
            peer.version, peer.opcode, peer.atom, peer.marked = (2, 2), 132, 300, {4, 5}
            peer.topology, peer.property_fault = topology_bytes(), None
            before = len(peer.sent)
            with pytest.raises(BusinessError):
                await focus.verify_sources()
            with pytest.raises(BusinessError):
                await focus.check()
            assert len(peer.sent) == before
        finally:
            await close(inspector, leases, focus)

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["property_event", "raw_virtual_key", "duplicate", "topology", "focus"])
def test_prepared_pair_does_not_exempt_other_xtest_clients_and_latches_later_changes(fault):
    async def scenario():
        peer = SourcePeer()
        _factory, inspector, leases, alias, monitor, _connections, focus = await prepared(peer)
        try:
            await focus.prepare(alias)
            await focus.prepare_sources()
            if fault in {"property_event", "raw_virtual_key"}:
                event = bytearray(32)
                event[0], event[1] = 35, 130
                struct.pack_into("<H", event, 2, peer.sequence)
                struct.pack_into("<HHI", event, 8, 12 if fault == "property_event" else 13, 3, 1000)
                if fault == "property_event":
                    struct.pack_into("<IB", event, 16, 300, 1)
                else:
                    struct.pack_into("<IHHI", event, 16, 40, 5, 0, 0)
                peer.event = bytes(event)
            elif fault == "duplicate":
                peer.marked.add(6)
            elif fault == "topology":
                peer.topology = peer.topology.replace(b"arbitrary", b"different")
            else:
                peer.focus += 10
            with pytest.raises(BusinessError):
                await focus.verify_sources()
            peer.topology, peer.focus, peer.marked = topology_bytes(), focus._lineage[0], {4, 5}
            before = len(peer.sent)
            with pytest.raises(BusinessError):
                await focus.verify_sources()
            assert len(peer.sent) == before
            if fault in {"property_event", "raw_virtual_key"}:
                assert monitor.wire.changed
        finally:
            await close(inspector, leases, focus)

    asyncio.run(scenario())


def test_property_race_inside_a_snapshot_cannot_publish_a_ready_pair():
    async def scenario():
        peer = SourcePeer()
        _factory, inspector, leases, alias, _monitor, _connections, focus = await prepared(peer)
        try:
            await focus.prepare(alias)

            def change(identity):
                if identity == 6:
                    peer.marked.discard(5)

            peer.on_property = change
            with pytest.raises(BusinessError):
                await focus.prepare_sources()
            peer.marked, peer.on_property = {4, 5}, None
            with pytest.raises(BusinessError):
                await focus.verify_sources()
        finally:
            await close(inspector, leases, focus)

    asyncio.run(scenario())


def test_unprepared_source_proof_has_no_native_queries_or_connection_ownership():
    async def scenario():
        peer = SourcePeer()
        _factory, inspector, leases, _alias, monitor, _connections, focus = await prepared(peer)
        try:
            sources = XTestSources(monitor)
            with pytest.raises(BusinessError):
                await sources.verify()
            with pytest.raises(BusinessError):
                await sources.prepare()
            with pytest.raises(BusinessError):
                await focus.prepare_sources()
            assert not peer.sent
        finally:
            await close(inspector, leases, focus)

    asyncio.run(scenario())
