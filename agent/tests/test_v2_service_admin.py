"""Administrator protocol/artifact/backend using isolated files and injected root IO; never elevates or registers a service."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys

import pytest
from test_v2_service_deployment import plan
from test_v2_service_query import systemd

from omni.adapters.platform import service_admin as backend
from omni.adapters.platform.service_admin_bundle import materialize_admin_bundle
from omni.adapters.platform.service_admin_policy import RootServicePolicy
from omni.adapters.platform.service_templates import render
from omni.adapters.platform.systemd_state import systemd_state
from omni.adapters.workers import service_admin_boot as boot
from omni.composition.cli import parser
from omni.domain.contracts import BusinessError
from omni.domain.service_admin import (
    admin_operation,
    decode,
    encode,
    request,
    response,
    socket_path,
    validate_request,
    validate_response,
)
from omni.domain.service_deployment import manager_identity
from omni.domain.service_operations import make_operation

SHA = "a" * 64


def operation(action="install"):
    return make_operation(
        plan("linux-system"), action, "830a07ac-f0e8-4a61-896a-f6ba73d76465", "2026-11-07T00:00:00+00:00"
    )


def test_protocol_binds_owner_operation_broker_sequence_request_and_exact_types():
    value = operation()
    record = request(value, "inspect", 0, "cd0c5bb8-2c29-4b91-b3e3-239fdc6ef3ad", SHA)
    assert validate_request(decode(encode(record)), value, 0, SHA) == record
    body = backend.unknown("service_admin_query_unavailable")
    assert validate_response(decode(encode(response(record, body))), record) == body
    for field, changed in (
        ("sequence", False),
        ("broker_sha256", "b" * 64),
        ("operation_id", value["deployment"]["id"]),
        ("command", ["change"]),
    ):
        with pytest.raises(BusinessError):
            validate_request({**record, field: changed}, value, 0, SHA)
    for field, changed in (
        ("root_uid", False),
        ("request_id", value["id"]),
        ("report", {**body, "stderr": "credential=never expose"}),
    ):
        with pytest.raises(BusinessError):
            validate_response({**response(record, body), field: changed}, record)
    with pytest.raises(BusinessError):
        validate_response(response(record, {**body, "state": {"running": True}}), record)
    assert value["starts_service"] is False and value["stops_service"] is False
    assert "persist_root_dispatch_audit" in value["steps"]
    assert socket_path(value).endswith(value["id"] + ".sock")


@pytest.mark.parametrize(
    "data",
    [b'{"x":1,"x":2}\n', b'{"x":NaN}\n', b'{"x":1e999}\n', b"{}\n{}\n", b"{}", b"x" * 65537 + b"\n"],
    ids=("duplicate", "nonfinite", "overflow", "pipeline", "unterminated", "oversize"),
)
def test_protocol_rejects_duplicate_nonfinite_unbounded_or_pipelined_frames(data):
    with pytest.raises(BusinessError):
        decode(data)


def test_broker_scope_rejects_other_targets_and_cli_requires_explicit_asset_pin():
    for target in ("linux-user", "macos-user", "windows-user"):
        with pytest.raises(BusinessError, match="system service"):
            admin_operation(make_operation(plan(target), "install", operation()["id"], operation()["created_at"]))
    args = parser().parse_args(
        [
            "service",
            "apply-operation",
            "operation.json",
            "--approve",
            SHA,
            "--admin-socket",
            socket_path(operation()),
            "--admin-bundle-sha256",
            SHA,
        ]
    )
    assert args.admin_bundle_sha256 == SHA
    assert parser().parse_args(["service", "admin-bundle", "new-root-artifact"]).service_operation == "admin-bundle"


def test_root_bundle_is_stdlib_only_and_tampering_is_rejected_without_any_root_execution(tmp_path, monkeypatch):
    target = tmp_path / "admin"
    result = materialize_admin_bundle(target)
    assert result["installed"] is False and result["elevated"] is False
    manifest = json.loads((target / "manifest.json").read_bytes())
    assert manifest["sha256"] == result["sha256"] and len(manifest["files"]) == 29
    for entry in manifest["files"]:
        assert hashlib.sha256((target / entry["path"]).read_bytes()).hexdigest() == entry["sha256"]
    with pytest.raises(BusinessError):
        materialize_admin_bundle(target)
    isolated = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            "-c",
            "import sys;sys.path.insert(0,sys.argv[1]);"
            "import omni.adapters.platform.service_admin;"
            "import omni.adapters.platform.darwin_service_admin;"
            "import omni.adapters.platform.darwin_admin_probe;"
            "assert 'site' not in sys.modules;assert 'psutil' not in sys.modules;"
            "assert not any(k.startswith(('omni.composition','omni.application','omni.adapters.persistence')) for k in sys.modules)",
            str(target / "lib"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert isolated.returncode == 0, isolated.stderr
    help_result = subprocess.run(
        [sys.executable, "-I", "-S", str(target / "service-admin.py"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert help_result.returncode == 0 and "--owner-uid" in help_result.stdout
    # Trust ownership is exercised separately on Linux. Here only bounded
    # artifact integrity/shape is injected; no privilege claim is made.
    monkeypatch.setattr(boot, "trusted", lambda path: None)
    assert boot.validate_bundle(target)["sha256"] == result["sha256"]
    bound = {
        "deployment": {
            "runtime": {
                "files": [
                    {"path": row["source"], "sha256": row["sha256"], "bytes": row["bytes"]}
                    for row in manifest["files"]
                    if row["source"] is not None
                ]
            }
        }
    }
    boot.validate_sources(manifest, bound)
    bound["deployment"]["runtime"]["files"][0]["sha256"] = "b" * 64
    with pytest.raises(ValueError, match="sources differ"):
        boot.validate_sources(manifest, bound)
    (target / "lib/omni/domain/service_admin.py").write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        boot.validate_bundle(target)


def test_not_found_with_dropins_cannot_authorize_publication_or_claim_absence():
    value = plan("linux-system")
    parsed = systemd_state(
        systemd(
            value,
            LoadState="not-found",
            MainPID="0",
            ActiveState="inactive",
            SubState="dead",
            FragmentPath="",
            DropInPaths="/etc/systemd/system/service.d/foreign.conf",
        ),
        manager_identity(value),
    )
    assert parsed.report["installed"] is None and parsed.definition_path is None
    assert parsed.report["error_code"] == "service_loaded_definition_unverified"
    unit = render(value)["omni.service"].decode()
    assert "User=1000\n" in unit and "User=root\n" not in unit


class Policy:
    def close(self):
        self.native_lock_released = True

    def __init__(self, action="install", installed=False):
        self.operation = operation(action)
        self.approval = self.operation
        self.plan = self.operation["deployment"]
        self.manager = manager_identity(self.plan)
        self.installed, self.enabled = installed, installed
        self.dispatched_once = False
        self.calls = []
        self.reject_paths = False
        self.drift = False

    def authorize(self):
        self.calls.append("authorize")

    def select(self, key):
        assert key == "" and self.approval == self.operation

    def program(self):
        return "/fixed/root/systemctl"

    def definition(self):
        return self.installed, (
            b"foreign" if self.drift else render(self.plan)["omni.service"]
        ) if self.installed else None

    def dispatched(self):
        return self.dispatched_once

    def reject_dropins(self, output):
        self.calls.append("reject_dropins")
        if self.reject_paths:
            raise BusinessError("service_admin_dropins_present", "No override is authorized")

    def record(self, phase, result):
        self.calls.append(phase)
        if phase == "dispatch":
            self.dispatched_once = True

    def publish_definition(self):
        assert self.dispatched_once
        self.calls.append("publish")
        self.installed = True

    def remove_definition(self):
        assert self.dispatched_once
        self.calls.append("remove")
        self.installed = False


class Commands:
    def __init__(self, policy):
        self.policy = policy
        self.calls = []
        self.fail = ""
        self.running = False
        self.cleanup = True

    def run(self, argv):
        self.calls.append(argv)
        assert argv[0] == "/fixed/root/systemctl" and argv[1] == "--system"
        assert not {"start", "stop", "restart", "--now", "--force", "sudo"} & set(argv)
        command = argv[2]
        if command == self.fail:
            raise OSError("credential=never expose")
        if "--property=UnitPath" in argv:
            return 0, b"/etc/systemd/system /usr/lib/systemd/system\n"
        if command == "show":
            return (0 if self.policy.installed else 4), systemd(
                self.policy.plan,
                LoadState="loaded" if self.policy.installed else "not-found",
                MainPID="42" if self.running else "0",
                ActiveState="active" if self.running else "inactive",
                SubState="running" if self.running else "dead",
                FragmentPath=self.policy.manager["installed_path"] if self.policy.installed else "",
            )
        if command == "is-enabled":
            return (
                (0, b"enabled\n")
                if self.policy.enabled
                else (1, b"disabled\n" if self.policy.installed else b"not-found\n")
            )
        if command == "enable":
            self.policy.enabled = True
        if command == "disable":
            self.policy.enabled = False
        return 0, b""

    def close(self, timeout_s=2):
        return self.cleanup


@pytest.mark.parametrize("action", ["install", "uninstall"])
def test_system_registration_writes_only_after_audit_and_verifies_before_enable(action):
    policy = Policy(action, installed=action == "uninstall")
    commands = Commands(policy)
    admin = backend.SystemServiceAdministrator(policy, commands)
    assert admin.change() == {"effect": "applied", "error_code": ""}
    assert (
        policy.calls.index("dispatch")
        < policy.calls.index("publish" if action == "install" else "remove")
        < policy.calls.index("outcome")
    )
    assert policy.installed is (action == "install") and policy.enabled is (action == "install")
    before = len(commands.calls)
    assert admin.change()["error_code"] == "service_admin_already_dispatched"
    assert len(commands.calls) == before
    if action == "install":
        enabled_index = next(i for i, argv in enumerate(commands.calls) if argv[2] == "enable")
        assert any(argv[2] == "show" for argv in commands.calls[1:enabled_index])


@pytest.mark.parametrize("failure", ["dropins", "foreign", "running", "daemon-reload", "enable", "audit"])
def test_privileged_failure_never_replays_or_reports_false_success(failure, monkeypatch):
    policy = Policy(installed=failure in {"foreign", "running"})
    commands = Commands(policy)
    if failure == "dropins":
        policy.reject_paths = True
    elif failure == "foreign":
        policy.drift = True
    elif failure == "running":
        commands.running = True
    elif failure == "audit":
        monkeypatch.setattr(policy, "record", lambda *args: (_ for _ in ()).throw(OSError("credential=never expose")))
    else:
        commands.fail = failure
    result = backend.SystemServiceAdministrator(policy, commands).change()
    assert result["effect"] == ("unknown" if failure in {"daemon-reload", "enable"} else "none")
    assert "never expose" not in json.dumps(result)
    if result["effect"] == "none":
        assert "publish" not in policy.calls and "remove" not in policy.calls
    else:
        count = len(commands.calls)
        assert (
            backend.SystemServiceAdministrator(policy, commands).change()["error_code"]
            == "service_admin_already_dispatched"
        )
        assert len(commands.calls) == count


def test_root_policy_rejects_non_root_or_non_linux_before_any_io(monkeypatch):
    from omni.adapters.platform import service_admin_policy as module

    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.setattr(module, "root_read", lambda *args: (_ for _ in ()).throw(AssertionError("No privileged IO")))
    with pytest.raises(BusinessError, match="root-owned"):
        RootServicePolicy(operation(), SHA).authorize()
