"""System exchange through the actual owner kernel and root command backend with injected IPC/native IO; no root or services."""

from __future__ import annotations

import asyncio
import copy
import json
from contextlib import contextmanager
from pathlib import Path

import pytest
from test_v2_service_admin import SHA
from test_v2_service_admin_ownership import Connection
from test_v2_service_control import Clock
from test_v2_service_deployment import request as deployment_request
from test_v2_service_deployment import runtime
from test_v2_service_query import systemd
from test_v2_system_service_start import Files

from omni.adapters.platform.services import service_admin as root
from omni.adapters.platform.services import service_admin_client as transport
from omni.adapters.platform.services import service_launch as launch
from omni.adapters.platform.services.deployment_files import CurrentDeploymentFiles
from omni.adapters.platform.runtime.paths import InstanceLock, InstancePaths
from omni.adapters.platform.services.service_templates import render
from omni.adapters.primitives import SystemClock, UUIDs
from omni.adapters.workers import service_admin_boot as boot
from omni.application.exchange_steps import ExchangeSteps
from omni.application.service_control import ServiceControl
from omni.application.service_exchange import ServiceExchange
from omni.application.service_operations import ServiceOperations
from omni.composition import service_cleanup as cleanup_composition
from omni.composition import service_exchange as composition
from omni.composition.cli import parser
from omni.domain.contracts import BusinessError
from omni.domain.releases import FENCE_FILES, make_release
from omni.domain.service_admin import (
    MAX_REQUESTS,
    VERSION,
    admin_scope,
    bound_operation,
    decode,
    encode,
    request,
    response,
    scoped_operations,
    socket_path,
    validate_request,
    validate_response,
)
from omni.domain.service_cleanup import make_cleanup
from omni.domain.service_control import make_control
from omni.domain.service_deployment import make_plan, manager_identity, startup
from omni.domain.service_exchange import make_exchange, marker, progress
from omni.domain.service_isolation import make_isolation, reference
from omni.domain.service_operations import make_operation, registration_goal


def plan(target="linux-system"):
    ids, at = UUIDs(), SystemClock().now()
    captured = runtime(target)
    captured["files"] += [{"path": name, "sha256": "e" * 64, "bytes": 100} for name in FENCE_FILES]
    old = make_plan(deployment_request(target), captured, ids.new(), at)
    newer = json.loads(json.dumps(captured).replace("/opt/versions/v2/venv", "/opt/versions/next/venv"))
    supplied = deployment_request(target)
    for key in ("home", "bundle_dir"):
        supplied[key] += "-next"
    new = make_plan(supplied, newer, ids.new(), at)
    release = make_release(old, new, {"backup": "/tmp/snapshot", "journal": "/tmp/release-journal"}, ids.new(), at)
    return make_exchange(
        release,
        make_operation(old, "uninstall", ids.new(), at),
        make_operation(new, "install", ids.new(), at),
        make_control(new, "start", ids.new(), at, ids.new()),
        "/tmp/exchange-journal",
        ids.new(),
        at,
    )


class Policy:
    def close(self):
        self.native_lock_released = True

    def __init__(self, value):
        self.approval = admin_scope(value)
        self.audit, self.records = {}, []
        self.registry = {manager_identity(value["release"]["source"])["name"]: value["release"]["source"]}
        self.enabled = True
        self.select("remove_source")

    def select(self, key):
        self.operation_key, self.operation = key, bound_operation(self.approval, key)
        self.plan = self.operation["deployment"]
        self.manager = manager_identity(self.plan)

    def authorize(self):
        pass

    def program(self):
        return "/fixed/root/systemctl"

    def definition(self):
        value = self.registry.get(self.manager["name"])
        return value is not None, None if value is None else render(value)["omni.service"]

    def reject_dropins(self, _):
        pass

    def dispatched(self):
        return self.operation["id"] in self.audit

    def record(self, phase, value):
        if phase == "dispatch":
            assert not self.dispatched()
            self.audit[self.operation["id"]] = self.operation["sha256"]
        self.records.append((self.operation_key, phase, dict(value)))

    def publish_definition(self):
        assert self.dispatched() and self.manager["name"] not in self.registry
        self.registry[self.manager["name"]] = self.plan

    def remove_definition(self):
        assert self.dispatched() and self.registry[self.manager["name"]] == self.plan
        self.registry.pop(self.manager["name"])


