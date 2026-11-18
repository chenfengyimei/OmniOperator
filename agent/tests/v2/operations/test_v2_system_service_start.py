"""Reviewed system startup through owner/kernel contracts and injected root transport, never actual root or services."""

from __future__ import annotations

from omni_test_support.v2.system_service_start import (
    MAX_REQUESTS as MAX_REQUESTS,
)
from omni_test_support.v2.system_service_start import (
    SHA as SHA,
)
from omni_test_support.v2.system_service_start import (
    VERSION as VERSION,
)
from omni_test_support.v2.system_service_start import (
    Boot as Boot,
)
from omni_test_support.v2.system_service_start import (
    BrokerWriter as BrokerWriter,
)
from omni_test_support.v2.system_service_start import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.system_service_start import (
    Clock as Clock,
)
from omni_test_support.v2.system_service_start import (
    Commands as Commands,
)
from omni_test_support.v2.system_service_start import (
    Connection as Connection,
)
from omni_test_support.v2.system_service_start import (
    Deployments as Deployments,
)
from omni_test_support.v2.system_service_start import (
    Files as Files,
)
from omni_test_support.v2.system_service_start import (
    Policy as Policy,
)
from omni_test_support.v2.system_service_start import (
    PrivilegedServiceClient as PrivilegedServiceClient,
)
from omni_test_support.v2.system_service_start import (
    ServiceControl as ServiceControl,
)
from omni_test_support.v2.system_service_start import (
    StartCommands as StartCommands,
)
from omni_test_support.v2.system_service_start import (
    StartPolicy as StartPolicy,
)
from omni_test_support.v2.system_service_start import (
    SystemClock as SystemClock,
)
from omni_test_support.v2.system_service_start import (
    UUIDs as UUIDs,
)
from omni_test_support.v2.system_service_start import (
    admin_operation as admin_operation,
)
from omni_test_support.v2.system_service_start import (
    asyncio as asyncio,
)
from omni_test_support.v2.system_service_start import (
    backend as backend,
)
from omni_test_support.v2.system_service_start import (
    client as client,
)
from omni_test_support.v2.system_service_start import (
    composition as composition,
)
from omni_test_support.v2.system_service_start import (
    contextmanager as contextmanager,
)
from omni_test_support.v2.system_service_start import (
    control as control,
)
from omni_test_support.v2.system_service_start import (
    copy as copy,
)
from omni_test_support.v2.system_service_start import (
    decode as decode,
)
from omni_test_support.v2.system_service_start import (
    encode as encode,
)
from omni_test_support.v2.system_service_start import (
    json as json,
)
from omni_test_support.v2.system_service_start import (
    make_control as make_control,
)
from omni_test_support.v2.system_service_start import (
    manager_identity as manager_identity,
)
from omni_test_support.v2.system_service_start import (
    parser as parser,
)
from omni_test_support.v2.system_service_start import (
    plan as plan,
)
from omni_test_support.v2.system_service_start import (
    pytest as pytest,
)
from omni_test_support.v2.system_service_start import (
    request as request,
)
from omni_test_support.v2.system_service_start import (
    response as response,
)
from omni_test_support.v2.system_service_start import (
    service_admin_client as service_admin_client,
)
from omni_test_support.v2.system_service_start import (
    service_admin_policy as service_admin_policy,
)
from omni_test_support.v2.system_service_start import (
    socket_path as socket_path,
)
from omni_test_support.v2.system_service_start import (
    start_postcondition as start_postcondition,
)
from omni_test_support.v2.system_service_start import (
    systemd as systemd,
)
from omni_test_support.v2.system_service_start import (
    validate_request as validate_request,
)
from omni_test_support.v2.system_service_start import (
    validate_response as validate_response,
)


