"""Injected native manager protocols and isolated file publication; no real registration or start."""

from __future__ import annotations

import asyncio
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from omni_test_support.v2.service_deployment import plan
from omni_test_support.v2.service_query import systemd

from omni.adapters.platform.services import service_registration as r
from omni.domain.contracts import BusinessError
from omni.domain.service_deployment import make_plan, manager_identity


def test_linux_exact_not_found_report_is_required_and_nonzero_is_not_blindly_absent(tmp_path, monkeypatch):
    async def scenario():
        template = plan("linux-user")
        directory = tmp_path.resolve()
        principal = {**template["request"]["principal"], "home": directory.as_posix().removeprefix(directory.drive)}
        value = make_plan(
            {**template["request"], "principal": principal},
            {**template["runtime"], "principal": principal},
            template["id"],
            template["created_at"],
        )
        monkeypatch.setattr(r.sys, "platform", "linux")
        monkeypatch.setattr(r.os, "geteuid", lambda: 1000, raising=False)
        native = r.ServiceRegistration()
        monkeypatch.setattr(native, "_program", lambda _: "/fixed/systemctl")
        output = b"untrusted localized no such service message"

        async def command(argv):
            assert argv[:3] == ["/fixed/systemctl", "--user", "show"]
            return 4, output

        monkeypatch.setattr(native, "_command", command)
        try:
            result = await native.inspect(value)
            assert result["installed"] is None
            output = systemd(
                value, LoadState="not-found", ActiveState="inactive", SubState="dead", MainPID="0", FragmentPath=""
            )
            result = await native.inspect(value)
            assert (
                result["installed"] is False and result["definition_present"] is False and result["state"] == "stopped"
            )
            output = systemd(
                value, LoadState="not-found", ActiveState="inactive", SubState="dead", MainPID="0", Id="foreign.service"
            )
            assert (await native.inspect(value))["installed"] is None
        finally:
            assert await native.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["install", "uninstall"])
def test_linux_fixed_commands_separate_registration_from_start_and_unknown_partial_effect(monkeypatch, action):
    async def scenario():
        value = plan("linux-user")
        native = r.ServiceRegistration()
        calls = []
        monkeypatch.setattr(native, "_supported", lambda _: True)
        monkeypatch.setattr(native, "_program", lambda _: "/fixed/systemctl")
        monkeypatch.setattr(native, "_publish_unit", lambda _: calls.append("publish"))
        monkeypatch.setattr(native, "_remove_unit", lambda _: calls.append("remove"))

        async def inspect(_):
            installed = action == "uninstall"
            return {
                "available": True,
                "installed": installed,
                "definition_present": installed,
                "definition_matches": installed,
                "state": "stopped",
            }

        failure = False

        async def command(argv):
            calls.append(argv)
            return (1 if failure else 0), b""

        monkeypatch.setattr(native, "inspect", inspect)
        monkeypatch.setattr(native, "_command", command)
        try:
            assert (await native.change(value, action))["effect"] == "applied"
            if action == "install":
                assert calls[0] == "publish" and calls[1] == ["/fixed/systemctl", "--user", "daemon-reload"]
                assert calls[2] == [
                    "/fixed/systemctl",
                    "--user",
                    "enable",
                    manager_identity(value)["name"],
                    "--no-ask-password",
                ]
            else:
                assert calls[0][2] == "disable" and calls[1] == "remove" and calls[2][2] == "daemon-reload"
            assert not {"start", "stop", "restart", "--now", "sudo"} & {
                part for call in calls if isinstance(call, list) for part in call
            }
            calls.clear()
            failure = True
            assert (await native.change(value, action))["effect"] == "unknown"
            if action == "uninstall":
                assert "remove" not in calls
        finally:
            assert await native.close()

    asyncio.run(scenario())


def test_unimplemented_targets_are_reported_without_native_dispatch(monkeypatch):
    async def scenario():
        native = r.ServiceRegistration()

        async def forbidden(*args):
            raise AssertionError("Unimplemented platform must not launch a command")

        monkeypatch.setattr(native, "_command", forbidden)
        try:
            for target in ("linux-system", "macos-system"):
                result = await native.inspect(plan(target))
                assert result["available"] is False and result["installed"] is None
                assert (await native.change(plan(target), "install"))["effect"] == "none"
        finally:
            assert await native.close()

    asyncio.run(scenario())


