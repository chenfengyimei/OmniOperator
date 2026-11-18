"""Continuous X11 generations, owned guardian IPC and capture fencing; all native targets and input remain injected."""

import asyncio
import struct
import sys
import uuid
from types import SimpleNamespace

import pytest
from v2_desktop_helpers import captured_frame
from v2_x11_helpers import COOKIE, WINDOW, Peer, reply

from omni.adapters.platform.desktop import backend as transport
from omni.adapters.platform.desktop.backend import DesktopBackend, helper_environment
from omni.adapters.platform.desktop.linux.linux_lease_client import LinuxLeaseClient
from omni.adapters.platform.desktop.linux.linux_window_leases import LeaseWire, WindowLeases
from omni.adapters.platform.desktop.linux.linux_x11 import X11Desktop
from omni.adapters.platform.runtime.processes import OwnedProcess
from omni.adapters.workers.linux_window_lease import run
from omni.domain.contracts import BusinessError
from omni.domain.linux_window_leases import VERSION, decode, encode, parse_request, parse_response


def target(window=10):
    return {
        "platform": "linux",
        "backend": "x11",
        "native_id": f"x11:{window:08x}:" + "a" * 64,
        "pid": 71,
        "process_created": 100.0,
        "application_id": "/usr/bin/fixture",
        "session_id": "linux:x11:fixture",
    }


def request(operation, **fields):
    return {"version": VERSION, "request_id": str(uuid.uuid4()), "operation": operation, **fields}


class Wire(LeaseWire):
    def __init__(self):
        self.generations = {}
        self.unsubscribed = []

    async def subscribe(self, window):
        self.generations.setdefault(window, 0)

    async def unsubscribe(self, window):
        self.unsubscribed.append(window)
        self.generations.pop(window)

    async def request(self, *args, **kwargs):
        return b""


class Desktop:
    window = staticmethod(X11Desktop.window)

    def __init__(self):
        self.wire = Wire()
        self.connection = SimpleNamespace(verify=self.check)
        self.during_verify = None

    async def check(self):
        pass

    async def verified(self, original):
        assert original == target(self.window(original))
        if self.during_verify:
            await self.during_verify()


def event(kind, window):
    return struct.pack("<BBHII", kind, 0, 0, window, window) + bytes(20)


def test_destroy_and_recreate_with_identical_metadata_cannot_revive_an_old_window_lease():
    async def scenario():
        desktop = Desktop()
        leases = WindowLeases(desktop)
        old = (await leases.bind([target()]))[0]
        assert await leases.verify(old) == target()
        await desktop.wire.event(event(17, 10))
        with pytest.raises(BusinessError) as failure:
            await leases.verify(old)
        assert failure.value.code == "desktop_target_changed"
        fresh = (await leases.bind([target()]))[0]
        assert old["native_id"] != fresh["native_id"] and await leases.verify(fresh) == target()
        with pytest.raises(BusinessError):
            await leases.verify(old)

    asyncio.run(scenario())


def test_structure_change_during_identity_read_and_synthetic_or_unrelated_events_reject_proof():
    async def scenario():
        desktop = Desktop()
        leases = WindowLeases(desktop)
        desktop.during_verify = lambda: desktop.wire.event(event(22, 10))
        with pytest.raises(BusinessError):
            await leases.bind([target()])
        assert not leases.groups and not desktop.wire.generations
        desktop.during_verify = None
        await leases.bind([target()])
        for frame in [event(17 | 128, 10), event(17, 11), event(2, 10), b"short"]:
            with pytest.raises(BusinessError):
                await desktop.wire.event(frame)

    asyncio.run(scenario())


def test_bounded_group_eviction_unsubscribes_only_unreferenced_windows():
    async def scenario():
        desktop = Desktop()
        leases = WindowLeases(desktop)
        old = (await leases.bind([target(10)]))[0]
        second = (await leases.bind([target(11)]))[0]
        third = (await leases.bind([target(12)]))[0]
        assert len(leases.groups) == 2 and desktop.wire.unsubscribed == [10]
        assert await leases.verify(second) == target(11)
        assert await leases.verify(third) == target(12)
        with pytest.raises(BusinessError):
            await leases.verify(old)
        with pytest.raises(BusinessError):
            await leases.bind([target(13), target(13)])
        assert len(leases.groups) == 2

    asyncio.run(scenario())