def test_root_protocol_accepts_only_exact_system_start_and_keeps_registration_separate():
    value = control()
    assert admin_operation(value) == value
    first = request(value, "change", 0, UUIDs().new(), SHA)
    assert first["format"] == VERSION == "omni-service-admin-4"
    assert validate_request(decode(encode(first)), value, 0, SHA) == first
    changed = copy.deepcopy(value)
    changed["boot_id"] = UUIDs().new()
    with pytest.raises(BusinessError):
        admin_operation(changed)
    with pytest.raises(BusinessError):
        admin_operation(control("stop"))
    for target in ("linux-user", "windows-user", "macos-user"):
        with pytest.raises(BusinessError):
            admin_operation(control(target=target))
    with pytest.raises(BusinessError):
        validate_request({**first, "command": "start", "argv": ["arbitrary"]}, value, 0, SHA)


def test_root_start_records_before_submission_and_never_claims_boot_readiness():
    policy = StartPolicy()
    commands = StartCommands(policy)
    service = backend.SystemServiceAdministrator(policy, commands)
    assert service.change() == {"effect": "applied", "error_code": ""}
    assert len([argv for argv in commands.calls if argv[2] == "start"]) == 1
    assert [phase for phase, _ in policy.records] == ["dispatch", "outcome"]
    assert all(value["acceptance_only"] is True and "ready" not in value for _, value in policy.records)
    assert not {"publish", "remove"} & set(policy.calls)
    count = len(commands.calls)
    assert service.change()["error_code"] == "service_admin_already_dispatched"
    assert len(commands.calls) == count


@pytest.mark.parametrize(
    "failure", ["foreign", "disabled", "missing", "running", "dropins", "audit", "second_drift", "start", "returncode"]
)
def test_root_start_denial_or_unknown_preserves_dispatch_and_never_replays(failure):
    policy = StartPolicy()
    commands = StartCommands(policy)
    if failure == "foreign":
        policy.drift = True
    elif failure == "disabled":
        policy.enabled = False
    elif failure == "missing":
        policy.installed = False
    elif failure == "running":
        commands.running = True
    elif failure == "dropins":
        policy.reject_paths = True
    elif failure == "audit":
        policy.audit_fail = True
    elif failure == "second_drift":
        policy.second_drift = True
    elif failure == "start":
        commands.fail = "start"
    else:
        commands.start_code = 1
    result = backend.SystemServiceAdministrator(policy, commands).change()
    assert result["effect"] == ("unknown" if failure in {"start", "returncode"} else "none")
    assert "credential" not in json.dumps(result)
    assert len([argv for argv in commands.calls if argv[2] == "start"]) == (1 if policy.dispatched_once else 0)
    if policy.dispatched_once:
        count = len(commands.calls)
        assert (
            backend.SystemServiceAdministrator(policy, commands).change()["error_code"]
            == "service_admin_already_dispatched"
        )
        assert len(commands.calls) == count


@pytest.mark.parametrize("fault", ["none", "not_ready", "wrong_pid", "wrong_boot", "cleanup"])
def test_owner_kernel_requires_reserved_boot_root_pid_and_actual_cleanup(monkeypatch, fault):
    async def scenario():
        policy = StartPolicy()
        commands = StartCommands(policy)
        value = policy.operation
        files = Files(value)
        boot = Boot(files, commands)
        if fault == "not_ready":
            boot.ready = False
        if fault == "wrong_pid":
            commands.pid = 99
        if fault == "wrong_boot":
            boot.wrong_boot = True
        if fault == "cleanup":
            commands.cleanup = False
        native, writer = client(monkeypatch, policy, commands)
        service = ServiceControl(Deployments(value["deployment"]), files, native, boot, Clock(), UUIDs())
        result = await service.apply(value, value["sha256"])
        assert files.reserved and len([argv for argv in commands.calls if argv[2] == "start"]) == 1
        assert sum(row["command"] == "change" for row in writer.requests) == 1
        if fault in {"not_ready", "wrong_pid", "wrong_boot"}:
            assert result["state"] == "unknown" and files.active
        else:
            assert result["state"] == "succeeded"
        if fault == "cleanup":
            assert result["cleanup_complete"] is False and files.owned and files.active
            assert not await service.close(0)
            commands.cleanup = True
        assert await service.close(1)
        if fault in {"not_ready", "wrong_pid", "wrong_boot"}:
            boot.ready, boot.wrong_boot, commands.pid = True, False, 42
            native, observed = client(monkeypatch, policy, commands)
            review = ServiceControl(Deployments(value["deployment"]), files, native, boot, Clock(), UUIDs())
            assert (await review.apply(value, "", reconcile=True))["state"] == "succeeded"
            assert all(row["command"] != "change" for row in observed.requests)
            assert await review.close(1)
        assert not files.active and not files.owned

    asyncio.run(scenario())