def test_unit_publication_is_complete_no_clobber_and_removal_requires_unchanged_bytes(tmp_path, monkeypatch):
    value = copy.deepcopy(plan("linux-user"))
    value["request"]["principal"].update({"id": str(tmp_path.stat().st_uid), "home": str(tmp_path)})
    target = tmp_path / ".config/systemd/user/isolated.service"
    native = r.ServiceRegistration()
    # NTFS does not model POSIX group/world mode bits. Ownership-mode policy is
    # covered below with explicit metadata; this case exercises real publication.
    if os.name == "nt":
        monkeypatch.setattr(r, "_owner_can_register", lambda info, uid: info.st_uid == uid)
    monkeypatch.setattr(r, "manager_identity", lambda _: {"installed_path": str(target), "artifact": "omni.service"})
    monkeypatch.setattr(r, "render", lambda _: {"omni.service": b"[Unit]\nDescription=isolated\n"})
    try:
        native._publish_unit(value)
        assert target.read_bytes() == b"[Unit]\nDescription=isolated\n" and target.stat().st_nlink == 1
        with pytest.raises(FileExistsError):
            native._publish_unit(value)
        target.write_bytes(b"foreign service, preserve me")
        with pytest.raises(BusinessError, match="foreign"):
            native._remove_unit(value)
        assert target.read_bytes() == b"foreign service, preserve me"
        target.write_bytes(b"[Unit]\nDescription=isolated\n")
        native._remove_unit(value)
        assert not target.exists()
        assert not list(target.parent.glob("*.omni-partial"))
    finally:
        assert asyncio.run(native.close())


@pytest.mark.parametrize(
    "uid,mode,expected", [(1000, 0o700, True), (0, 0o700, False), (1000, 0o770, False), (1000, 0o702, False)]
)
def test_linux_registration_owner_and_modes_are_enforced(uid, mode, expected):
    assert r._owner_can_register(SimpleNamespace(st_uid=uid, st_mode=mode), 1000) is expected


def test_unit_links_and_parent_permissions_rejected_without_writes(tmp_path, monkeypatch):
    value = copy.deepcopy(plan("linux-user"))
    value["request"]["principal"].update({"id": str(tmp_path.stat().st_uid), "home": str(tmp_path)})
    target = tmp_path / "registration" / "isolated.service"
    target.parent.mkdir()
    native = r.ServiceRegistration()
    monkeypatch.setattr(r, "manager_identity", lambda _: {"installed_path": str(target), "artifact": "omni.service"})
    monkeypatch.setattr(r, "render", lambda _: {"omni.service": b"isolated"})
    try:
        foreign = tmp_path / "foreign"
        foreign.write_bytes(b"private")
        os.link(foreign, target)
        with pytest.raises(BusinessError, match="one regular"):
            native._remove_unit(value)
        assert foreign.read_bytes() == b"private" and target.exists()
    finally:
        assert asyncio.run(native.close())


def test_windows_broker_source_is_packaged_bounded_and_never_updates_or_force_stops():
    path = Path(r.__file__).parents[2] / "workers/windows_services.ps1"
    text = path.read_text(encoding="utf-8")
    assert "RegisterTask($TaskName,$xml,34,$OwnerSID,$null,3,$null)" in text
    assert "DeleteTask($TaskName,0)" in text and "$task.GetInstances(0)" in text
    assert "-2147024894" in text and "$dispatched=$true" in text
    assert "MaxCharactersInDocument = 65536" in text
    start = text.split("} elseif ($Mode -eq 'start') {", 1)[1].split("} elseif ($Mode -eq 'uninstall') {", 1)[0]
    assert text.count("$task.Run($null)") == 1 and "$task.Run($null)" in start
    assert "$result.definition_matches -ne $true" in start and "$result.state -ne 'stopped'" in start
    assert "$task.Definition.Settings.AllowDemandStart" in start
    for forbidden in ("Invoke-Expression", "Start-Process", ".Stop(", "schtasks /Create /F", "Write-Host"):
        assert forbidden not in text
    assert "$_" not in text[text.index("} catch {\n    # Never") :]


