"""Reviewed system startup through owner/kernel contracts and injected root transport, never actual root or services."""

from __future__ import annotations

import asyncio
import copy
import json
from contextlib import contextmanager

import pytest
from test_v2_service_admin import SHA, Commands, Policy
from test_v2_service_admin_ownership import Connection
from test_v2_service_control import Clock
from test_v2_service_deployment import plan
from test_v2_service_query import systemd

from omni.adapters.platform import service_admin as backend
from omni.adapters.platform import service_admin_client, service_admin_policy
from omni.adapters.platform.service_admin_client import PrivilegedServiceClient
from omni.adapters.primitives import SystemClock, UUIDs
from omni.application.service_control import ServiceControl
from omni.composition import service_control as composition
from omni.composition.cli import parser
from omni.domain.contracts import BusinessError
from omni.domain.service_admin import (
    MAX_REQUESTS,
    VERSION,
    admin_operation,
    decode,
    encode,
    request,
    response,
    socket_path,
    validate_request,
    validate_response,
)
from omni.domain.service_control import make_control, start_postcondition
from omni.domain.service_deployment import manager_identity


def control(action="start", target="linux-system"):
    return make_control(plan(target), action, UUIDs().new(), SystemClock().now(), UUIDs().new())


class StartPolicy(Policy):
    def __init__(self, value=None):
        super().__init__(installed=True)
        self.operation = control() if value is None else value
        self.approval = self.operation
        self.plan = self.operation["deployment"]
        self.manager = manager_identity(self.plan)
        self.records = []
        self.audit_fail = self.second_drift = False

    def record(self, phase, result):
        if self.audit_fail:
            raise OSError("credential=must not leave the root boundary")
        super().record(phase, result)
        self.records.append((phase, dict(result)))


class StartCommands(Commands):
    def __init__(self, policy):
        super().__init__(policy)
        self.start_code = 0
        self.pid = 42
        self.unit_queries = 0

    def run(self, argv):
        if argv[2] != "start":
            result = super().run(argv)
            if argv[2] == "show" and "--property=UnitPath" not in argv:
                self.unit_queries += 1
                if self.running:
                    result = (
                        result[0],
                        systemd(
                            self.policy.plan,
                            MainPID=str(self.pid),
                            ActiveState="active",
                            SubState="running",
                            FragmentPath=self.policy.manager["installed_path"],
                        ),
                    )
            if "--property=UnitPath" in argv and self.policy.second_drift:
                self.policy.drift = True
            return result
        self.calls.append(argv)
        assert self.policy.dispatched_once
        assert argv == [
            "/fixed/root/systemctl",
            "--system",
            "start",
            self.policy.manager["name"],
            "--no-block",
            "--job-mode=fail",
            "--no-ask-password",
        ]
        self.running = True
        if self.fail == "start":
            raise OSError("credential=must not leave the root boundary")
        return self.start_code, b"not an application readiness proof"


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


class BrokerWriter:
    def __init__(self, reader, administrator):
        self.reader, self.administrator = reader, administrator
        self.requests = []

    def write(self, payload):
        value = validate_request(decode(payload), self.administrator.policy.operation, len(self.requests), SHA)
        self.requests.append(value)
        if value["command"] == "inspect":
            result = self.administrator.inspect()
        elif value["command"] == "change":
            result = self.administrator.change()
        else:
            closed = self.administrator.close(0)
            result = {"closed": closed, "cleanup_pending": not closed}
        self.reader.feed_data(encode(response(value, result)))

    async def drain(self):
        pass

    def close(self):
        self.reader.feed_eof()

    async def wait_closed(self):
        pass


def client(monkeypatch, policy, commands):
    value = policy.operation
    native = PrivilegedServiceClient(dict(value), socket_path(value), SHA)
    reader = asyncio.StreamReader()
    writer = BrokerWriter(reader, backend.SystemServiceAdministrator(policy, commands))

    async def connect():
        native._reader, native._writer = reader, writer

    monkeypatch.setattr(native, "_connect", connect)
    return native, writer


class Files:
    def __init__(self, operation):
        self.operation, self.rows = operation, []
        self.owned = self.active = self.reserved = False

    @contextmanager
    def exclusive(self, operation):
        assert operation == self.operation and not self.owned
        self.owned = True
        try:
            yield
        finally:
            self.owned = False

    def latest(self, operation):
        return dict(self.rows[-1]) if self.rows else None

    def activate(self, operation):
        self.active = True

    def append(self, operation, result):
        self.rows.append(dict(result))

    def dispatched(self, operation):
        return any(row["state"] == "dispatch" for row in self.rows)

    def reserve(self, operation):
        assert self.owned and self.dispatched(operation)
        self.reserved = True

    def finish_launch(self, operation):
        assert self.reserved

    def settled(self, operation):
        self.active = False


class Deployments:
    def __init__(self, deployment):
        self.deployment = deployment

    def verify(self, bundle):
        return {"valid": True, "eligible": True, "plan": self.deployment}


class Boot:
    def __init__(self, files, commands):
        self.files, self.commands = files, commands
        self.ready = True
        self.wrong_boot = False

    async def probe(self, deployment):
        return {
            "ready": self.ready and self.commands.running,
            "pid": 42,
            "boot_id": UUIDs().new() if self.wrong_boot else self.files.operation["boot_id"],
            "error_code": None,
        }

    async def close(self, timeout_s):
        return True

    async def drain(self, operation):
        pytest.fail("A root start cannot dispatch any stop")


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
