"""Five deployment targets share one pure, fingerprinted startup contract."""

from __future__ import annotations

import copy
import json
import plistlib
import subprocess
from xml.etree import ElementTree as ET

import pytest

from omni.adapters.platform.service_templates import TASK_NAMESPACE, render
from omni.domain.contracts import BusinessError
from omni.domain.service_deployment import (
    TARGETS,
    make_plan,
    manager_identity,
    parse_request,
    parse_startup,
    plan_fingerprint,
    service_argv,
    startup,
    validate_plan,
    validate_runtime,
)

INSTANCE = "8ccf2cf6-661f-4100-9141-2198864b558e"
PLAN = "521c022b-82a0-4680-81c9-54dac4b724f8"


def request(target="linux-user"):
    windows = target == "windows-user"
    principal = {
        "name": "example",
        "id": "S-1-5-21-100-200-300-1000" if windows else "1000",
        "home": "C:\\Users\\example" if windows else "/home/example",
    }
    return {
        "format": "omni-service-request-1",
        "target": target,
        "instance_id": INSTANCE,
        "home": "D:\\private\\omni" if windows else "/home/example/omni",
        "working_directory": "D:\\Owner Workspace" if windows else "/home/example/Owner Workspace",
        "bundle_dir": "D:\\Service Bundles\\v2" if windows else "/opt/Service Bundles/v2",
        "principal": principal,
        "host": "127.0.0.1",
        "port": 8766,
        "external_url": None,
        "master_key_file": None,
        "stdout_logs": True,
    }


def runtime(target="linux-user"):
    windows = target == "windows-user"
    prefix = "C:\\versions\\v2\\venv" if windows else "/opt/versions/v2/venv"
    python = prefix + ("\\Scripts\\python.exe" if windows else "/bin/python")
    return {
        "format": "omni-service-runtime-2",
        "platform": "win32" if windows else "darwin" if target.startswith("macos-") else "linux",
        "python": python,
        "python_realpath": python if windows else "/usr/bin/python3.12",
        "python_prefix": prefix,
        "python_links": [] if windows else [{"path": python, "target": "/usr/bin/python3.12"}],
        "venv_config": {
            "path": prefix + ("\\pyvenv.cfg" if windows else "/pyvenv.cfg"),
            "sha256": "a" * 64,
            "bytes": 27,
        },
        "python_sha256": "b" * 64,
        "process_python": "C:\\Python312\\python.exe" if windows else "/usr/bin/python3.12",
        "process_python_sha256": "f" * 64,
        "python_version": "3.12.12 (example) [Compiler]",
        "architecture": "AMD64" if windows else "arm64",
        "package_root": prefix + ("\\Lib\\site-packages\\omni" if windows else "/lib/python3.12/site-packages/omni"),
        "build_id": "c" * 64,
        "assets_sha256": "d" * 64,
        "api_version": 1,
        "schema_head": "v2_0026",
        "editable": False,
        "principal": request(target)["principal"],
        "files": [{"path": "__init__.py", "sha256": "e" * 64, "bytes": 23}],
        "distributions": [{"name": "omnioperator", "version": "2.0.0a1"}],
    }


def plan(target="linux-user"):
    return make_plan(request(target), runtime(target), PLAN, "2026-11-05T00:00:00+00:00")


@pytest.mark.parametrize("target", sorted(TARGETS))
def test_five_targets_render_deterministic_bound_startup_without_executing(target, monkeypatch):
    value = plan(target)

    def forbidden(*args, **kwargs):
        raise AssertionError("Service rendering must not launch a process")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    artifacts = render(value)
    assert artifacts == render(validate_plan(value))
    manager = manager_identity(value)
    assert set(artifacts) == {"deployment.json", "startup.json", "README.txt", manager["artifact"]}
    assert json.loads(artifacts["deployment.json"]) == value
    parsed = parse_startup(json.loads(artifacts["startup.json"]))
    assert parsed == startup(value)
    assert parsed["service_plan_sha256"] == plan_fingerprint(value)
    assert parsed["instance_id"] == INSTANCE
    argv = service_argv(value)
    assert argv == [
        value["runtime"]["python"],
        "-I",
        "-u",
        "-m",
        "omni.composition.service_runner",
        "--deployment",
        value["request"]["bundle_dir"],
    ]
    notes = json.loads(artifacts["README.txt"])
    assert notes["commands_not_executed"]["install"]
    assert notes["plan_sha256"] == value["sha256"]


