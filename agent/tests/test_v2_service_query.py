"""Read-only manager protocols and bounded process ownership; never invokes a real OS manager."""

import asyncio
import json
import os
import threading
import uuid
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from omni.adapters.platform import service_query as q
from omni.adapters.platform.service_templates import render
from omni.domain.service_deployment import make_plan, manager_identity


def plan(target):
    windows = target == "windows-user"
    prefix = "C:/fixture" if windows else "/fixture"
    principal = {"name": "owner", "id": "S-1-5-21-1000" if windows else "1000", "home": prefix + "/owner"}
    request = {
        "format": "omni-service-request-1",
        "target": target,
        "instance_id": str(uuid.uuid4()),
        "home": prefix + "/data",
        "working_directory": prefix + "/work",
        "bundle_dir": prefix + "/bundle",
        "principal": principal,
        "host": "127.0.0.1",
        "port": 8765,
        "external_url": None,
        "master_key_file": None,
        "stdout_logs": True,
    }
    runtime = {
        "format": "omni-service-runtime-2",
        "platform": "win32" if windows else "darwin" if target.startswith("macos") else "linux",
        "python": prefix + "/python.exe" if windows else prefix + "/python",
        "python_realpath": prefix + "/python.exe" if windows else prefix + "/python",
        "python_prefix": prefix + "/python-home",
        "python_links": [],
        "venv_config": None,
        "python_sha256": "a" * 64,
        "process_python": prefix + "/python.exe" if windows else prefix + "/python",
        "process_python_sha256": "a" * 64,
        "python_version": "3.12.0",
        "architecture": "x64",
        "package_root": prefix + "/package",
        "build_id": "b" * 64,
        "assets_sha256": "c" * 64,
        "api_version": 1,
        "schema_head": "v2_0026",
        "editable": False,
        "principal": principal,
        "files": [{"path": "composition/service_runner.py", "sha256": "d" * 64, "bytes": 1}],
        "distributions": [],
    }
    return make_plan(request, runtime, str(uuid.uuid4()), "2026-11-05T00:00:00+00:00")