def test_ipc_binds_each_reply_to_request_and_preserves_target_identity_except_the_opaque_alias():
    original = request("bind", targets=[target()])
    leased = {**target(), "native_id": "x11-live:" + str(uuid.uuid4())}
    reply = {
        **{key: original[key] for key in ("version", "request_id", "operation")},
        "ok": True,
        "result": [leased],
        "error_code": None,
    }
    assert parse_response(decode(encode(reply)), original)["result"] == [leased]
    for changed in [
        {**reply, "request_id": str(uuid.uuid4())},
        {**reply, "result": [{**leased, "pid": 72}]},
        {**reply, "result": [target()]},
        {**reply, "ok": 1},
    ]:
        with pytest.raises(BusinessError):
            parse_response(changed, original)
    with pytest.raises(BusinessError):
        parse_request(request("bind", targets=[target(), target()]))
    with pytest.raises(BusinessError):
        decode(b'{"version":1,"version":2}\n')


class Session:
    def __init__(self, clean=True):
        self.closed = False
        self.clean = clean
        self.alias = {**target(), "native_id": "x11-live:" + str(uuid.uuid4())}

    async def open(self):
        pass

    async def bind(self, targets):
        return [self.alias]

    async def verify(self, supplied):
        assert supplied == self.alias
        return target()

    async def tick(self):
        pass

    async def close(self):
        self.closed = True
        return self.clean


def test_worker_close_ack_requires_actual_release_and_eof_never_hides_cleanup_failure():
    async def scenario(clean, explicit):
        session = Session(clean)
        reader = asyncio.StreamReader()
        initial = request("bind", targets=[target()])
        reader.feed_data(encode(initial))
        if explicit:
            reader.feed_data(encode(request("close")))
        reader.feed_eof()
        frames = []

        async def send(frame):
            frames.append(decode(frame))

        code = await run(reader, send, session_factory=lambda: session)
        assert session.closed and code == (0 if clean else 2)
        assert len(frames) == (2 if clean and explicit else 1)

    for clean in (False, True):
        for explicit in (False, True):
            asyncio.run(scenario(clean, explicit))


def test_worker_rejects_verify_before_bind_without_opening_native_resources():
    async def scenario():
        reader = asyncio.StreamReader()
        reader.feed_data(encode(request("verify", target={**target(), "native_id": "x11-live:" + str(uuid.uuid4())})))
        reader.feed_eof()
        opened = []

        async def send(frame):
            pytest.fail("invalid startup cannot publish any proof")

        assert await run(reader, send, session_factory=lambda: opened.append(True)) == 2
        assert opened == []

    asyncio.run(scenario())


PROGRAM = """
import json,sys,uuid
aliases={}
for line in sys.stdin.buffer:
    q=json.loads(line)
    if q['operation']=='bind':
        result=[]
        for original in q['targets']:
            item={**original,'native_id':'x11-live:'+str(uuid.uuid4())}
            aliases[item['native_id']]=original
            result.append(item)
    elif q['operation']=='verify':result=aliases[q['target']['native_id']]
    else:result={'closed':True}
    sys.stdout.buffer.write((json.dumps({**{k:q[k] for k in ('version','request_id','operation')},'ok':True,'result':result,'error_code':None})+'\u005cn').encode('ascii'))
    sys.stdout.buffer.flush()
    if q['operation']=='close':break
"""


def test_real_owned_pipe_reuses_one_guardian_and_reclaims_its_slot_after_close():
    async def scenario():
        created = []

        async def spawn(argv, **options):
            assert argv[-1] == "omni.adapters.workers.linux_window_lease"
            owned = await OwnedProcess.spawn([sys.executable, "-u", "-c", PROGRAM], **options)
            created.append(owned)
            return owned

        slots = asyncio.Semaphore(2)
        client = LinuxLeaseClient(spawn, slots, helper_environment)
        try:
            deadline = asyncio.get_running_loop().time() + 10
            leased = (await client.bind([target()], deadline))[0]
            assert await client.guard(leased, deadline) == target()
            assert len(created) == 1 and client.health()["resident"] == 1 and slots._value == 1
            assert client.quiescent()
            with pytest.raises(BusinessError):
                await client.guard({**leased, "pid": 72}, deadline)
            assert await client.close(5)
            assert client.health()["resident"] == 0 and slots._value == 2
            assert created[0].reclaimed
            with pytest.raises(BusinessError):
                await client.guard(leased, deadline)
        finally:
            assert await client.close(5)

    asyncio.run(scenario())