def test_transport_reserves_last_frame_for_close_and_cannot_change_control_as_registration(monkeypatch):
    async def scenario():
        policy = StartPolicy()
        native, writer = client(monkeypatch, policy, StartCommands(policy))
        for _ in range(MAX_REQUESTS - 1):
            assert (await native.inspect(policy.plan))["installed"] is True
        assert (await native.inspect(policy.plan))["error_code"] == "service_admin_request_limit"
        assert len(writer.requests) == MAX_REQUESTS - 1
        with pytest.raises(BusinessError):
            await native.change(policy.plan, "start")
        assert await native.close(1)
        assert len(writer.requests) == MAX_REQUESTS and writer.requests[-1]["command"] == "close"

    asyncio.run(scenario())


def test_root_close_uses_one_frame_until_actual_cleanup(monkeypatch):
    policy = StartPolicy()
    value = request(policy.operation, "close", 0, UUIDs().new(), SHA)
    connection = Connection([encode(value)])
    monkeypatch.setattr(backend.select, "select", lambda descriptors, *args: (descriptors, [], []))
    monkeypatch.setattr(backend, "connected", lambda connection: True)
    service = backend.SystemServiceAdministrator(policy, StartCommands(policy))
    reports = iter((False, False, True))
    monkeypatch.setattr(service, "close", lambda timeout: next(reports))
    backend.serve_connection(connection, service, SHA)
    assert len(connection.replies) == 1
    assert validate_response(connection.replies[0], value) == {"closed": True, "cleanup_pending": False}


@pytest.mark.parametrize(
    "state,pid,available,enabled",
    [
        ("unknown", 42, True, True),
        ("running", None, True, True),
        ("running", True, True, True),
        ("running", 42, False, True),
        ("running", 42, True, False),
    ],
)
def test_system_start_cannot_accept_ambiguous_manager_evidence(state, pid, available, enabled):
    deployment = plan("linux-system")
    native = {
        "installed": True,
        "definition_present": True,
        "definition_matches": True,
        "available": available,
        "enabled": enabled,
        "state": state,
        "pid": pid,
    }
    assert not start_postcondition(deployment, native, {"pid": 42})


def test_cli_requires_explicit_root_endpoint_and_bundle_for_existing_control():
    value = control()
    args = parser().parse_args(
        [
            "service",
            "apply-control",
            "control.json",
            "--approve",
            value["sha256"],
            "--admin-socket",
            socket_path(value),
            "--admin-bundle-sha256",
            SHA,
        ]
    )
    assert args.admin_bundle_sha256 == SHA
    with pytest.raises(SystemExit):
        parser().parse_args(["service", "apply-control", "control.json", "--admin-socket", socket_path(value)])