@pytest.mark.parametrize("corrupt", [None, "identity", "boolean", "extra", "body", "pid"])
def test_windows_native_receipt_is_identity_bound_and_rejects_untrusted_output(monkeypatch, tmp_path, corrupt):
    async def scenario():
        value = plan("windows-user")
        monkeypatch.setattr(r.sys, "platform", "win32")
        native = r.ServiceRegistration()
        original = r._read
        expected = r.render(value)["service.xml"]

        def read(path, limit, **kwargs):
            return expected if Path(path).name == "service.xml" else original(path, limit, **kwargs)

        def system_directory(buffer, capacity):
            buffer.value = str(tmp_path)
            return len(str(tmp_path))

        monkeypatch.setattr(
            r.ctypes,
            "WinDLL",
            lambda *args, **kwargs: SimpleNamespace(GetSystemDirectoryW=system_directory),
            raising=False,
        )
        monkeypatch.setattr(r, "_read", read)
        commands = []

        async def command(argv):
            commands.append(argv)
            response = {
                "format": "omni-service-native-1",
                "plan_sha256": value["sha256"],
                "action": "inspect",
                "available": True,
                "installed": False,
                "definition_present": False,
                "definition_matches": None,
                "enabled": False,
                "state": "stopped",
                "engine_pid": None,
                "effect": "none",
                "error_code": "",
            }
            if corrupt == "identity":
                response["plan_sha256"] = "0" * 64
            elif corrupt == "boolean":
                response["installed"] = "false"
            elif corrupt == "extra":
                response["stderr"] = "credential=never expose"
            elif corrupt == "pid":
                response["pid"] = "42"
            elif corrupt == "body":
                return 0, b"native exception credential=never expose"
            return 0, json.dumps(response).encode()

        monkeypatch.setattr(native, "_command", command)
        try:
            result = await native._windows(value, "inspect")
            assert len(commands) == 1 and commands[0][1:7] == [
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Mta",
                "-File",
                str(Path(r.__file__).resolve().parents[2] / "workers/windows_services.ps1"),
            ]
            assert commands[0][7:9] == ["-Mode", "inspect"]
            if corrupt is None:
                assert result["installed"] is False and result["state"] == "stopped"
            else:
                assert result["installed"] is None and result["error_code"] == "service_registration_unverified"
            assert "never expose" not in json.dumps(result)
        finally:
            assert await native.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("action,expected_pid", [(4242, 4242), (None, None)])
def test_windows_inspect_receipt_carries_the_same_action_pid_as_status(monkeypatch, action, expected_pid):
    """R3: start/reconcile read the native receipt, so it must carry the verified action PID."""

    async def scenario():
        value = plan("windows-user")
        monkeypatch.setattr(r.sys, "platform", "win32")
        expected = r.render(value)["service.xml"]

        def read(path, limit, **kwargs):
            return expected if Path(path).name == "service.xml" else b"script"

        monkeypatch.setattr(r, "_read", read)
        monkeypatch.setattr(r, "_no_links", lambda path: None)
        monkeypatch.setattr(
            r.ctypes,
            "WinDLL",
            lambda *args, **kwargs: SimpleNamespace(GetSystemDirectoryW=lambda buffer, capacity: 1),
            raising=False,
        )

        async def command(_argv):
            response = {
                "format": "omni-service-native-1",
                "plan_sha256": value["sha256"],
                "action": "inspect",
                "available": True,
                "installed": True,
                "definition_present": True,
                "definition_matches": True,
                "enabled": True,
                "state": "running",
                "engine_pid": 555,
                "effect": "none",
                "error_code": "",
            }
            return 0, json.dumps(response).encode()

        monkeypatch.setattr(r, "action_pid", lambda engine, expected_xml, plan: action if engine == 555 else None)
        receipt = await r.windows_receipt(command, value, "inspect")
        assert receipt["state"] == "running" and receipt["pid"] == expected_pid
        assert receipt["engine_pid"] == 555

    asyncio.run(scenario())