class Commands:
    def __init__(self, policy):
        self.policy, self.calls = policy, []
        self.running, self.cleanup = False, True
        self.fail_enable = False
        self.control_files = None

    def run(self, argv):
        assert argv[:2] == ["/fixed/root/systemctl", "--system"]
        self.calls.append(list(argv))
        action = argv[2]
        policy, value = self.policy, self.policy.plan
        if action == "show":
            if "--property=UnitPath" in argv:
                return 0, b"/etc/systemd/system /usr/lib/systemd/system\n"
            present = policy.manager["name"] in policy.registry
            return (0 if present else 1), systemd(
                value,
                LoadState="loaded" if present else "not-found",
                FragmentPath=policy.manager["installed_path"] if present else "",
                ActiveState="active" if self.running else "inactive",
                SubState="running" if self.running else "dead",
                MainPID="42" if self.running else "0",
            )
        if action == "is-enabled":
            if policy.manager["name"] not in policy.registry:
                return 1, b"not-found\n"
            return (0, b"enabled\n") if policy.enabled else (1, b"disabled\n")
        assert policy.dispatched()
        if action == "enable":
            policy.enabled = True
            if self.fail_enable:
                return 1, b"injected accepted but unverified"
        elif action == "disable":
            policy.enabled = False
        elif action == "start":
            assert self.control_files.reserved and "--no-block" in argv and "--job-mode=fail" in argv
            self.running = True
        else:
            assert action == "daemon-reload"
        return 0, b""

    def close(self, timeout):
        return self.cleanup


class Writer:
    def __init__(self, reader, administrator, fingerprint=SHA):
        self.reader, self.administrator = reader, administrator
        self.fingerprint = fingerprint
        self.requests, self.pending = [], None
        self.closed = self.half_closed = self.corrupt = False
        self.hold = None
        self.held = asyncio.Event()

    def write(self, payload):
        value = validate_request(
            decode(payload), self.administrator.policy.approval, len(self.requests), self.fingerprint
        )
        self.requests.append(value)
        if value["command"] != "close":
            self.administrator.policy.select(value["operation_key"])
        command = value["command"]
        if command == "inspect":
            report = self.administrator.inspect()
        elif command == "change":
            report = self.administrator.change()
        else:
            settled = self.administrator.close(0)
            if settled and command == "close":
                self.administrator.policy.close()
            report = {"settled" if command == "settle" else "closed": settled, "cleanup_pending": not settled}
        self.pending = (value, report)
        if command == self.hold or (command in {"settle", "close"} and report["cleanup_pending"]):
            self.held.set()
        else:
            self.reply()

    def reply(self):
        value, report = self.pending
        if value["command"] in {"settle", "close"}:
            settled = self.administrator.close(0)
            assert settled  # Production keeps the same request until actual root cleanup.
            report = {"settled" if value["command"] == "settle" else "closed": True, "cleanup_pending": False}
        packet = response(value, report)
        if self.corrupt:
            packet["scope_sha256"] = "b" * 64
        self.reader.feed_data(encode(packet))
        self.pending = None

    async def drain(self):
        pass

    def can_write_eof(self):
        return True

    def write_eof(self):
        self.half_closed = True

    def close(self):
        self.closed = True
        self.reader.feed_eof()

    async def wait_closed(self):
        pass


def session(monkeypatch, policy, commands):
    parent = transport.ExchangeAdministrator(policy.approval, socket_path(policy.approval), SHA)
    reader = asyncio.StreamReader()
    writer = Writer(reader, root.SystemServiceAdministrator(policy, commands))

    async def connect():
        parent._reader, parent._writer = reader, writer

    monkeypatch.setattr(parent, "_connect", connect)
    return parent, writer