def native(monkeypatch, target):
    monkeypatch.setattr(
        q.sys, "platform", "win32" if target == "windows-user" else "darwin" if target.startswith("macos-") else "linux"
    )
    monkeypatch.setattr(q.os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr(q.ServiceQuery, "_program", staticmethod(lambda _: "/fixed/manager"))


def systemd(data, **changes):
    identity = manager_identity(data)
    fields = {
        "Id": identity["name"],
        "LoadState": "loaded",
        "ActiveState": "active",
        "SubState": "running",
        "MainPID": "42",
        "FragmentPath": identity["installed_path"],
        "SourcePath": "",
        "DropInPaths": "",
        "NeedDaemonReload": "no",
        "Transient": "no",
    }
    return "".join(f"{name}={value}\n" for name, value in {**fields, **changes}.items()).encode()


@pytest.mark.parametrize("target", ["windows-user", "macos-system", "linux-user", "linux-system"])
def test_native_targets_use_only_fixed_query_argv_and_conservative_definition_proof(monkeypatch, target):
    async def scenario():
        data = plan(target)
        identity = manager_identity(data)
        artifact = render(data)[identity["artifact"]]
        native(monkeypatch, target)
        inspector = q.ServiceQuery()
        commands, reads = [], []

        async def command(argv):
            commands.append(argv)
            if target == "windows-user":
                return 0, artifact.decode().replace("utf-8", "utf-16").encode("utf-16")
            if target.startswith("macos-"):
                from test_v2_macos_services import job, native_response

                return 0, json.dumps(native_response(data, argv[-1], data=job(data, PID=42))).encode()
            return 0, systemd(data)

        async def read(path):
            reads.append(path.as_posix())
            return artifact

        monkeypatch.setattr(inspector, "_command", command)
        monkeypatch.setattr(inspector, "_file", read)
        if target == "macos-system":

            async def helper():
                return Path("/fixed/helper")

            monkeypatch.setattr(inspector, "_native_helper", helper)
        try:
            result = await inspector.query(data)
            assert result["available"] and result["installed"]
            if target == "windows-user":
                assert commands == [["/fixed/manager", "/Query", "/TN", identity["name"], "/XML"]]
                assert result["definition_matches"] is True and result["state"] == "unknown" and result["pid"] is None
                assert reads == []
            elif target.startswith("macos-"):
                assert len(commands) == 1
                assert commands[0][:4] == [
                    str(Path("/fixed/helper")),
                    "--system-service-query",
                    identity["name"],
                    "1000",
                ]
                assert result["definition_matches"] is True and result["error_code"] == ""
                assert result["state"] == "running" and result["pid"] == 42
            else:
                assert commands[0][:4] == (
                    ["/fixed/manager", "--user", "show", identity["name"]]
                    if target == "linux-user"
                    else ["/fixed/manager", "show", identity["name"], "--no-pager"]
                )
                assert result["definition_matches"] is True and result["state"] == "running" and result["pid"] == 42
            assert not {"start", "stop", "enable", "install", "bootstrap", "daemon-reload"} & set(commands[0])
        finally:
            assert await inspector.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "field", ["Command", "Arguments", "WorkingDirectory", "UserId", "LogonType", "RunLevel", "extra_action"]
)
def test_windows_task_action_and_principal_drift_cannot_match(monkeypatch, field):
    async def scenario():
        data = plan("windows-user")
        native(monkeypatch, "windows-user")
        tree = ET.fromstring(render(data)["service.xml"])
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        if field == "extra_action":
            ET.SubElement(tree.find("t:Actions", ns), "{" + ns["t"] + "}ComHandler")
        else:
            tree.find(
                "t:Principals/t:Principal/t:UserId" if field == "UserId" else ".//t:" + field, ns
            ).text = "drifted"
        inspector = q.ServiceQuery()

        async def command(_argv):
            return 0, ET.tostring(tree)

        monkeypatch.setattr(inspector, "_command", command)
        try:
            result = await inspector.query(data)
            assert result["installed"] is True and result["definition_matches"] is not True
            assert result["state"] == "unknown"
        finally:
            assert await inspector.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "changes",
    [
        {"DropInPaths": "/run/override.conf"},
        {"NeedDaemonReload": "yes"},
        {"SourcePath": "/etc/init.d/foreign"},
        {"Transient": "yes"},
        {"FragmentPath": "/private/owner.token"},
    ],
)
def test_linux_drift_dropins_and_unexpected_paths_never_read_or_claim_expected_definition(monkeypatch, changes):
    async def scenario():
        data = plan("linux-user")
        native(monkeypatch, "linux-user")
        inspector = q.ServiceQuery()

        async def command(_argv):
            return 0, systemd(data, **changes)

        async def forbidden(_path):
            raise AssertionError("Unverified manager path must never be read")

        monkeypatch.setattr(inspector, "_command", command)
        monkeypatch.setattr(inspector, "_file", forbidden)
        try:
            result = await inspector.query(data)
            assert result["installed"] is True and result["definition_matches"] is not True
        finally:
            assert await inspector.close()

    asyncio.run(scenario())


def test_absence_and_runtime_states_are_independent_and_duplicate_output_is_rejected(monkeypatch):
    async def scenario():
        data = plan("linux-system")
        native(monkeypatch, "linux-system")
        inspector = q.ServiceQuery()
        payloads = [systemd(data, LoadState="not-found", FragmentPath=""), systemd(data) + b"MainPID=999\n"]

        async def command(_argv):
            return 0, payloads.pop(0)

        monkeypatch.setattr(inspector, "_command", command)
        try:
            absent = await inspector.query(data)
            assert (
                absent["installed"] is False and absent["state"] == "running" and absent["definition_matches"] is None
            )
            assert (await inspector.query(data))["error_code"] == "service_manager_output_invalid"
        finally:
            assert await inspector.close()

    asyncio.run(scenario())