@pytest.mark.parametrize("command", ["apply-control", "reconcile-control"])
def test_composition_uses_only_explicit_pinned_broker_and_owns_cleanup(monkeypatch, tmp_path, command):
    value, events = control(), []
    native = object()

    class DeploymentFiles:
        def read(self, path):
            assert path == tmp_path / "control.json"
            return value

    def administrator(operation, endpoint, broker_sha256):
        assert operation == value and endpoint == str((tmp_path / "broker.sock").absolute())
        assert broker_sha256 == SHA
        events.append("explicit_root_client")
        return native

    class Application:
        def __init__(self, deployments, files, selected, boot, clock, ids):
            assert selected is native

        async def apply(self, operation, approval, **options):
            assert operation == value and approval == value["sha256"]
            assert options == {"reconcile": command == "reconcile-control", "cancel": False}
            return {"state": "unknown"}

        async def close(self, timeout):
            events.append("actual_cleanup")
            return events.count("actual_cleanup") == 2

    monkeypatch.setattr(composition, "CurrentDeploymentFiles", DeploymentFiles)
    monkeypatch.setattr(composition, "ServiceControl", Application)
    monkeypatch.setattr(composition, "ServiceRegistration", lambda: pytest.fail("No implicit local fallback"))
    monkeypatch.setattr(service_admin_client, "PrivilegedServiceClient", administrator)
    result = asyncio.run(
        composition.run_control(
            command,
            tmp_path / "control.json",
            approval=value["sha256"],
            admin_socket=tmp_path / "broker.sock",
            admin_bundle_sha256=SHA,
        )
    )
    assert result["state"] == "unknown" and events == ["explicit_root_client", "actual_cleanup", "actual_cleanup"]


@pytest.mark.parametrize("case", ["missing_pin", "missing_socket", "stop", "user", "inspect", "cancel"])
def test_composition_rejects_incomplete_or_out_of_scope_root_route_before_connection(monkeypatch, tmp_path, case):
    value = control("stop" if case == "stop" else "start", "linux-user" if case == "user" else "linux-system")

    class DeploymentFiles:
        def read(self, path):
            return value

    monkeypatch.setattr(composition, "CurrentDeploymentFiles", DeploymentFiles)
    monkeypatch.setattr(
        service_admin_client, "PrivilegedServiceClient", lambda *args: pytest.fail("No root connection")
    )
    with pytest.raises(BusinessError):
        asyncio.run(
            composition.run_control(
                "inspect-control" if case == "inspect" else "cancel-control" if case == "cancel" else "apply-control",
                tmp_path / "control.json",
                approval=value["sha256"],
                admin_socket=None if case == "missing_socket" else tmp_path / "broker.sock",
                admin_bundle_sha256=None if case == "missing_pin" else SHA,
            )
        )


def test_root_start_audit_carries_exact_boot_and_no_application_data(monkeypatch, tmp_path):
    value, captured = control(), []
    policy = service_admin_policy.RootServicePolicy(dict(value), SHA)
    monkeypatch.setattr(service_admin_policy, "AUDIT_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(policy, "authorize", lambda: None)
    monkeypatch.setattr(service_admin_policy, "publish", lambda path, data: captured.append((path, json.loads(data))))
    policy.record("dispatch", {"effect": "unknown", "acceptance_only": True})
    path, row = captured[0]
    assert path == tmp_path / value["id"] / "dispatch.json" and row["format"] == "omni-service-admin-audit-4"
    assert row["boot_id"] == value["boot_id"] and row["operation_sha256"] == value["sha256"]
    assert set(row) == {
        "format",
        "approval_id",
        "approval_sha256",
        "administrator_role",
        "exchange_sha256",
        "operation_key",
        "operation_id",
        "operation_sha256",
        "instance_id",
        "owner_uid",
        "root_uid",
        "broker_sha256",
        "action",
        "boot_id",
        "phase",
        "recorded_at",
        "result",
    }


def test_close_slot_is_enforced_at_root_and_disconnected_cleanup_does_not_claim_completion(monkeypatch):
    policy = StartPolicy()
    with pytest.raises(BusinessError):
        request(policy.operation, "inspect", MAX_REQUESTS - 1, UUIDs().new(), SHA)
    value = request(policy.operation, "close", 0, UUIDs().new(), SHA)
    connection = Connection([encode(value)])
    monkeypatch.setattr(backend.select, "select", lambda descriptors, *args: (descriptors, [], []))
    monkeypatch.setattr(backend, "connected", lambda connection: False)
    commands = StartCommands(policy)
    commands.cleanup = False
    service = backend.SystemServiceAdministrator(policy, commands)
    backend.serve_connection(connection, service, SHA)
    assert connection.replies == [] and not service.close(0)
    commands.cleanup = True
    assert service.close(0)