class ExchangeFiles:
    def __init__(self):
        self.rows, self.owned, self.active = [], False, False

    @contextmanager
    def exclusive(self, value):
        assert not self.owned
        self.owned = True
        try:
            yield
        finally:
            self.owned = False

    def history(self, _):
        return list(self.rows)

    def claim(self, _):
        self.active = True

    def append(self, value, record):
        progress(value, [*self.rows, record])
        self.rows.append(record)

    def settled(self, _):
        self.active = False


class Deployments:
    def __init__(self, value):
        self.value = value

    def verify(self, bundle):
        exact = next(
            item
            for item in self.value["release"].values()
            if isinstance(item, dict) and item.get("request", {}).get("bundle_dir") == bundle
        )
        return {"valid": True, "eligible": True, "plan": exact}


def kernel(value, parent, commands, exchange_files=None, journals=None, wrong_boot=False):
    files = ExchangeFiles() if exchange_files is None else exchange_files
    journals = (
        {key: Files(value[key]) for key in ("remove_source", "install_target", "start_target")}
        if journals is None
        else journals
    )
    commands.control_files = journals["start_target"]
    source, target, query, start = (
        parent.lease(key, read_only=ro)
        for key, ro in (
            ("remove_source", False),
            ("install_target", False),
            ("remove_source", True),
            ("start_target", False),
        )
    )

    class Release:
        files = type("ReleaseFiles", (), {"installations": lambda *args: None})()

        async def select(self, release, approval):
            assert approval == release["sha256"]
            report = await query.inspect(release["source"])
            if not registration_goal("uninstall", report):
                raise BusinessError("source_present", "Source must be absent")
            assert await query.close(1)
            return {"state": "selected"}

        async def close(self, timeout):
            return await query.close(timeout)

    class Boot:
        wrong = wrong_boot

        async def probe(self, _):
            return {
                "ready": commands.running,
                "pid": 42,
                "boot_id": UUIDs().new() if self.wrong else value["start_target"]["boot_id"],
            }

        async def close(self, timeout):
            return True

    clock, ids, verifier, boot_port = Clock(), UUIDs(), Deployments(value), Boot()
    stages = ExchangeSteps(
        ServiceOperations(verifier, journals["remove_source"], source, clock, ids),
        ServiceOperations(verifier, journals["install_target"], target, clock, ids),
        Release(),
        ServiceControl(verifier, journals["start_target"], start, boot_port, clock, ids),
        owner=parent,
    )
    stages.prepared = lambda _: None  # Immutable/storage proof is injected; existing real-SQLite callers cover it.
    return ServiceExchange(files, stages, clock, ids), files, journals, boot_port


@pytest.mark.parametrize("fault", ["none", "install_unknown", "wrong_boot"])
def test_full_system_exchange_owns_one_root_session_and_resumes_without_native_replay(monkeypatch, fault):
    async def scenario():
        value = plan()
        policy = Policy(value)
        commands = Commands(policy)
        commands.fail_enable = fault == "install_unknown"
        parent, writer = session(monkeypatch, policy, commands)
        assert await parent.probe()
        service, files, journals, _ = kernel(value, parent, commands, wrong_boot=fault == "wrong_boot")
        result = await service.apply(value, value["sha256"])
        if fault != "none":
            assert result["state"] == "blocked" and await service.close(1) and writer.closed
            parent, writer = session(monkeypatch, policy, commands)
            assert await parent.probe()
            resumed, files, journals, _ = kernel(value, parent, commands, files, journals)
            result = await resumed.apply(value, value["sha256"], resume=True)
            service = resumed
        assert result["state"] == "finished" and result["ready_at_completion"] is True
        assert not files.owned and not files.active and writer.closed
        assert all(not journal.active and not journal.owned for journal in journals.values())
        assert {operation["id"] for operation in scoped_operations(value).values()} == set(policy.audit)
        for action in ("disable", "enable", "start"):
            assert sum(argv[2] == action for argv in commands.calls) == 1
        assert sum(row["command"] == "close" for row in writer.requests) == 1
        assert await service.close(1)

    asyncio.run(scenario())


