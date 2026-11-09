"""macOS service contracts using isolated files and injected managers; no framework, login or service mutation."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import plistlib
from pathlib import Path

import pytest
from test_v2_service_deployment import plan

from omni.adapters.platform import service_query as q
from omni.adapters.platform import service_registration as r
from omni.adapters.platform.macos_service import VERSION, MacOSServiceInspection, disabled_override, loaded_definition
from omni.domain.contracts import BusinessError
from omni.domain.service_deployment import manager_identity


def job(value, **changes):
    return {**plistlib.loads(r.render(value)[manager_identity(value)["artifact"]]), **changes}


def native_response(value, nonce, *, loaded=True, data=None, **changes):
    return {
        "version": VERSION,
        "domain": manager_identity(value)["domain"],
        "nonce": nonce,
        "label": manager_identity(value)["name"],
        "uid": value["request"]["principal"]["id"],
        "complete": True,
        "loaded": loaded,
        "job": job(value, PID=0) if data is None and loaded else data,
        "error_code": "",
        **changes,
    }


@pytest.mark.parametrize(
    "field,changed,expected",
    [
        ("WorkingDirectory", "/foreign", False),
        ("ProgramArguments", ["/foreign"], False),
        ("RunAtLoad", 1, False),
        ("KeepAlive", True, False),
        ("LimitLoadToSessionType", "Background", False),
        ("UserName", "foreign", None),
        ("MachServices", {"foreign": True}, None),
        ("PID", True, None),
        ("PID", -1, None),
        ("PID", 2**40, None),
        ("OnDemand", False, False),
        ("Program", "/foreign", False),
    ],
)
def test_loaded_definition_requires_the_complete_typed_execution_contract(field, changed, expected):
    value = plan("macos-user")
    assert loaded_definition(value, job(value, PID=42)) is True
    assert loaded_definition(value, job(value, **{field: changed})) is expected
    missing = job(value)
    del missing["ProgramArguments"]
    assert loaded_definition(value, missing) is None


@pytest.mark.parametrize(
    "payload",
    [
        b"not a protocol",
        b'disabled services = {\n "x" => false\n',
        b'disabled services = {\n "x" => false\n "x" => true\n}',
        b'disabled services = {\n "x" => 0\n}',
        b'disabled services = {\n "x" => false\n}\ntrailing',
        b'disabled services = {\n "x" => false\n injected\n}',
    ],
)
def test_disabled_state_rejects_partial_ambiguous_and_unrecognized_output(payload):
    assert disabled_override(payload, "x") is None
    assert disabled_override(b'disabled services = {\n "x" => false\n "other" => true\n}', "x") is False
    assert disabled_override(b'disabled services = {\n "x" => true\n}', "x") is True
    assert disabled_override(b"disabled services = {\n}", "x") is False


@pytest.mark.parametrize("loaded,pid,state", [(False, None, "stopped"), (True, 0, "stopped"), (True, 42, "running")])
@pytest.mark.parametrize("target", ["macos-user", "macos-system"])
def test_inspection_uses_fixed_helper_identity_nonce_and_metadata_only(monkeypatch, loaded, pid, state, target):
    async def scenario():
        value = plan(target)
        calls = []

        async def helper():
            return Path("/fixed/helper")

        async def command(argv):
            calls.append(argv)
            data = job(value, PID=pid) if loaded else None
            return 0, json.dumps(native_response(value, argv[-1], loaded=loaded, data=data)).encode()

        result = await MacOSServiceInspection(command, helper).probe(value)
        assert result["loaded"] is loaded and result["state"] == state
        assert result["loaded_definition_matches"] is (True if loaded else None)
        assert "job" not in result and "ProgramArguments" not in json.dumps(result)
        entry = "--system-service-query" if target == "macos-system" else "--service-query"
        assert calls[0][1:4] == [entry, manager_identity(value)["name"], "1000"]
        assert len(calls[0][-1]) == 32

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "corrupt", ["nonce", "uid", "label", "version", "domain", "extra", "incomplete", "boolean", "oversize", "secret"]
)
def test_native_invalid_or_unavailable_result_never_proves_absence(corrupt):
    async def scenario():
        value = plan("macos-user")

        async def helper():
            return Path("/fixed/helper")

        async def command(argv):
            response = native_response(value, argv[-1], loaded=False)
            if corrupt in {"nonce", "uid", "label", "version", "domain"}:
                response[corrupt] = "changed"
            elif corrupt == "extra":
                response["stderr"] = "credential=never expose"
            elif corrupt == "incomplete":
                response["complete"] = False
            elif corrupt == "boolean":
                response["loaded"] = 0
            elif corrupt == "oversize":
                return 0, b"x" * 65537
            elif corrupt == "secret":
                return 1, b"credential=never expose"
            return 0, json.dumps(response).encode()

        inspector = MacOSServiceInspection(command, helper)
        if corrupt in {"nonce", "uid", "label", "version", "domain", "extra"}:
            with pytest.raises(BusinessError, match="identity"):
                await inspector.probe(value)
        else:
            result = await inspector.probe(value)
            assert result["loaded"] is None and result["state"] == "unknown"
            assert "never expose" not in json.dumps(result)

    asyncio.run(scenario())


def test_missing_helper_and_bounded_description_do_not_infer_service_absence():
    async def scenario():
        value = plan("macos-user")

        async def helper():
            return None

        async def forbidden(_):
            raise AssertionError("Missing helper cannot be replaced with launchctl print")

        result = await MacOSServiceInspection(forbidden, helper).probe(value)
        assert result["loaded"] is None and result["error_code"] == "service_macos_helper_missing"

        async def stale():
            raise BusinessError("desktop_macos_helper_invalid", "untrusted credential=never expose")

        result = await MacOSServiceInspection(forbidden, stale).probe(value)
        assert result["loaded"] is None and result["error_code"] == "service_macos_helper_invalid"
        assert "never expose" not in json.dumps(result)
        assert loaded_definition(value, job(value, ProgramArguments=["x" * 4097])) is None
        deep = []
        for _ in range(9):
            deep = [deep]
        assert loaded_definition(value, job(value, EnvironmentVariables={"nested": deep})) is None

    asyncio.run(scenario())


@pytest.mark.parametrize("code", ["service_native_session_unverified", "credential=never expose"])
def test_native_failure_keeps_allowlisted_recovery_reason_without_echoing_arbitrary_text(code):
    async def scenario():
        value = plan("macos-user")

        async def helper():
            return Path("/fixed/helper")

        async def command(argv):
            return 0, json.dumps(
                native_response(value, argv[-1], loaded=None, complete=False, error_code=code)
            ).encode()

        result = await MacOSServiceInspection(command, helper).probe(value)
        assert result["loaded"] is None and result["state"] == "unknown"
        assert result["error_code"] == (
            code if code == "service_native_session_unverified" else "service_loaded_definition_unverified"
        )
        assert "never expose" not in json.dumps(result)

    asyncio.run(scenario())


@pytest.mark.parametrize("target", ["macos-user", "macos-system"])
def test_user_manager_status_replaces_textual_guess_with_native_and_file_proofs(monkeypatch, target):
    async def scenario():
        value = plan(target)
        monkeypatch.setattr(q.sys, "platform", "darwin")
        monkeypatch.setattr(q.os, "geteuid", lambda: 1000, raising=False)
        inspector = q.ServiceQuery()
        payload = job(value, PID=42)

        async def helper():
            return Path("/fixed/helper")

        async def command(argv):
            assert argv[1] == ("--system-service-query" if target == "macos-system" else "--service-query")
            return 0, json.dumps(native_response(value, argv[-1], data=payload)).encode()

        async def read(_):
            return r.render(value)[manager_identity(value)["artifact"]]

        monkeypatch.setattr(inspector, "_native_helper", helper)
        monkeypatch.setattr(inspector, "_command", command)
        monkeypatch.setattr(inspector, "_file", read)
        try:
            result = await inspector.query(value)
            assert result["installed"] is True and result["definition_matches"] is True and result["pid"] == 42
            assert result["loaded"] is True and result["definition_present"] is True
            payload = job(value, PID=42, ProgramArguments=["/foreign"])
            result = await inspector.query(value)
            assert result["definition_matches"] is False and result["state"] == "unknown"
        finally:
            assert await inspector.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("loaded", [False, True])
def test_macos_removal_disables_verifies_unloads_then_removes_exact_file(monkeypatch, loaded):
    async def scenario():
        value = plan("macos-user")
        native = r.ServiceRegistration()
        calls = []
        enabled = True

        async def inspect(_):
            return {
                "available": True,
                "installed": True,
                "definition_present": True,
                "definition_matches": True,
                "enabled": enabled,
                "state": "stopped",
                "loaded": loaded,
                "loaded_definition_matches": True if loaded else None,
            }

        async def command(argv):
            nonlocal enabled
            calls.append(argv)
            assert argv[0] == "/fixed/launchctl"
            if argv[1] == "disable":
                enabled = False
            return 0, b""

        async def probe(self, _):
            calls.append("confirmed_unloaded")
            return {"loaded": False}

        monkeypatch.setattr(native, "_supported", lambda _: True)
        monkeypatch.setattr(native, "_program", lambda _: "/fixed/launchctl")
        monkeypatch.setattr(native, "inspect", inspect)
        monkeypatch.setattr(native, "_command", command)
        monkeypatch.setattr(native, "_remove_unit", lambda _: calls.append("remove"))
        monkeypatch.setattr(r.MacOSServiceInspection, "probe", probe)
        try:
            result = await native.change(value, "uninstall")
            assert result["effect"] == "applied" and calls[-2:] == ["confirmed_unloaded", "remove"]
            assert [call[1] for call in calls if isinstance(call, list)] == (
                ["disable", "bootout"] if loaded else ["disable"]
            )
            assert all("bootstrap" not in call and "kickstart" not in call for call in calls if isinstance(call, list))
        finally:
            assert await native.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["disable", "changed", "running", "unload", "unknown"])
def test_macos_partial_removal_retains_unknown_and_never_removes_unconfirmed_target(monkeypatch, failure):
    async def scenario():
        value = plan("macos-user")
        native = r.ServiceRegistration()
        calls, inspections = [], 0

        async def inspect(_):
            nonlocal inspections
            inspections += 1
            return {
                "available": True,
                "installed": True,
                "definition_present": True,
                "definition_matches": inspections == 1 or failure != "changed",
                "enabled": inspections == 1,
                "state": "running" if inspections > 1 and failure == "running" else "stopped",
                "loaded": True,
                "loaded_definition_matches": True,
            }

        async def command(argv):
            calls.append(argv[1])
            return (
                1
                if (failure == "disable" and argv[1] == "disable") or (failure == "unload" and argv[1] == "bootout")
                else 0
            ), b""

        async def probe(self, _):
            return {"loaded": None if failure == "unknown" else False}

        monkeypatch.setattr(native, "_supported", lambda _: True)
        monkeypatch.setattr(native, "_program", lambda _: "/fixed/launchctl")
        monkeypatch.setattr(native, "inspect", inspect)
        monkeypatch.setattr(native, "_command", command)
        monkeypatch.setattr(native, "_remove_unit", lambda _: calls.append("remove"))
        monkeypatch.setattr(r.MacOSServiceInspection, "probe", probe)
        try:
            assert (await native.change(value, "uninstall"))["effect"] == "unknown"
            assert "remove" not in calls
            assert calls.count("bootout") <= 1 and calls.count("disable") == 1
            if failure in {"disable", "changed", "running"}:
                assert "bootout" not in calls
        finally:
            assert await native.close()

    asyncio.run(scenario())


def test_macos_install_publishes_without_bootstrap_and_declares_platform_effects(monkeypatch):
    async def scenario():
        value = plan("macos-user")
        native = r.ServiceRegistration()
        calls = []

        async def inspect(_):
            return {"available": True, "installed": False, "definition_present": False, "state": "stopped"}

        async def command(argv):
            calls.append(argv)
            return 0, b""

        monkeypatch.setattr(native, "_supported", lambda _: True)
        monkeypatch.setattr(native, "_program", lambda _: "/fixed/launchctl")
        monkeypatch.setattr(native, "inspect", inspect)
        monkeypatch.setattr(native, "_command", command)
        monkeypatch.setattr(native, "_publish_unit", lambda _: calls.append("publish"))
        try:
            assert (await native.change(value, "install"))["effect"] == "applied"
            assert calls == [
                "publish",
                [
                    "/fixed/launchctl",
                    "enable",
                    manager_identity(value)["domain"] + "/" + manager_identity(value)["name"],
                ],
            ]
        finally:
            assert await native.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "loaded,present,disabled,expected_installed,expected_match",
    [
        (False, False, False, False, False),
        (False, True, False, True, True),
        (True, True, True, True, True),
        (None, True, False, True, None),
    ],
)
def test_registration_reports_disk_loaded_and_enable_facts_independently(
    monkeypatch, tmp_path, loaded, present, disabled, expected_installed, expected_match
):
    async def scenario():
        value = plan("macos-user")
        native = r.ServiceRegistration()
        identity = manager_identity(value)
        source = tmp_path / "isolated.plist"
        if present:
            source.write_bytes(r.render(value)["agent.plist"])

        async def probe(self, _):
            return {
                "loaded": loaded,
                "loaded_definition_matches": True if loaded is True else None,
                "state": "unknown" if loaded is None else "stopped",
                "error_code": "service_native_unverified" if loaded is None else "",
            }

        async def command(argv):
            assert argv == ["/fixed/launchctl", "print-disabled", identity["domain"]]
            return 0, (
                'disabled services = {\n "' + identity["name"] + '" => ' + str(disabled).lower() + "\n}"
            ).encode()

        monkeypatch.setattr(r, "manager_identity", lambda _: {**identity, "installed_path": str(source)})
        monkeypatch.setattr(native, "_check_unit_path", lambda path, _: None)
        monkeypatch.setattr(native, "_program", lambda _: "/fixed/launchctl")
        monkeypatch.setattr(native, "_command", command)
        monkeypatch.setattr(r.MacOSServiceInspection, "probe", probe)
        try:
            result = await native._inspect_macos(value)
            assert result["installed"] is expected_installed and result["definition_matches"] is expected_match
            assert result["loaded"] is loaded and result["definition_present"] is present
            assert result["enabled"] is not disabled
            if loaded is None:
                assert result["available"] is False and result["state"] == "unknown"
            if present and loaded is False:
                assert result["activation"] == "next_login"
        finally:
            assert await native.close()

    asyncio.run(scenario())


def acceptance_script():
    path = Path(__file__).resolve().parents[1] / "scripts/accept_macos_services.py"
    spec = importlib.util.spec_from_file_location("macos_service_acceptance", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_acceptance_distinguishes_registration_loaded_runtime_and_unavailable_native():
    script = acceptance_script()
    registration = {
        "available": True,
        "installed": True,
        "definition_present": True,
        "definition_matches": True,
        "enabled": True,
        "loaded": False,
    }
    status = {"manager": {"installed": True, "loaded": False, "definition_matches": None}, "ready": False}
    assert script.expectation("registered", registration, status)
    assert not script.expectation("loaded", registration, status)
    assert not script.expectation("ready", registration, status)
    assert not script.expectation("absent", registration, status)
    assert not script.expectation("inspection", {**registration, "loaded": None}, status)
    assert not script.expectation("inspection", {**registration, "available": False}, status)


def test_acceptance_refuses_non_macos_before_files_or_native_initialization(tmp_path, monkeypatch, capsys):
    script = acceptance_script()
    monkeypatch.setattr(script.sys, "platform", "linux")

    def forbidden():
        raise AssertionError("Wrong platform must not initialize native IO")

    monkeypatch.setattr(script, "CurrentDeploymentFiles", forbidden)
    output = tmp_path / "never-created.json"
    assert script.main(["--bundle", str(tmp_path / "bundle"), "--output", str(output)]) == 2
    assert "macos_required" in capsys.readouterr().err and not output.exists()


def test_acceptance_queries_only_and_closes_all_real_owners(monkeypatch):
    async def scenario():
        script = acceptance_script()
        value = plan("macos-user")
        calls = []

        class Registration:
            async def inspect(self, deployment):
                assert deployment == value
                calls.append("registration")
                return {
                    "available": True,
                    "installed": True,
                    "definition_present": True,
                    "definition_matches": True,
                    "enabled": True,
                    "loaded": True,
                }

            async def close(self, timeout_s):
                calls.append("registration_closed")
                return True

        class Query:
            async def query(self, deployment):
                calls.append("query")
                return {
                    "available": True,
                    "installed": True,
                    "definition_matches": True,
                    "state": "running",
                    "loaded": True,
                    "pid": 42,
                }

            async def close(self, timeout_s):
                calls.append("query_closed")
                return True

        class Readiness:
            async def probe(self, deployment):
                calls.append("readiness")
                return {"ready": True, "pid": 42}

            async def close(self, timeout_s):
                calls.append("readiness_closed")
                return True

        monkeypatch.setattr(script, "ServiceRegistration", Registration)
        monkeypatch.setattr(script, "ServiceQuery", Query)
        monkeypatch.setattr(script, "ServiceReadiness", Readiness)
        report = await script.inspect(object(), {"valid": True, "eligible": True, "plan": value}, "ready")
        assert report["expectation_passed"] is True and report["scope"] == "read_only_inspection"
        assert report["installation_exercised"] is False and report["whole_platform_acceptance"] is False
        assert calls == [
            "registration",
            "query",
            "readiness",
            "registration_closed",
            "query_closed",
            "readiness_closed",
        ]

    asyncio.run(scenario())
