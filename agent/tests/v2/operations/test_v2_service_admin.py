"""Administrator protocol/artifact/backend using isolated files and injected root IO; never elevates or registers a service."""

from __future__ import annotations

from omni_test_support.v2.service_admin import (
    SHA as SHA,
)
from omni_test_support.v2.service_admin import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.service_admin import (
    Commands as Commands,
)
from omni_test_support.v2.service_admin import (
    Policy as Policy,
)
from omni_test_support.v2.service_admin import (
    RootServicePolicy as RootServicePolicy,
)
from omni_test_support.v2.service_admin import (
    admin_operation as admin_operation,
)
from omni_test_support.v2.service_admin import (
    backend as backend,
)
from omni_test_support.v2.service_admin import (
    boot as boot,
)
from omni_test_support.v2.service_admin import (
    decode as decode,
)
from omni_test_support.v2.service_admin import (
    encode as encode,
)
from omni_test_support.v2.service_admin import (
    hashlib as hashlib,
)
from omni_test_support.v2.service_admin import (
    json as json,
)
from omni_test_support.v2.service_admin import (
    make_operation as make_operation,
)
from omni_test_support.v2.service_admin import (
    manager_identity as manager_identity,
)
from omni_test_support.v2.service_admin import (
    materialize_admin_bundle as materialize_admin_bundle,
)
from omni_test_support.v2.service_admin import (
    operation as operation,
)
from omni_test_support.v2.service_admin import (
    parser as parser,
)
from omni_test_support.v2.service_admin import (
    plan as plan,
)
from omni_test_support.v2.service_admin import (
    pytest as pytest,
)
from omni_test_support.v2.service_admin import (
    render as render,
)
from omni_test_support.v2.service_admin import (
    request as request,
)
from omni_test_support.v2.service_admin import (
    response as response,
)
from omni_test_support.v2.service_admin import (
    socket_path as socket_path,
)
from omni_test_support.v2.service_admin import (
    subprocess as subprocess,
)
from omni_test_support.v2.service_admin import (
    sys as sys,
)
from omni_test_support.v2.service_admin import (
    systemd as systemd,
)
from omni_test_support.v2.service_admin import (
    systemd_state as systemd_state,
)
from omni_test_support.v2.service_admin import (
    validate_request as validate_request,
)
from omni_test_support.v2.service_admin import (
    validate_response as validate_response,
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
    assert manifest["sha256"] == result["sha256"] and len(manifest["files"]) == 30
    assert any(row["path"] == "lib/omni/adapters/platform/services/__init__.py" for row in manifest["files"])
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
            "import omni.adapters.platform.services.service_admin;"
            "import omni.adapters.platform.services.darwin_service_admin;"
            "import omni.adapters.platform.services.darwin_admin_probe;"
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
    from omni.adapters.platform.services import service_admin_policy as module

    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.setattr(module, "root_read", lambda *args: (_ for _ in ()).throw(AssertionError("No privileged IO")))
    with pytest.raises(BusinessError, match="root-owned"):
        RootServicePolicy(operation(), SHA).authorize()