def test_child_settle_keeps_parent_open_and_denies_cross_lease_or_readonly_writes(monkeypatch):
    async def scenario():
        value = plan()
        policy = Policy(value)
        commands = Commands(policy)
        parent, writer = session(monkeypatch, policy, commands)
        first, second = parent.lease("remove_source"), parent.lease("install_target")
        assert (await first.inspect(value["release"]["source"]))["available"] is True
        assert (await second.inspect(value["release"]["target"]))["error_code"] == "service_admin_lease_owned"
        commands.cleanup = False
        assert not await first.close(0) and not writer.closed
        await writer.held.wait()
        assert parent._holder is first and parent._pending is None
        commands.cleanup = True
        writer.reply()
        assert await first.close(1) and not writer.closed
        query = parent.lease("remove_source", read_only=True)
        with pytest.raises(BusinessError):
            await query.change(value["release"]["source"], "uninstall")
        with pytest.raises(BusinessError):
            await second.start(value["release"]["target"])
        assert await query.close(1) and await second.close(1)
        assert await parent.close(1) and writer.closed

    asyncio.run(scenario())


def test_cancelled_waiter_retains_actual_root_rpc_and_parent_exchange_guard(monkeypatch):
    async def scenario():
        value = plan()
        policy = Policy(value)
        commands = Commands(policy)
        parent, writer = session(monkeypatch, policy, commands)
        assert await parent.probe()
        writer.hold = "change"
        service, files, _, _ = kernel(value, parent, commands)
        waiter = asyncio.create_task(service.apply(value, value["sha256"]))
        await writer.held.wait()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not await service.close(0) and files.owned and parent._holder is not None
        writer.hold = None
        writer.reply()
        assert (await service._pending)["state"] == "finished"
        assert await service.close(1) and writer.closed and not files.owned
        assert sum(argv[2] == "disable" for argv in commands.calls) == 1

    asyncio.run(scenario())


def test_root_protocol_binds_whole_scope_and_exact_child_and_keeps_global_close_slot():
    value = plan()
    packet = request(value, "inspect", 0, UUIDs().new(), SHA, operation_key="remove_source")
    assert packet["format"] == VERSION == "omni-service-admin-4"
    assert packet["scope_id"] == value["id"] and packet["operation_id"] == value["remove_source"]["id"]
    assert validate_request(decode(encode(packet)), value, 0, SHA) == packet
    for changed in ({"scope_sha256": "b" * 64}, {"operation_key": "select_target"}, {"operation_id": value["id"]}):
        with pytest.raises(BusinessError):
            validate_request({**packet, **changed}, value, 0, SHA)
    with pytest.raises(BusinessError):
        request(value, "change", 0, UUIDs().new(), SHA)
    with pytest.raises(BusinessError):
        request(value, "settle", MAX_REQUESTS - 1, UUIDs().new(), SHA, operation_key="remove_source")
    close = request(value, "close", MAX_REQUESTS - 1, UUIDs().new(), SHA)
    assert close["operation_id"] == value["id"]


def test_root_connection_continues_after_child_settle_and_rejects_foreign_scope(monkeypatch):
    value = plan()
    policy = Policy(value)
    commands = Commands(policy)
    frames = [
        request(value, command, index, UUIDs().new(), SHA, operation_key=key)
        for index, (command, key) in enumerate(
            (("inspect", "remove_source"), ("settle", "remove_source"), ("inspect", "install_target"), ("close", ""))
        )
    ]
    connection = Connection([encode(frame) for frame in frames])
    monkeypatch.setattr(root.select, "select", lambda descriptors, *args: (descriptors, [], []))
    root.serve_connection(connection, root.SystemServiceAdministrator(policy, commands), SHA)
    replies = [validate_response(reply, frame) for reply, frame in zip(connection.replies, frames, strict=True)]
    assert replies[1] == {"settled": True, "cleanup_pending": False} and replies[3]["closed"] is True


def test_administrator_source_proof_requires_both_immutable_runtimes():
    value = plan()
    record = value["release"]["source"]["runtime"]["files"][0]
    manifest = {"files": [{"source": record["path"], "sha256": record["sha256"], "bytes": record["bytes"]}]}
    boot.validate_sources(manifest, value)
    changed = copy.deepcopy(value)
    changed["release"]["source"]["runtime"]["files"][0]["sha256"] = "b" * 64
    with pytest.raises(ValueError):
        boot.validate_sources(manifest, changed)