def test_late_spawn_keeps_actual_owner_and_capacity_until_the_child_is_reclaimed():
    async def scenario():
        entered, proceed = asyncio.Event(), asyncio.Event()

        async def spawn(argv, **options):
            entered.set()
            await proceed.wait()
            return await OwnedProcess.spawn([sys.executable, "-u", "-c", PROGRAM], **options)

        slots = asyncio.Semaphore(2)
        client = LinuxLeaseClient(spawn, slots, helper_environment)
        task = asyncio.create_task(client.bind([target()], asyncio.get_running_loop().time() + 10))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not await client.close(0) and slots._value == 1
        proceed.set()
        assert await client.close(5)
        assert slots._value == 2

    asyncio.run(scenario())


class Guardian:
    def __init__(self):
        self.alias = {**target(), "native_id": "x11-live:" + str(uuid.uuid4())}
        self.calls = []
        self.resident = 1
        self.guards = 0
        self.reject_at = None

    def health(self):
        return {"resident": self.resident, "cleanup_pending": 0}

    def is_leased(self, supplied):
        return str(supplied.get("native_id", "")).startswith("x11-live:")

    def quiescent(self):
        return True

    def stop_accepting(self):
        pass

    async def bind(self, supplied, deadline, before_dispatch=None):
        if before_dispatch:
            await before_dispatch()
        self.calls.append(("bind", supplied))
        assert supplied == [target()]
        return [self.alias]

    async def guard(self, supplied, deadline, before_dispatch=None):
        if before_dispatch:
            await before_dispatch()
        self.calls.append(("guard", supplied))
        self.guards += 1
        assert supplied == self.alias
        if self.guards == self.reject_at:
            raise BusinessError("desktop_target_changed", "fixture window destroyed", status=409)
        return target()

    async def reclaim(self, timeout_s):
        return True

    async def close(self, timeout_s=30):
        self.resident = 0
        return True


class Transport(DesktopBackend):
    def __init__(self, guardian, output, **options):
        super().__init__(lease_backend=guardian, **options)
        self.output, self.requests = output, []

    async def _run(self, request, deadline, before_dispatch, sent=None):
        await self._dispatch_gate(before_dispatch)
        self.requests.append(request)
        return self.output


def test_discovery_binds_only_x11_preserves_order_and_returns_opaque_snapshot_identities(monkeypatch):
    monkeypatch.setattr(transport, "sys", SimpleNamespace(platform="linux"))

    async def scenario():
        guardian = Guardian()
        semantic = {**target(), "backend": "atspi", "native_id": "atspi:fixture"}
        backend = Transport(guardian, {"items": [semantic, target()], "truncated": True})
        try:
            assert await backend.targets(2) == {"items": [semantic, guardian.alias], "truncated": True}
            assert guardian.calls == [("bind", [target()])]
        finally:
            assert await backend.close(1)

    asyncio.run(scenario())


@pytest.mark.parametrize("matches", [False, True])
def test_verify_brackets_the_one_shot_helper_and_keeps_opaque_alias_in_the_receipt(matches):
    async def scenario():
        guardian = Guardian()
        output = {"matches": matches, "target": target() if matches else None, "reason": None}
        backend = Transport(guardian, output)
        try:
            result = await backend.verify(guardian.alias)
            assert backend.requests[0]["target"] == target()
            assert guardian.guards == 2
            assert result == {**output, "target": guardian.alias if matches else None}
            await asyncio.sleep(0)
            assert backend.quiescent() and backend.health()["resident"] == 1
        finally:
            assert await backend.close(1)
        assert backend.health()["resident"] == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["verify", "capture"])
def test_structure_change_after_native_read_blocks_receipt_and_capture_publication(operation, tmp_path):
    async def scenario():
        guardian = Guardian()
        guardian.reject_at = 2
        output = captured_frame() if operation == "capture" else {"matches": True, "target": target(), "reason": None}
        backend = Transport(guardian, output, capture_root=tmp_path)
        try:
            if operation == "verify":
                assert await backend.verify(guardian.alias) == {
                    "matches": False,
                    "target": None,
                    "reason": "desktop_target_changed",
                }
            else:
                with pytest.raises(BusinessError) as denied:
                    await backend.capture(guardian.alias)
                assert denied.value.code == "desktop_target_changed"
            assert len(backend.requests) == 1 and guardian.guards == 2
        finally:
            assert await backend.close(1)

    asyncio.run(scenario())


