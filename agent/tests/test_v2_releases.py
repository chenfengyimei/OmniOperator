"""Immutable release preparation, continuity, crash fences and recovery with isolated real SQLite and fake managers."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_v2_deployment_files import installation as installation
from test_v2_service_operations import prepared

from omni.adapters.persistence.releases import ReleaseData
from omni.adapters.platform.services import deployment_files as d
from omni.adapters.platform.runtime.paths import InstanceLock, InstancePaths
from omni.adapters.platform.runtime.permissions import assert_private
from omni.adapters.platform.releases.release_files import ReleaseFiles
from omni.adapters.primitives import SystemClock, UUIDs
from omni.application.releases import Releases
from omni.composition.cli import parser
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError
from omni.domain.releases import FENCE_FILES, make_release, validate_release
from omni.domain.service_deployment import make_plan


class Manager:
    def __init__(self):
        self.installed = False
        self.cleanup = True
        self.calls = 0
        self.wait = None

    async def inspect(self, plan):
        self.calls += 1
        if self.wait is not None:
            await self.wait.wait()
        return {
            "available": True,
            "installed": self.installed,
            "definition_present": self.installed,
            "state": "stopped",
            "pid": None,
        }

    async def close(self, timeout_s):
        return self.cleanup

    async def change(self, plan, action):
        pytest.fail("A release directory selection cannot mutate service registration")


@pytest.fixture
def release(installation, tmp_path, monkeypatch):
    deployments, package, _assets, binary, _config = installation
    actual = Path(__file__).resolve().parents[1] / "src/omni"
    for name in FENCE_FILES:
        target = package / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((actual / name).read_bytes())
    source = asyncio.run(prepared(deployments, tmp_path))
    next_package = tmp_path / "next-program/omni"
    shutil.copytree(package, next_package)
    (next_package / "adapters/isolated.py").write_bytes(b"VALUE='next-version'\n")
    next_prefix = tmp_path / "next-environment"
    shutil.copytree(binary.parent.parent, next_prefix)
    current = {"package": next_package, "prefix": next_prefix}
    monkeypatch.setattr(d, "_installation", lambda: (current["package"], current["package"] / "web_assets", False))
    monkeypatch.setattr(
        d,
        "_python",
        lambda: {
            **d.python_files(current["prefix"] / "bin/python.exe", current["prefix"]),
            "python_version": sys.version.replace("\r\n", " ").replace("\n", " "),
        },
    )
    request = {**source["request"], "home": str(tmp_path / "next-home"), "bundle_dir": str(tmp_path / "next-bundle")}
    target = make_plan(request, deployments.capture(), UUIDs().new(), SystemClock().now())
    value = make_release(
        source,
        target,
        {"backup": str(tmp_path / "snapshot"), "journal": str(tmp_path / "journal")},
        UUIDs().new(),
        SystemClock().now(),
    )
    files = ReleaseFiles(deployments)
    manager = Manager()
    data = ReleaseData(files)
    service = Releases(deployments, files, data, manager, SystemClock(), UUIDs())
    return SimpleNamespace(
        plan=value,
        files=files,
        service=service,
        data=data,
        manager=manager,
        deployments=deployments,
        current=current,
        source_package=package,
        source_prefix=binary.parent.parent,
        root=tmp_path,
    )


def test_full_prepare_select_keeps_old_data_immutable_and_only_one_home_startable(release):
    r = release
    r.files.installations(r.plan)
    initial = hashlib.sha256(InstancePaths(Path(r.plan["source"]["request"]["home"])).database.read_bytes()).hexdigest()
    prepared_result = r.service.prepare(r.plan, r.plan["sha256"])
    assert prepared_result["state"] == "prepared" and r.manager.calls == 0
    source, target = r.files.homes(r.plan)
    assert hashlib.sha256(source.database.read_bytes()).hexdigest() == initial
    assert not (target.control / "owner.token").exists()
    assert_private(target.control / "release-fence.json")
    with pytest.raises(BusinessError, match="staged or retired"):
        InstanceLock(target).acquire()
    assert r.service.prepare(r.plan, r.plan["sha256"])["state"] == "prepared"  # Read-only repeat.
    assert asyncio.run(r.service.select(r.plan, r.plan["sha256"]))["state"] == "selected"
    assert r.manager.calls == 1 and not (target.control / "release-fence.json").exists()
    with pytest.raises(BusinessError, match="staged or retired"):
        InstanceLock(source).acquire()

    async def boot_target():
        instance = await open_instance(target.home, capabilities=())
        try:
            assert instance.identity["instance_id"] == r.plan["target"]["request"]["instance_id"]
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(boot_target())
    assert r.service.inspect(r.plan)["ready"] is False  # Selection is not runtime/service proof.


@pytest.mark.parametrize("changed", ["source", "target", "snapshot", "program", "fence"])
def test_drift_never_selects_or_replaces_original_content(release, changed):
    r = release
    r.service.prepare(r.plan, r.plan["sha256"])
    source, target = r.files.homes(r.plan)
    if changed in {"source", "target"}:
        home = source if changed == "source" else target
        (home.database.parent / "omni.db-journal").write_bytes(b"foreign journal; preserve")
    elif changed == "snapshot":
        (Path(r.plan["paths"]["backup"]) / "manifest.json").write_bytes(b"changed")
    elif changed == "program":
        (r.source_package / "adapters/isolated.py").write_bytes(b"changed")
    else:
        (target.control / "release-fence.json").unlink()
    with pytest.raises((BusinessError, ValueError)):
        asyncio.run(r.service.select(r.plan, r.plan["sha256"]))
    assert r.service.inspect(r.plan)["state"] == "prepared"
    assert not (source.control / "release-fence.json").exists() and r.manager.calls == 0


def test_registered_source_requires_separate_uninstall_and_never_invokes_changes(release):
    r = release
    r.service.prepare(r.plan, r.plan["sha256"])
    r.manager.installed = True
    with pytest.raises(BusinessError, match="Remove the stopped"):
        asyncio.run(r.service.select(r.plan, r.plan["sha256"]))
    assert r.service.inspect(r.plan)["state"] == "prepared"
    assert r.manager.calls == 1


@pytest.mark.parametrize("stage", ["restored", "source_retired", "target_activated"])
def test_interrupted_preparation_and_selection_preserve_fences_and_allow_only_proven_completion(
    release, monkeypatch, stage
):
    r = release
    if stage == "restored":
        original = r.deployments.materialize
        monkeypatch.setattr(r.deployments, "materialize", lambda *args: (_ for _ in ()).throw(OSError("injected")))
        with pytest.raises(OSError):
            r.service.prepare(r.plan, r.plan["sha256"])
        assert r.service.inspect(r.plan)["state"] == "restored"
        with pytest.raises(BusinessError, match="Preserve partial"):
            r.service.prepare(r.plan, r.plan["sha256"])
        monkeypatch.setattr(r.deployments, "materialize", original)
        assert r.service.abandon(r.plan, r.plan["sha256"])["state"] == "abandoned"
        assert r.files.homes(r.plan)[1].database.exists()  # No rollback deletion of user files.
        return
    r.service.prepare(r.plan, r.plan["sha256"])
    original = r.files.activate_target
    original_append = r.files.append
    if stage == "source_retired":
        monkeypatch.setattr(r.files, "activate_target", lambda *args: (_ for _ in ()).throw(OSError("injected")))
    else:

        def append(plan, phase, data, at):
            if phase == "selected":
                raise OSError("injected")
            original_append(plan, phase, data, at)

        monkeypatch.setattr(r.files, "append", append)
    with pytest.raises(OSError):
        asyncio.run(r.service.select(r.plan, r.plan["sha256"]))
    assert r.service.inspect(r.plan)["state"] == "selection_committed"
    source, _ = r.files.homes(r.plan)
    with pytest.raises(BusinessError, match="staged or retired"):
        InstanceLock(source).acquire()
    with pytest.raises(BusinessError, match="requires recovery"):
        r.service.abandon(r.plan, r.plan["sha256"])
    monkeypatch.setattr(r.files, "activate_target", original)
    monkeypatch.setattr(r.files, "append", original_append)
    # A fresh command can finish only these idempotent filesystem steps, not a native action.
    next_service = Releases(r.deployments, r.files, r.data, r.manager, SystemClock(), UUIDs())
    assert asyncio.run(next_service.select(r.plan, r.plan["sha256"]))["state"] == "selected"


def test_cancelled_query_and_cleanup_failure_keep_actual_instance_ownership(release):
    r = release
    r.service.prepare(r.plan, r.plan["sha256"])

    async def scenario():
        r.manager.wait = asyncio.Event()
        task = asyncio.create_task(r.service.select(r.plan, r.plan["sha256"]))
        while r.manager.calls == 0:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not await r.service.close(0)
        for home in r.files.homes(r.plan):
            with pytest.raises(BusinessError, match="already running"):
                InstanceLock(home).acquire(release=(r.plan["id"], r.plan["sha256"]))
        r.manager.cleanup = False
        r.manager.wait.set()
        await r.service._pending
        assert not await r.service.close(0)
        r.manager.cleanup = True
        assert await r.service.close(1)

    asyncio.run(scenario())


def test_data_changes_during_manager_wait_are_rechecked_before_retiring_source(release):
    r = release
    r.service.prepare(r.plan, r.plan["sha256"])

    async def scenario():
        r.manager.wait = asyncio.Event()
        task = asyncio.create_task(r.service.select(r.plan, r.plan["sha256"]))
        while r.manager.calls == 0:
            await asyncio.sleep(0)
        source = r.files.homes(r.plan)[0]
        (source.database.parent / "omni.db-journal").write_bytes(b"foreign change; preserve")
        r.manager.wait.set()
        with pytest.raises(BusinessError, match="Release data changed"):
            await task
        assert not (source.control / "release-fence.json").exists()
        assert r.service.inspect(r.plan)["state"] == "prepared"

    asyncio.run(scenario())


def test_recovery_restores_matching_original_snapshot_into_another_home_and_preserves_failed_version_data(release):
    r = release
    r.service.prepare(r.plan, r.plan["sha256"])
    asyncio.run(r.service.select(r.plan, r.plan["sha256"]))
    record = r.service.prepared(r.files.history(r.plan))
    r.current.update(package=r.source_package, prefix=r.source_prefix)
    request = {
        **r.plan["source"]["request"],
        "home": str(r.root / "recovery-home"),
        "bundle_dir": str(r.root / "recovery-bundle"),
    }
    target = make_plan(request, r.deployments.capture(), UUIDs().new(), SystemClock().now())
    value = make_release(
        r.plan["target"],
        target,
        {"backup": str(r.root / "failed-version-snapshot"), "journal": str(r.root / "recovery-journal")},
        UUIDs().new(),
        SystemClock().now(),
        {
            "release_sha256": r.plan["sha256"],
            "snapshot": record["backup"],
            "manifest_sha256": record["backup_manifest_sha256"],
            "isolation": None,
            "restore_policy": "original_snapshot",
            "expected_source_witness": record["target_witness"],
        },
    )
    recovery = Releases(r.deployments, r.files, r.data, r.manager, SystemClock(), UUIDs())
    assert recovery.prepare(value, value["sha256"])["state"] == "prepared"
    assert recovery.prepared(r.files.history(value))["restore_from"] == record["backup"]
    assert Path(value["paths"]["backup"], "manifest.json").exists()
    assert asyncio.run(recovery.select(value, value["sha256"]))["state"] == "selected"
    failed_home, restored_home = r.files.homes(value)
    with pytest.raises(BusinessError, match="staged or retired"):
        InstanceLock(failed_home).acquire()
    lock = InstanceLock(restored_home)
    lock.acquire()
    lock.close()


def test_plan_approval_bounds_and_cli_never_alias_an_installation_or_implicit_native_action(release):
    r = release
    assert validate_release(r.plan) == r.plan
    for key in ("starts_service", "stops_service", "overwrites_source"):
        changed = copy.deepcopy(r.plan)
        changed[key] = 0
        with pytest.raises(BusinessError):
            validate_release(changed)
    with pytest.raises(BusinessError, match="exact release"):
        r.service.prepare(r.plan, "0" * 64)
    assert not r.files.homes(r.plan)[1].home.exists()
    changed = copy.deepcopy(r.plan["target"])
    changed["request"]["home"] = r.plan["source"]["request"]["home"]
    changed = make_plan(changed["request"], changed["runtime"], changed["id"], changed["created_at"])
    with pytest.raises(BusinessError):
        make_release(r.plan["source"], changed, r.plan["paths"], r.plan["id"], r.plan["created_at"])
    args = parser().parse_args(["release", "prepare", "plan.json", "--approve", r.plan["sha256"]])
    assert args.release_command == "prepare"


def test_cli_composition_plans_prepares_selects_and_builds_a_separate_recovery_plan(release, monkeypatch):
    from omni.composition import releases as composition

    r = release
    monkeypatch.setattr(composition, "ServiceRegistration", Manager)
    request_file = r.root / "request.json"
    request = {"target": r.plan["target"]["request"], **r.plan["paths"]}
    request_file.write_text(json.dumps(request), encoding="utf-8")
    output = r.root / "release-plan.json"
    result = asyncio.run(
        composition.run_release(
            "plan", Path(r.plan["source"]["request"]["bundle_dir"]), request=request_file, output=output
        )
    )
    assert result["executed"] is False
    assert asyncio.run(composition.run_release("prepare", output, approval=result["sha256"]))["state"] == "prepared"
    assert asyncio.run(composition.run_release("select", output, approval=result["sha256"]))["state"] == "selected"
    r.current.update(package=r.source_package, prefix=r.source_prefix)
    request["target"] = {
        **r.plan["source"]["request"],
        "home": str(r.root / "recovery-cli-home"),
        "bundle_dir": str(r.root / "recovery-cli-bundle"),
    }
    request.update(backup=str(r.root / "recovery-cli-snapshot"), journal=str(r.root / "recovery-cli-journal"))
    request_file.write_text(json.dumps(request), encoding="utf-8")
    recovery_output = r.root / "recovery-cli.json"
    recovery = asyncio.run(
        composition.run_release("recovery-plan", output, request=request_file, output=recovery_output)
    )
    assert recovery["executed"] is False
    value = r.deployments.read(recovery_output)
    assert value["recovery"]["release_sha256"] == result["sha256"]
    assert value["target"]["runtime"] == r.plan["source"]["runtime"]


@pytest.mark.parametrize("bad_role", [[], False, "other"])
def test_malformed_or_foreign_startup_fence_never_opens_the_instance(tmp_path, bad_role):
    from omni.adapters.platform.services.deployment_files import CurrentDeploymentFiles, _json

    paths = InstancePaths(tmp_path / "fenced")
    paths.initialize()
    marker = {"format": "omni-release-fence-1", "release_id": UUIDs().new(), "plan_sha256": "a" * 64, "role": bad_role}
    CurrentDeploymentFiles()._publish_json(paths.control / "release-fence.json", _json(marker))
    with pytest.raises(BusinessError):
        InstanceLock(paths).acquire()


def test_source_root_inspection_routing_is_explicit_and_bound_without_calling_change(tmp_path, monkeypatch):
    from test_v2_service_deployment import plan as service_plan

    from omni.composition import releases as composition
    from omni.domain.service_admin import socket_path
    from omni.domain.service_operations import make_operation

    source = service_plan("linux-system")
    source["runtime"]["files"] += [{"path": name, "sha256": "c" * 64, "bytes": 3} for name in FENCE_FILES]
    source = make_plan(source["request"], source["runtime"], source["id"], source["created_at"])
    runtime = {
        **source["runtime"],
        "python": "/opt/new/bin/python",
        "python_realpath": "/opt/new/bin/python",
        "python_prefix": "/opt/new",
        "package_root": "/opt/new/site/omni",
    }
    target = make_plan(
        {**source["request"], "home": "/home/example/next-home", "bundle_dir": "/home/example/next-bundle"},
        runtime,
        UUIDs().new(),
        SystemClock().now(),
    )
    plan = make_release(
        source,
        target,
        {"backup": "/home/example/snapshot", "journal": "/home/example/journal"},
        UUIDs().new(),
        SystemClock().now(),
    )
    operation = make_operation(source, "uninstall", UUIDs().new(), SystemClock().now())
    plan_path, op_path = tmp_path / "plan.json", tmp_path / "operation.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    op_path.write_text(json.dumps(operation), encoding="utf-8")
    calls = []

    def client(value, endpoint, bundle_sha):
        assert (
            value == operation and endpoint == str(Path(socket_path(operation)).absolute()) and bundle_sha == "a" * 64
        )
        calls.append("pinned_client")
        return Manager()

    class Application:
        def __init__(self, deployments, files, data, manager, clock, ids, *, drain=None):
            assert drain is None
            self.manager = manager

        async def select(self, value, approval):
            assert value == plan and approval == plan["sha256"]
            await self.manager.inspect(source)
            return {"state": "selected"}

        async def close(self, timeout):
            return await self.manager.close(timeout)

    monkeypatch.setattr(composition, "Releases", Application)
    from omni.adapters.platform.services import service_admin_client

    monkeypatch.setattr(service_admin_client, "PrivilegedServiceClient", client)
    with pytest.raises(BusinessError, match="together"):
        asyncio.run(composition.run_release("select", plan_path, admin_socket=tmp_path / "not-a-real-socket"))
    result = asyncio.run(
        composition.run_release(
            "select",
            plan_path,
            approval=plan["sha256"],
            source_operation=op_path,
            admin_socket=Path(socket_path(operation)),
            admin_bundle_sha256="a" * 64,
        )
    )
    assert result["state"] == "selected" and calls == ["pinned_client"]