@pytest.mark.parametrize("options", [{}, {"admin_socket": Path("/run/incomplete.sock")}, {"admin_bundle_sha256": SHA}])
@pytest.mark.parametrize("target", ["linux-system", "macos-system"])
def test_system_execution_requires_explicit_complete_authorization_before_owner_intent(monkeypatch, options, target):
    value = plan(target)

    class Files:
        def read(self, _):
            return value

    monkeypatch.setattr(composition, "CurrentDeploymentFiles", Files)
    monkeypatch.setattr(composition, "ServiceRegistration", lambda: pytest.fail("No owner fallback for root exchange"))
    with pytest.raises(BusinessError):
        asyncio.run(composition.run_exchange("apply", Path("plan.json"), approval=value["sha256"], **options))
    parsed = parser().parse_args(
        [
            "exchange",
            "apply",
            "plan.json",
            "--approve",
            value["sha256"],
            "--admin-socket",
            socket_path(value),
            "--admin-bundle-sha256",
            SHA,
        ]
    )
    assert parsed.admin_bundle_sha256 == SHA


def test_parent_final_close_retains_exchange_guard_after_all_child_settles(monkeypatch):
    async def scenario():
        value = plan()
        policy = Policy(value)
        commands = Commands(policy)
        parent, writer = session(monkeypatch, policy, commands)
        assert await parent.probe()
        writer.hold = "close"
        service, files, _, _ = kernel(value, parent, commands)
        caller = asyncio.create_task(service.apply(value, value["sha256"]))
        await writer.held.wait()
        assert files.owned and not writer.closed and not await service.close(0)
        writer.hold = None
        writer.reply()
        assert (await caller)["state"] == "finished" and not files.owned and writer.closed
        assert await service.close(1)

    asyncio.run(scenario())


def test_untrusted_parent_reply_cannot_release_lease_or_root_cleanup(monkeypatch):
    async def scenario():
        value = plan()
        policy = Policy(value)
        commands = Commands(policy)
        parent, writer = session(monkeypatch, policy, commands)
        child = parent.lease("remove_source")
        writer.corrupt = True
        assert (await child.inspect(value["release"]["source"]))["available"] is False
        assert not await child.close(1) and not await parent.close(1)
        assert parent._holder is child and writer.half_closed and not writer.closed
        writer.close()  # Fixture-only stream disposal; this is deliberately not a production cleanup proof.

    asyncio.run(scenario())


@pytest.mark.parametrize("ready", [True, False])
@pytest.mark.parametrize("target", ["linux-system", "macos-system"])
def test_composition_pins_full_scope_leases_and_preflights_before_exchange_intent(monkeypatch, ready, target):
    value, events = plan(target), []

    class Files:
        def read(self, _):
            return value

    class Lease:
        async def close(self, timeout):
            return True

    class Parent:
        def __init__(self, scope, endpoint, fingerprint):
            assert scope == value and endpoint.endswith(value["id"] + ".sock") and fingerprint == SHA

        def lease(self, key, *, read_only=False):
            events.append((key, read_only))
            return Lease()

        async def probe(self):
            events.append("probe")
            return ready

        async def close(self, timeout):
            events.append("root_closed")
            return True

    class Application:
        def __init__(self, files, steps, clock, ids):
            self.steps = steps

        async def apply(self, scope, approval, **options):
            assert ready and events[-1] == "probe" and scope == value and approval == value["sha256"]
            events.append("intent")
            return {"state": "finished"}

        async def close(self, timeout):
            return await self.steps.close(timeout)

    monkeypatch.setattr(composition, "CurrentDeploymentFiles", Files)
    monkeypatch.setattr(composition, "ServiceExchange", Application)
    monkeypatch.setattr(transport, "ExchangeAdministrator", Parent)
    monkeypatch.setattr(composition, "ServiceRegistration", lambda: pytest.fail("No owner-native fallback"))

    async def scenario():
        call = composition.run_exchange(
            "apply",
            Path("plan.json"),
            approval=value["sha256"],
            admin_socket=Path(socket_path(value)),
            admin_bundle_sha256=SHA,
        )
        if ready:
            assert (await call)["state"] == "finished"
        else:
            with pytest.raises(BusinessError) as caught:
                await call
            assert caught.value.code == "service_exchange_admin_unavailable" and "intent" not in events
        assert events[:4] == [
            ("remove_source", False),
            ("remove_source", True),
            ("install_target", False),
            ("start_target", False),
        ]
        assert events[-1] == "root_closed"

    asyncio.run(scenario())