def test_foreign_platform_or_user_never_spawns_manager(monkeypatch):
    async def scenario():
        inspector = q.ServiceQuery()

        async def forbidden(_argv):
            raise AssertionError("Foreign manager must not execute")

        monkeypatch.setattr(inspector, "_command", forbidden)
        try:
            monkeypatch.setattr(q.sys, "platform", "win32")
            assert (await inspector.query(plan("macos-user")))["error_code"] == "service_target_unsupported"
            monkeypatch.setattr(q.sys, "platform", "linux")
            monkeypatch.setattr(q.os, "geteuid", lambda: 1001, raising=False)
            assert (await inspector.query(plan("linux-user")))["error_code"] == "service_principal_unverified"
        finally:
            assert await inspector.close()

    asyncio.run(scenario())


def test_definition_reader_rejects_links_and_changed_payload_without_modifying_files(tmp_path):
    definition = tmp_path / "fixed.service"
    definition.write_bytes(b"unit payload")
    assert q._read_definition(definition) == b"unit payload"
    hardlinked = tmp_path / "second.service"
    os.link(definition, hardlinked)
    with pytest.raises(OSError):
        q._read_definition(definition)
    assert definition.read_bytes() == hardlinked.read_bytes() == b"unit payload"
    oversized = tmp_path / "big.service"
    oversized.write_bytes(b"x" * (q.MAX_OUTPUT + 1))
    with pytest.raises(OSError):
        q._read_definition(oversized)


class Process:
    def __init__(self, payload=b"", *, eof=True):
        self.stdout, self.stderr = asyncio.StreamReader(), asyncio.StreamReader()
        self.stdout.feed_data(payload)
        if eof:
            self.stdout.feed_eof()
            self.stderr.feed_eof()
        self.returncode = 0 if eof else None

    async def wait(self):
        return self.returncode or 0


class Owner:
    def __init__(self, payload=b"", *, eof=True, outcomes=()):
        self.process = Process(payload, eof=eof)
        self.outcomes, self.reclaimed = list(outcomes), False

    async def terminate(self, _timeout):
        if self.outcomes and not self.outcomes.pop(0):
            return False
        self.reclaimed = True
        self.process.returncode = 0
        if not self.process.stdout.at_eof():
            self.process.stdout.feed_eof()
        if not self.process.stderr.at_eof():
            self.process.stderr.feed_eof()
        return True


def spawn(monkeypatch, owner, *, gate=None, started=None):
    calls = []

    async def owned(_cls, argv, **kwargs):
        calls.append((argv, kwargs))
        if started:
            started.set()
        if gate:
            await gate.wait()
        return owner

    monkeypatch.setattr(q.OwnedProcess, "spawn", classmethod(owned))
    return calls


def test_cancelled_query_retains_spawn_cleanup_and_slot_until_actual_reclamation(monkeypatch):
    async def scenario():
        native(monkeypatch, "windows-user")
        data = plan("windows-user")
        gate, started = asyncio.Event(), asyncio.Event()
        owner = Owner(render(data)["service.xml"])
        calls = spawn(monkeypatch, owner, gate=gate, started=started)
        inspector = q.ServiceQuery()
        caller = asyncio.create_task(inspector.query(data))
        await started.wait()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert inspector.health()["pending"] == inspector.health()["resident"] == 1
        assert not await inspector.close(0)
        gate.set()
        assert await inspector.close(2)
        assert len(calls) == 1 and owner.reclaimed and not inspector.health()["resident"]

    asyncio.run(scenario())


