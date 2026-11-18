"""System-domain inspection, protocol isolation and build gates; all native IO injected on this host."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from omni_test_support.paths import PRODUCT_ROOT as PRODUCT_ROOT
from omni_test_support.v2.macos_services import acceptance_script, job, native_response
from omni_test_support.v2.service_deployment import plan

from omni.adapters.platform.services import service_query as q
from omni.adapters.platform.services.macos_service import MacOSServiceInspection
from omni.domain.contracts import BusinessError
from omni.domain.service_deployment import manager_identity


@pytest.mark.parametrize("loaded", [True, False])
def test_user_and_system_frames_cannot_be_substituted_even_for_the_same_label(loaded):
    async def scenario():
        value = plan("macos-system")

        async def helper():
            return Path("/fixed/helper")

        async def command(argv):
            assert argv[1] == "--system-service-query"
            frame = native_response(value, argv[-1], loaded=loaded)
            frame["domain"] = "gui/" + frame["uid"]
            return 0, json.dumps(frame).encode()

        with pytest.raises(BusinessError) as caught:
            await MacOSServiceInspection(command, helper).probe(value)
        assert caught.value.code == "service_native_identity_invalid"

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["missing", "denied", "revoked", "oversize", "old_protocol", "unknown_field"])
def test_system_unknown_never_falls_back_to_launchctl_or_claims_absence(monkeypatch, fault):
    async def scenario():
        value = plan("macos-system")
        monkeypatch.setattr(q.os, "geteuid", lambda: 1000, raising=False)
        monkeypatch.setattr(q.sys, "platform", "darwin")
        inspector, calls = q.ServiceQuery(), []

        async def helper():
            return None if fault == "missing" else Path("/fixed/helper")

        async def command(argv):
            assert argv[1] == "--system-service-query"
            calls.append(argv)
            if fault == "denied":
                return 1, b"SECRET-NEVER-EXPORT"
            if fault == "oversize":
                return 0, b"x" * 65537
            frame = native_response(value, argv[-1], loaded=False)
            if fault == "revoked":
                frame.update(complete=False, loaded=None, error_code="service_native_path_unverified")
            if fault == "old_protocol":
                frame["version"] = "omni-macos-service-query-1"
            if fault == "unknown_field":
                frame["other_jobs"] = "SECRET-NEVER-EXPORT"
            return 0, json.dumps(frame).encode()

        async def missing(_):
            raise FileNotFoundError

        monkeypatch.setattr(inspector, "_native_helper", helper)
        monkeypatch.setattr(inspector, "_command", command)
        monkeypatch.setattr(inspector, "_file", missing)
        try:
            report = await inspector.query(value)
            assert report["available"] is False and report["installed"] is None and report["state"] == "unknown"
            assert report["definition_matches"] is None
            assert "SECRET" not in json.dumps(report)
            assert len(calls) == (0 if fault == "missing" else 1)
        finally:
            assert await inspector.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("loaded,present", [(False, False), (False, True), (True, True), (True, False)])
def test_system_status_requires_both_loaded_native_and_disk_facts(monkeypatch, loaded, present):
    async def scenario():
        value = plan("macos-system")
        monkeypatch.setattr(q.os, "geteuid", lambda: 1000, raising=False)
        monkeypatch.setattr(q.sys, "platform", "darwin")
        inspector = q.ServiceQuery()

        async def helper():
            return Path("/fixed/helper")

        async def command(argv):
            return 0, json.dumps(
                native_response(value, argv[-1], loaded=loaded, data=job(value, PID=42) if loaded else None)
            ).encode()

        async def read(_):
            if not present:
                raise FileNotFoundError
            return q.render(value)[manager_identity(value)["artifact"]]

        monkeypatch.setattr(inspector, "_native_helper", helper)
        monkeypatch.setattr(inspector, "_command", command)
        monkeypatch.setattr(inspector, "_file", read)
        try:
            report = await inspector.query(value)
            assert report["available"] is True and report["definition_present"] is present
            assert report["installed"] is (loaded or present)
            assert report["definition_matches"] is (False if loaded and not present else True if loaded else None)
            assert report["activation"] == (
                "loaded" if loaded else "explicit_start_or_next_boot" if present else "unknown"
            )
            assert "UserName" not in json.dumps(report) and "ProgramArguments" not in json.dumps(report)
        finally:
            assert await inspector.close()

    asyncio.run(scenario())


def test_system_evidence_uses_only_read_ports_and_keeps_administrator_acceptance_pending(monkeypatch):
    async def scenario():
        script, value, events = acceptance_script(), plan("macos-system"), []
        report = {
            "available": True,
            "loaded": False,
            "installed": False,
            "definition_present": False,
            "definition_matches": None,
            "state": "stopped",
            "pid": None,
        }

        class Registration:
            async def inspect(self, _):
                pytest.fail("System evidence cannot imply an implemented administrator backend")

            async def close(self, _):
                events.append("registration_closed")
                return True

        class Query:
            async def query(self, actual):
                assert actual == value
                events.append("query")
                return report

            async def close(self, _):
                events.append("query_closed")
                return True

        class Readiness:
            async def probe(self, _):
                events.append("readiness")
                return {"ready": False}

            async def close(self, _):
                events.append("readiness_closed")
                return True

        monkeypatch.setattr(script, "ServiceRegistration", Registration)
        monkeypatch.setattr(script, "ServiceQuery", Query)
        monkeypatch.setattr(script, "ServiceReadiness", Readiness)
        result = await script.inspect(object(), {"valid": True, "eligible": True, "plan": value}, "absent")
        assert result["expectation_passed"] is True
        assert result["target"] == "macos-system" and result["registration_source"] == "system_read_only_query"
        assert result["administrator_operation_exercised"] is False and result["whole_platform_acceptance"] is False
        assert events == ["query", "query", "readiness", "registration_closed", "query_closed", "readiness_closed"]

    asyncio.run(scenario())


def test_system_read_only_evidence_cannot_claim_administrator_dispatch(tmp_path, monkeypatch, capsys):
    script, value = acceptance_script(), plan("macos-system")

    class Files:
        def verify(self, _):
            return {"eligible": True, "plan": value}

    monkeypatch.setattr(script.sys, "platform", "darwin")
    monkeypatch.setattr(script, "CurrentDeploymentFiles", Files)
    output = tmp_path / "not-created.json"
    assert script.main(["--bundle", str(tmp_path / "bundle"), "--output", str(output), "--expect", "registered"]) == 2
    assert "macos_system_inspection_only" in capsys.readouterr().err and not output.exists()


@pytest.mark.parametrize("failure", ["failed", "skipped", "missing_case"])
def test_native_acl_gate_fails_before_signing_or_installing_any_application(tmp_path, monkeypatch, failure):
    path = PRODUCT_ROOT / "scripts/build_macos_helper.py"
    spec = importlib.util.spec_from_file_location("gated_macos_build", path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    commands = []

    def run(argv):
        commands.append(argv)
        assert "test" in argv and "build" not in argv and "codesign" not in str(argv)
        output = Path(argv[argv.index("--xunit-output") + 1])
        native = "--filter" in argv
        suite = ET.Element("testsuite")
        for index in range((7 if failure == "missing_case" else 8) if native else 9):
            case = ET.SubElement(suite, "testcase", name="case" + str(index))
            if native and index == 0 and failure != "missing_case":
                ET.SubElement(case, "failure" if failure == "failed" else "skipped")
        ET.ElementTree(suite).write(output, encoding="utf-8")

    monkeypatch.setattr(script, "source_fingerprints", lambda: {"fixture.swift": "a" * 64})
    monkeypatch.setattr(script.subprocess, "check_output", lambda *args, **kwargs: "injected-toolchain")
    monkeypatch.setattr(script, "run", run)
    with pytest.raises(RuntimeError, match="all eight"):
        script.build(tmp_path / "build", ["arm64"], "-")
    assert len(commands) == 2 and all("install" not in str(command) for command in commands)