@pytest.mark.parametrize("configured", [True, False])
@pytest.mark.parametrize("target", ["linux-system", "macos-system"])
def test_quarantined_system_cleanup_uses_its_separate_uninstall_approval(monkeypatch, configured, target):
    exchange, ids, at = plan(target), UUIDs(), SystemClock().now()
    ref = reference(make_isolation(exchange, 1, "e" * 64, "review", ids.new(), at))
    value = make_cleanup(exchange, 3, "f" * 64, ref, "target", ids.new(), ids.new(), at)
    events = []

    class Files:
        def read(self, _):
            return value

    def client(operation, endpoint, fingerprint):
        assert operation == value["operation"] and fingerprint == SHA
        assert endpoint.endswith(operation["id"] + ".sock")
        events.append("separate_root")
        return object()

    class Application:
        def __init__(self, bound, files, operations):
            assert bound == value

        async def apply(self, approved):
            assert approved == value["sha256"]
            return {"state": "succeeded", "receipt": {"cleanup_complete": True}}

        async def close(self, timeout):
            return True

    monkeypatch.setattr(cleanup_composition, "CurrentDeploymentFiles", Files)
    monkeypatch.setattr(cleanup_composition, "ServiceCleanup", Application)
    monkeypatch.setattr(transport, "PrivilegedServiceClient", client)
    monkeypatch.setattr(cleanup_composition, "ServiceRegistration", lambda: pytest.fail("No implicit root cleanup"))
    options = {"admin_socket": Path(socket_path(value["operation"])), "admin_bundle_sha256": SHA} if configured else {}

    async def scenario():
        call = cleanup_composition.run_cleanup(
            "apply-cleanup", Path("cleanup.json"), approval=value["sha256"], **options
        )
        if configured:
            assert (await call)["state"] == "succeeded" and events == ["separate_root"]
        else:
            with pytest.raises(BusinessError) as caught:
                await call
            assert caught.value.code == "service_cleanup_admin_required" and events == []

    asyncio.run(scenario())


def test_system_autostart_requires_the_exact_exchange_boot_even_after_host_restart(tmp_path, monkeypatch):
    value = plan()
    paths, old = InstancePaths(tmp_path / "target"), InstancePaths(tmp_path / "source")
    paths.initialize()
    old.initialize()
    logical = value["release"]["target"]["request"]["home"]
    monkeypatch.setattr(launch, "Path", lambda text: paths.home if text == logical else Path(text))
    publisher = CurrentDeploymentFiles()
    for home in (paths, old):
        publisher._publish_json(home.control / "service-exchange.json", json.dumps(marker(value)).encode())
    assert value["releases_activation_on_abandon"] is True
    assert marker(value)["activation"]["boot_id"] == value["start_target"]["boot_id"]
    lock = InstanceLock(paths)
    lock.acquire()
    try:
        with pytest.raises(BusinessError) as caught:
            launch.consume_launch(paths.control, None)
        assert caught.value.code == "service_activation_pending"
        unrelated = make_control(value["release"]["target"], "start", UUIDs().new(), SystemClock().now(), UUIDs().new())
        with pytest.raises(BusinessError):
            launch.reserve_launch(paths.control, unrelated)
        launch.reserve_launch(paths.control, value["start_target"])
        assert (
            launch.consume_launch(paths.control, startup(value["release"]["target"]))
            == value["start_target"]["boot_id"]
        )
        with pytest.raises(BusinessError):
            launch.consume_launch(paths.control, startup(value["release"]["target"]))
        with pytest.raises(BusinessError) as caught:
            launch.consume_launch(old.control, startup(value["release"]["source"]))
        assert caught.value.code == "service_activation_source_blocked"
    finally:
        lock.close()