def test_failed_cleanup_retains_owner_and_retry_cannot_claim_a_matched_service(monkeypatch):
    async def scenario():
        native(monkeypatch, "windows-user")
        data = plan("windows-user")
        owner = Owner(render(data)["service.xml"], outcomes=(False, False, True))
        spawn(monkeypatch, owner)
        inspector = q.ServiceQuery()
        result = await inspector.query(data)
        assert result["error_code"] == "service_query_cleanup_pending" and result["definition_matches"] is None
        assert inspector.health()["resident"] == inspector.health()["cleanup_pending"] == 1
        assert not await inspector.close(1) and not owner.reclaimed
        assert await inspector.close(1) and owner.reclaimed

    asyncio.run(scenario())


def test_manager_output_limit_and_stderr_are_not_returned_as_diagnostic_content(monkeypatch, caplog):
    async def scenario():
        native(monkeypatch, "windows-user")
        owner = Owner()
        owner.process.stderr = asyncio.StreamReader()
        owner.process.stderr.feed_data(b"private-manager-stderr-canary" * 40000)
        owner.process.stderr.feed_eof()
        spawn(monkeypatch, owner)
        inspector = q.ServiceQuery()
        try:
            result = await inspector.query(plan("windows-user"))
            assert result["error_code"] == "service_query_output_limit"
            assert "private-manager" not in repr(result) + caplog.text
        finally:
            assert await inspector.close(1)

    asyncio.run(scenario())


def test_manager_five_second_deadline_reclaims_stalled_pipes_without_shortening_timeout(monkeypatch):
    async def scenario():
        native(monkeypatch, "windows-user")
        owner = Owner(eof=False)
        spawn(monkeypatch, owner)
        inspector = q.ServiceQuery()
        try:
            async with asyncio.timeout(10):
                result = await inspector.query(plan("windows-user"))
            assert result["error_code"] == "service_query_timeout" and owner.reclaimed
        finally:
            assert await inspector.close(1)

    asyncio.run(scenario())


def test_eight_actual_queries_and_two_resident_spawns_remain_bounded_after_cancel(monkeypatch):
    async def scenario():
        native(monkeypatch, "windows-user")
        data = plan("windows-user")
        release, owners = asyncio.Event(), []

        async def owned(_cls, _argv, **_kwargs):
            owner = Owner(render(data)["service.xml"])
            owners.append(owner)
            await release.wait()
            return owner

        monkeypatch.setattr(q.OwnedProcess, "spawn", classmethod(owned))
        inspector = q.ServiceQuery()
        callers = [asyncio.create_task(inspector.query(data)) for _ in range(8)]
        try:
            async with asyncio.timeout(5):
                while inspector.health()["pending"] != 8 or len(owners) != 2:
                    await asyncio.sleep(0)
            assert (await inspector.query(data))["error_code"] == "service_query_busy"
            assert inspector.health()["resident"] == 2
            assert not await inspector.close(0)
        finally:
            release.set()
            await asyncio.gather(*callers, return_exceptions=True)
            assert await inspector.close(2)
        assert len(owners) == 2 and all(owner.reclaimed for owner in owners)
        assert not inspector.health()["resident"] and not inspector.health()["pending"]

    asyncio.run(scenario())


def test_cancelled_file_read_keeps_actual_bounded_future_until_close_can_finish(monkeypatch, tmp_path):
    async def scenario():
        release, started = threading.Event(), threading.Event()

        def read(_path):
            started.set()
            assert release.wait(10)
            return b"fixed definition"

        monkeypatch.setattr(q, "_read_definition", read)
        inspector = q.ServiceQuery()
        caller = asyncio.create_task(inspector._file(tmp_path / "managed.service"))
        try:
            async with asyncio.timeout(5):
                while not started.is_set():
                    await asyncio.sleep(0)
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert inspector.health()["file_reads"] == 1
            assert not await inspector.close(0)
        finally:
            release.set()
            await asyncio.gather(caller, return_exceptions=True)
            assert await inspector.close(2)
        assert not inspector.health()["file_reads"]

    asyncio.run(scenario())