def test_revocation_before_identity_guard_blocks_native_read_and_lease_rpc():
    async def scenario():
        guardian = Guardian()
        backend = Transport(guardian, None)

        async def revoked():
            raise BusinessError("desktop_view_changed", "View was revoked", status=409)

        try:
            with pytest.raises(BusinessError) as denied:
                await backend.verify(guardian.alias, before_dispatch=revoked)
            assert denied.value.code == "desktop_view_changed"
            assert not backend.requests and not guardian.calls
        finally:
            assert await backend.close(1)

    asyncio.run(scenario())


def test_queued_rpc_timeout_does_not_retire_or_clear_the_actual_inflight_reply():
    async def scenario():
        async def spawn(argv, **options):
            return await OwnedProcess.spawn([sys.executable, "-u", "-c", PROGRAM], **options)

        entered, proceed = asyncio.Event(), asyncio.Event()
        client = LinuxLeaseClient(spawn, asyncio.Semaphore(2), helper_environment)

        async def gate():
            entered.set()
            await proceed.wait()

        try:
            deadline = asyncio.get_running_loop().time() + 10
            leased = (await client.bind([target()], deadline))[0]
            active = asyncio.create_task(client.guard(leased, deadline, gate))
            await entered.wait()
            with pytest.raises(TimeoutError):
                await client.guard(leased, asyncio.get_running_loop().time() + 0.02)
            assert client.health()["resident"] == 1 and client.health()["cleanup_pending"] == 0
            proceed.set()
            assert await active == target()
            assert await client.guard(leased, deadline) == target()
        finally:
            proceed.set()
            assert await client.close(5)

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["invalid_frame", "stdout_limit", "stderr_limit"])
def test_broken_guardian_retains_capacity_until_actual_process_reclamation(fault):
    async def scenario():
        owners = []
        allowed = False
        program = (
            "import sys;sys.stdout.buffer.write(b'bad\\n');sys.stdout.buffer.flush();sys.stdin.buffer.read()"
            if fault == "invalid_frame"
            else "import sys;sys.stdout.buffer.write(b'x'*70000);sys.stdout.buffer.flush();sys.stdin.buffer.read()"
            if fault == "stdout_limit"
            else "import sys;sys.stderr.buffer.write(b'x'*1100000);sys.stderr.buffer.flush();sys.stdin.buffer.read()"
        )

        async def spawn(argv, **options):
            owner = await OwnedProcess.spawn([sys.executable, "-u", "-c", program], **options)
            owners.append(owner)

            async def terminate(timeout_s):
                return await owner.terminate(timeout_s) if allowed else False

            return SimpleNamespace(process=owner.process, terminate=terminate)

        slots = asyncio.Semaphore(2)
        client = LinuxLeaseClient(spawn, slots, helper_environment)
        try:
            with pytest.raises(BusinessError):
                await client.bind([target()], asyncio.get_running_loop().time() + 10)
            assert not await client.close(0.1)
            assert slots._value == 1 and not owners[0].reclaimed
            assert client.health()["resident"] == client.health()["cleanup_pending"] == 1
        finally:
            allowed = True
            assert await client.close(5)
        assert slots._value == 2 and owners[0].reclaimed

    asyncio.run(scenario())


def test_actual_binary_subscription_and_barriers_drain_queued_lifecycle_events_on_one_connection():
    class LeasePeer(Peer):
        async def sock_sendall(self, connection, packet):
            if self.authenticated and packet[0] == 43:
                self.sequence += 1
                self.sent.append((43, 0, packet[4:]))
                self.incoming.extend(reply(self.sequence))
                return
            if self.authenticated and packet[0] == 2 and struct.unpack("<III", packet[4:]) == (WINDOW, 1 << 11, 0):
                self.sequence += 1
                self.sent.append((2, 0, packet[4:]))
                return
            await super().sock_sendall(connection, packet)

    async def scenario():
        connection = SimpleNamespace(checked=True, closed=False)
        protocol = LeaseWire(connection, lambda: None)
        peer = LeasePeer()
        protocol.loop = peer
        await protocol.authenticate(COOKIE, 0)
        await protocol.subscribe(WINDOW)
        assert protocol.generations == {WINDOW: 0}
        peer.incoming.extend(event(17, WINDOW))
        await protocol.request(43)
        assert protocol.generations == {WINDOW: 1}
        peer.incoming.extend(event(22, WINDOW))
        await protocol.unsubscribe(WINDOW)
        assert protocol.generations == {} and not peer.incoming
        selections = [struct.unpack("<III", body) for opcode, _minor, body in peer.sent if opcode == 2]
        assert selections == [(WINDOW, 1 << 11, 1 << 17), (WINDOW, 1 << 11, 0)]

    asyncio.run(scenario())
