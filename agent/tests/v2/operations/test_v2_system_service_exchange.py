"""System exchange through the actual owner kernel and root command backend with injected IPC/native IO; no root or services."""

from __future__ import annotations

from omni_test_support.v2.system_service_exchange import (
    FENCE_FILES as FENCE_FILES,
)
from omni_test_support.v2.system_service_exchange import (
    MAX_REQUESTS as MAX_REQUESTS,
)
from omni_test_support.v2.system_service_exchange import (
    SHA as SHA,
)
from omni_test_support.v2.system_service_exchange import (
    VERSION as VERSION,
)
from omni_test_support.v2.system_service_exchange import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.system_service_exchange import (
    Clock as Clock,
)
from omni_test_support.v2.system_service_exchange import (
    Commands as Commands,
)
from omni_test_support.v2.system_service_exchange import (
    Connection as Connection,
)
from omni_test_support.v2.system_service_exchange import (
    CurrentDeploymentFiles as CurrentDeploymentFiles,
)
from omni_test_support.v2.system_service_exchange import (
    Deployments as Deployments,
)
from omni_test_support.v2.system_service_exchange import (
    ExchangeFiles as ExchangeFiles,
)
from omni_test_support.v2.system_service_exchange import (
    ExchangeSteps as ExchangeSteps,
)
from omni_test_support.v2.system_service_exchange import (
    Files as Files,
)
from omni_test_support.v2.system_service_exchange import (
    InstanceLock as InstanceLock,
)
from omni_test_support.v2.system_service_exchange import (
    InstancePaths as InstancePaths,
)
from omni_test_support.v2.system_service_exchange import (
    Path as Path,
)
from omni_test_support.v2.system_service_exchange import (
    Policy as Policy,
)
from omni_test_support.v2.system_service_exchange import (
    ServiceControl as ServiceControl,
)
from omni_test_support.v2.system_service_exchange import (
    ServiceExchange as ServiceExchange,
)
from omni_test_support.v2.system_service_exchange import (
    ServiceOperations as ServiceOperations,
)
from omni_test_support.v2.system_service_exchange import (
    SystemClock as SystemClock,
)
from omni_test_support.v2.system_service_exchange import (
    UUIDs as UUIDs,
)
from omni_test_support.v2.system_service_exchange import (
    Writer as Writer,
)
from omni_test_support.v2.system_service_exchange import (
    admin_scope as admin_scope,
)
from omni_test_support.v2.system_service_exchange import (
    asyncio as asyncio,
)
from omni_test_support.v2.system_service_exchange import (
    boot as boot,
)
from omni_test_support.v2.system_service_exchange import (
    bound_operation as bound_operation,
)
from omni_test_support.v2.system_service_exchange import (
    cleanup_composition as cleanup_composition,
)
from omni_test_support.v2.system_service_exchange import (
    composition as composition,
)
from omni_test_support.v2.system_service_exchange import (
    contextmanager as contextmanager,
)
from omni_test_support.v2.system_service_exchange import (
    copy as copy,
)
from omni_test_support.v2.system_service_exchange import (
    decode as decode,
)
from omni_test_support.v2.system_service_exchange import (
    deployment_request as deployment_request,
)
from omni_test_support.v2.system_service_exchange import (
    encode as encode,
)
from omni_test_support.v2.system_service_exchange import (
    json as json,
)
from omni_test_support.v2.system_service_exchange import (
    kernel as kernel,
)
from omni_test_support.v2.system_service_exchange import (
    launch as launch,
)
from omni_test_support.v2.system_service_exchange import (
    make_cleanup as make_cleanup,
)
from omni_test_support.v2.system_service_exchange import (
    make_control as make_control,
)
from omni_test_support.v2.system_service_exchange import (
    make_exchange as make_exchange,
)
from omni_test_support.v2.system_service_exchange import (
    make_isolation as make_isolation,
)
from omni_test_support.v2.system_service_exchange import (
    make_operation as make_operation,
)
from omni_test_support.v2.system_service_exchange import (
    make_plan as make_plan,
)
from omni_test_support.v2.system_service_exchange import (
    make_release as make_release,
)
from omni_test_support.v2.system_service_exchange import (
    manager_identity as manager_identity,
)
from omni_test_support.v2.system_service_exchange import (
    marker as marker,
)
from omni_test_support.v2.system_service_exchange import (
    parser as parser,
)
from omni_test_support.v2.system_service_exchange import (
    plan as plan,
)
from omni_test_support.v2.system_service_exchange import (
    progress as progress,
)
from omni_test_support.v2.system_service_exchange import (
    pytest as pytest,
)
from omni_test_support.v2.system_service_exchange import (
    reference as reference,
)
from omni_test_support.v2.system_service_exchange import (
    registration_goal as registration_goal,
)
from omni_test_support.v2.system_service_exchange import (
    render as render,
)
from omni_test_support.v2.system_service_exchange import (
    request as request,
)
from omni_test_support.v2.system_service_exchange import (
    response as response,
)
from omni_test_support.v2.system_service_exchange import (
    root as root,
)
from omni_test_support.v2.system_service_exchange import (
    runtime as runtime,
)
from omni_test_support.v2.system_service_exchange import (
    scoped_operations as scoped_operations,
)
from omni_test_support.v2.system_service_exchange import (
    session as session,
)
from omni_test_support.v2.system_service_exchange import (
    socket_path as socket_path,
)
from omni_test_support.v2.system_service_exchange import (
    startup as startup,
)
from omni_test_support.v2.system_service_exchange import (
    systemd as systemd,
)
from omni_test_support.v2.system_service_exchange import (
    transport as transport,
)
from omni_test_support.v2.system_service_exchange import (
    validate_request as validate_request,
)
from omni_test_support.v2.system_service_exchange import (
    validate_response as validate_response,
)


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