def test_windows_uses_schema_privileges_and_exact_separate_exec_arguments():
    value = plan("windows-user")
    root = ET.fromstring(render(value)["service.xml"])
    namespace = {"t": TASK_NAMESPACE}
    assert (
        root.findtext("t:Principals/t:Principal/t:UserId", namespaces=namespace) == value["request"]["principal"]["id"]
    )
    assert root.findtext("t:Principals/t:Principal/t:LogonType", namespaces=namespace) == "InteractiveToken"
    assert root.findtext("t:Principals/t:Principal/t:RunLevel", namespaces=namespace) == "LeastPrivilege"
    assert root.findtext("t:Settings/t:MultipleInstancesPolicy", namespaces=namespace) == "IgnoreNew"
    assert root.findtext("t:Settings/t:ExecutionTimeLimit", namespaces=namespace) == "PT0S"
    assert root.find("t:Settings/t:RestartOnFailure", namespace) is None
    assert root.findtext("t:Actions/t:Exec/t:Command", namespaces=namespace) == service_argv(value)[0]
    assert root.findtext("t:Actions/t:Exec/t:Arguments", namespaces=namespace) == subprocess.list2cmdline(
        service_argv(value)[1:]
    )


def test_systemd_escaping_and_macos_daemon_do_not_interpret_paths_as_shell():
    wanted = request("linux-system")
    wanted["bundle_dir"] = '/opt/空 格/%i/$USER/"quoted"'
    wanted["working_directory"] = "/home/example/work %i $USER"
    value = make_plan(wanted, runtime("linux-system"), PLAN, "2026-11-05T00:00:00Z")
    unit = render(value)["omni.service"].decode("utf-8")
    assert '"/opt/空 格/%%i/$$USER/\\"quoted\\""' in unit
    assert 'WorkingDirectory="/home/example/work %%i $USER"' in unit
    assert "User=1000\n" in unit
    assert "Type=exec\n" in unit and "Restart=no\n" in unit and "KillMode=mixed\n" in unit
    daemon = plistlib.loads(render(plan("macos-system"))["daemon.plist"])
    assert daemon["ProgramArguments"] == service_argv(plan("macos-system"))
    assert daemon["UserName"] == "example"
    assert daemon["KeepAlive"] is False and daemon["ExitTimeOut"] == 45


def test_strict_requests_reject_unknown_keys_paths_root_and_malformed_runtime():
    for target, field, bad in (
        ("linux-user", "home", "/home/example/../outside"),
        ("linux-user", "working_directory", "relative"),
        ("linux-user", "bundle_dir", "/tmp/x\nUser=root"),
        ("windows-user", "home", "\\\\server\\share"),
        ("windows-user", "bundle_dir", "D:\\hidden\\..\\service"),
        ("windows-user", "home", "D:\\NUL.txt"),
    ):
        value = request(target)
        value[field] = bad
        with pytest.raises(BusinessError):
            parse_request(value)
    value = request()
    value["environment"] = {"PRIVATE_TOKEN": "never accepted"}
    with pytest.raises(BusinessError):
        parse_request(value)
    for target in ("linux-system", "macos-system"):
        value = request(target)
        value["principal"] = {"name": "root", "id": "0", "home": "/root"}
        with pytest.raises(BusinessError):
            parse_request(value)
    malformed = runtime()
    malformed["platform"] = []
    with pytest.raises(BusinessError):
        validate_runtime(malformed)


def test_origin_validation_and_python_full_version_normalization_are_bounded():
    value = request()
    value["host"] = "0.0.0.0"
    with pytest.raises(BusinessError):
        parse_request(value)
    for origin in (
        "http://example.com",
        "https://user:secret@example.com",
        "https://[broken",
        "https://bad host",
        "https://example.com/path",
    ):
        value["external_url"] = origin
        with pytest.raises(BusinessError):
            parse_request(value)
    value["external_url"] = "https://EXAMPLE.com:443/"
    assert parse_request(value)["external_url"] == "https://example.com"
    current = runtime()
    current["python_version"] = "3.12.12 (example)\n[Compiler]"
    assert validate_runtime(current)["python_version"] == "3.12.12 (example) [Compiler]"


def test_plan_hash_binds_interpreter_principal_argv_and_files_but_editable_preview_is_allowed():
    value = plan()
    for section, field, replacement in (
        ("request", "bundle_dir", "/opt/another-bundle"),
        ("runtime", "python", "/opt/another-python"),
        ("runtime", "build_id", "f" * 64),
    ):
        changed = copy.deepcopy(value)
        changed[section][field] = replacement
        with pytest.raises(BusinessError):
            validate_plan(changed)
    captured = runtime()
    captured["editable"] = True
    preview = make_plan(request(), captured, PLAN, "2026-11-05T00:00:00Z")
    assert validate_plan(preview)["runtime"]["editable"] is True
    assert render(preview)["startup.json"]
    changed = copy.deepcopy(preview)
    changed["runtime"]["files"][0]["sha256"] = "1" * 64
    with pytest.raises(BusinessError):
        validate_plan(changed)
