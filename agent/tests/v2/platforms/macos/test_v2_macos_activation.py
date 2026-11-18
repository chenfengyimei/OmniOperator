"""Mac activation with real private journals/locks and injected logical paths/native IO; never launches a service."""

from __future__ import annotations

from omni_test_support.v2.macos_activation import (
    FENCE_FILES as FENCE_FILES,
)
from omni_test_support.v2.macos_activation import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.macos_activation import (
    Clock as Clock,
)
from omni_test_support.v2.macos_activation import (
    CurrentDeploymentFiles as CurrentDeploymentFiles,
)
from omni_test_support.v2.macos_activation import (
    ExchangeFiles as ExchangeFiles,
)
from omni_test_support.v2.macos_activation import (
    ExchangeSteps as ExchangeSteps,
)
from omni_test_support.v2.macos_activation import (
    InstanceLock as InstanceLock,
)
from omni_test_support.v2.macos_activation import (
    InstancePaths as InstancePaths,
)
from omni_test_support.v2.macos_activation import (
    Path as Path,
)
from omni_test_support.v2.macos_activation import (
    ReleaseFiles as ReleaseFiles,
)
from omni_test_support.v2.macos_activation import (
    ServiceControl as ServiceControl,
)
from omni_test_support.v2.macos_activation import (
    ServiceExchange as ServiceExchange,
)
from omni_test_support.v2.macos_activation import (
    ServiceOperations as ServiceOperations,
)
from omni_test_support.v2.macos_activation import (
    SystemClock as SystemClock,
)
from omni_test_support.v2.macos_activation import (
    UUIDs as UUIDs,
)
from omni_test_support.v2.macos_activation import (
    acceptance_script as acceptance_script,
)
from omni_test_support.v2.macos_activation import (
    activation_binding as activation_binding,
)
from omni_test_support.v2.macos_activation import (
    asyncio as asyncio,
)
from omni_test_support.v2.macos_activation import (
    claimed as claimed,
)
from omni_test_support.v2.macos_activation import (
    copy as copy,
)
from omni_test_support.v2.macos_activation import (
    exchange_plan as exchange_plan,
)
from omni_test_support.v2.macos_activation import (
    json as json,
)
from omni_test_support.v2.macos_activation import (
    launch as launch,
)
from omni_test_support.v2.macos_activation import (
    make_control as make_control,
)
from omni_test_support.v2.macos_activation import (
    make_exchange as make_exchange,
)
from omni_test_support.v2.macos_activation import (
    make_operation as make_operation,
)
from omni_test_support.v2.macos_activation import (
    make_plan as make_plan,
)
from omni_test_support.v2.macos_activation import (
    make_release as make_release,
)
from omni_test_support.v2.macos_activation import (
    marker as marker,
)
from omni_test_support.v2.macos_activation import (
    native as native,
)
from omni_test_support.v2.macos_activation import (
    open_instance as open_instance,
)
from omni_test_support.v2.macos_activation import (
    pytest as pytest,
)
from omni_test_support.v2.macos_activation import (
    request as request,
)
from omni_test_support.v2.macos_activation import (
    runtime as runtime,
)
from omni_test_support.v2.macos_activation import (
    service_control_files as service_control_files,
)
from omni_test_support.v2.macos_activation import (
    service_operation_files as service_operation_files,
)
from omni_test_support.v2.macos_activation import (
    stage_identity as stage_identity,
)
from omni_test_support.v2.macos_activation import (
    start_postcondition as start_postcondition,
)
from omni_test_support.v2.macos_activation import (
    startup as startup,
)
from omni_test_support.v2.macos_activation import (
    validate_exchange as validate_exchange,
)
from omni_test_support.v2.macos_activation import (
    validate_reservation as validate_reservation,
)


def test_macos_plan_binds_boot_and_advertises_unload_and_abandon_effects():
    plan = exchange_plan()
    assert validate_exchange(plan) == plan and plan["format"] == "omni-service-exchange-4"
    assert plan["starts_service"] is True and plan["stops_service"] is True
    assert plan["releases_activation_on_abandon"] is True and plan["automatic_rollback"] is False
    value = marker(plan)
    assert validate_reservation(value) == value
    assert value["activation"] == activation_binding(plan["start_target"])
    for field in ("stops_service", "releases_activation_on_abandon"):
        changed = copy.deepcopy(plan)
        changed[field] = 1
        with pytest.raises(BusinessError):
            validate_exchange(changed)


def test_claim_blocks_source_login_and_unarmed_target_before_any_launch_receipt(claimed):
    plan, _, source, target = claimed
    for home, code in ((source, "service_activation_source_blocked"), (target, "service_activation_pending")):
        lock = InstanceLock(home)
        lock.acquire()
        try:
            with pytest.raises(BusinessError) as caught:
                launch.consume_launch(home.control, startup(plan["release"]["target"]))
            assert caught.value.code == code
            assert not (home.control / "service-launches").exists()
        finally:
            lock.close()


def test_only_reserved_target_control_can_be_claimed_once_and_not_by_development_entry(claimed):
    plan, _, _, target = claimed
    control = plan["start_target"]
    lock = InstanceLock(target)
    lock.acquire()
    try:
        other = make_control(control["deployment"], "start", UUIDs().new(), SystemClock().now(), UUIDs().new())
        with pytest.raises(BusinessError) as caught:
            launch.reserve_launch(target.control, other)
        assert caught.value.code == "service_activation_mismatch"
        launch.reserve_launch(target.control, control)
        with pytest.raises(BusinessError):
            launch.consume_launch(target.control, None)
        assert launch.consume_launch(target.control, startup(control["deployment"])) == control["boot_id"]
        claim = target.control / "service-launches" / (control["id"] + ".json")
        data = claim.read_bytes()
        with pytest.raises(BusinessError) as caught:
            launch.consume_launch(target.control, startup(control["deployment"]))
        assert caught.value.code == "service_launch_consumed" and claim.read_bytes() == data
    finally:
        lock.close()


@pytest.mark.parametrize("damage", ["boot", "home", "instance", "sha", "disabled", "extra", "old", "oversize"])
def test_corrupt_or_wrong_binding_cannot_fall_back_to_random_boot(claimed, damage):
    plan, _, _, target = claimed
    path = target.control / "service-exchange.json"
    value = json.loads(path.read_bytes())
    if damage == "boot":
        value["activation"]["boot_id"] = UUIDs().new()
    elif damage == "home":
        value["activation"]["home"] += "-foreign"
    elif damage == "instance":
        value["activation"]["instance_id"] = UUIDs().new()
    elif damage == "sha":
        value["sha256"] = "b" * 64
    elif damage == "disabled":
        value["activation"] = None
    elif damage == "extra":
        value["bypass"] = True
    elif damage == "old":
        value["format"] = "omni-service-exchange-2"
    else:
        value["unbounded"] = "x" * 8192
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises((BusinessError, OSError)):
        launch.consume_launch(target.control, startup(plan["release"]["target"]))
    assert not (target.control / "service-launches").exists()


def test_claim_interruption_before_intent_is_resumable_but_missing_live_reservation_is_not(claimed):
    plan, files, _, target = claimed
    path = target.control / "service-exchange.json"
    path.unlink()
    with files.exclusive(plan):
        files.claim(plan)  # Both marker publication and intent may be interrupted.
        assert json.loads(path.read_bytes()) == marker(plan)
        files.append(plan, {"state": "intent", "stage": "", "recorded_at": SystemClock().now(), "proof": {}})
    path.unlink()
    with files.exclusive(plan), pytest.raises(BusinessError) as caught:
        files.claim(plan)
    assert caught.value.code == "service_exchange_reservation_missing"


@pytest.mark.parametrize("ending", ["finished", "abandoned"])
def test_terminal_witness_releases_activation_only_after_child_cleanup_and_is_repeatable(claimed, ending):
    plan, files, source, target = claimed
    at = SystemClock().now()
    with files.exclusive(plan):
        files.append(plan, {"state": "intent", "stage": "", "recorded_at": at, "proof": {}})
        if ending == "finished":
            for stage in ("remove_source", "select_target", "install_target", "start_target"):
                identity, sha = stage_identity(plan, stage)
                files.append(plan, {"state": "dispatch", "stage": stage, "recorded_at": at, "proof": {}})
                proof = {
                    "stage": stage,
                    "operation_id": identity,
                    "operation_sha256": sha,
                    "succeeded": True,
                    "cleanup_complete": True,
                    "error_code": "",
                    "verification": "injected",
                }
                files.append(plan, {"state": "complete", "stage": stage, "recorded_at": at, "proof": proof})
        files.append(plan, {"state": ending, "stage": "", "recorded_at": at, "proof": {}})
        # A terminal parent witness never overrides a still-active child.
        child = target.control / "service-controls"
        child.mkdir()
        active = child / "active.json"
        CurrentDeploymentFiles()._publish_json(active, b"{}")
        with pytest.raises(BusinessError):
            files.settled(plan)
        assert (target.control / "service-exchange.json").exists()
        active.unlink()
        files.settled(plan)
        files.settled(plan)
    assert all(not (home.control / "service-exchange.json").exists() for home in (source, target))
    assert launch.consume_launch(target.control, None) != plan["start_target"]["boot_id"]


def test_unarmed_exchange_rejects_real_core_before_business_database_creation(claimed):
    _, _, _, target = claimed
    with pytest.raises(BusinessError) as caught:
        asyncio.run(open_instance(target.home, capabilities=()))
    assert caught.value.code == "service_activation_pending"
    assert not target.database.exists() and not (target.control / "owner.token").exists()
    lock = InstanceLock(target)
    lock.acquire()
    lock.close()


@pytest.mark.parametrize("pid,accepted", [(None, False), (True, False), (43, False), (42, True)])
def test_macos_readiness_requires_native_loaded_pid_not_only_owner_proof(pid, accepted):
    deployment = exchange_plan()["release"]["target"]
    report = {
        "available": True,
        "installed": True,
        "definition_present": True,
        "definition_matches": True,
        "enabled": True,
        "state": "running",
        "pid": pid,
    }
    assert start_postcondition(deployment, report, {"ready": True, "pid": 42}) is accepted


def test_macos_registration_keeps_pid_from_fixed_native_inspector(monkeypatch, tmp_path):
    async def scenario():
        deployment = exchange_plan()["release"]["target"]
        service = native.ServiceRegistration()
        path = tmp_path / "target.plist"
        path.write_bytes(native.render(deployment)["agent.plist"])
        manager = native.manager_identity(deployment)

        async def probe(self, _):
            return {"loaded": True, "loaded_definition_matches": True, "state": "running", "pid": 42, "error_code": ""}

        async def command(argv):
            assert argv == ["/fixed/launchctl", "print-disabled", manager["domain"]]
            return 0, b"disabled services = {\n}\n"

        monkeypatch.setattr(service, "_check_unit_path", lambda *args: None)
        monkeypatch.setattr(native, "manager_identity", lambda _: {**manager, "installed_path": str(path)})
        monkeypatch.setattr(native.MacOSServiceInspection, "probe", probe)
        monkeypatch.setattr(service, "_program", lambda _: "/fixed/launchctl")
        monkeypatch.setattr(service, "_command", command)
        try:
            assert (await service._inspect_macos(deployment))["pid"] == 42
        finally:
            assert await service.close(1)

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["none", "unknown_boot", "cancelled_waiter"])
def test_complete_mac_exchange_uses_existing_kernel_and_never_replays_activation(claimed, monkeypatch, fault):
    plan, files, source, target = claimed
    ids, clock, calls = UUIDs(), Clock(), []
    deployment = plan["release"]["target"]
    for home in (source, target):
        home.database.touch()  # Only storage identity below is injected, not launch or journal behavior.

    def verify(path, identity, *, schema_version=None):
        assert path in {source.database, target.database}
        assert identity == deployment["request"]["instance_id"]
        assert schema_version in {None, deployment["runtime"]["schema_head"]}

    monkeypatch.setattr(service_control_files, "verify_deployed_instance", verify)
    monkeypatch.setattr(service_operation_files, "verify_deployed_instance", verify)

    class Deployments:
        def verify(self, bundle):
            exact = next(
                item for item in (plan["release"]["source"], deployment) if item["request"]["bundle_dir"] == bundle
            )
            return {"valid": True, "eligible": True, "plan": exact}

    class Manager:
        def __init__(self, home, installed):
            self.home, self.installed, self.lock = home, installed, None
            self.identity = None
            self.release = self.entered = None

        async def inspect(self, _):
            return {
                "available": True,
                "installed": self.installed,
                "definition_present": self.installed,
                "definition_matches": self.installed,
                "enabled": self.installed,
                "state": "running" if self.lock else "stopped",
                "pid": 42 if self.lock else None,
            }

        async def change(self, _, action):
            calls.append(action)
            with pytest.raises(BusinessError):
                InstanceLock(self.home).acquire()
            self.installed = action == "install"
            return {"effect": "applied"}

        async def start(self, _):
            calls.append("start")
            if self.entered is not None:
                self.entered.set()
                await self.release.wait()
            self.lock = InstanceLock(self.home)
            self.lock.acquire()  # The control coordinator must release the instance lock before activation.
            boot = launch.consume_launch(self.home.control, startup(deployment))
            self.identity = {"ready": True, "pid": 42, "boot_id": boot}
            return {"effect": "applied"}

        async def close(self, timeout):
            return True  # This transport close does not close the fake running app.

    class Boot:
        wrong = fault == "unknown_boot"

        async def probe(self, _):
            if target_manager.identity is None:
                return {"ready": False}
            return {
                **target_manager.identity,
                "boot_id": ids.new() if self.wrong else target_manager.identity["boot_id"],
            }

        async def close(self, timeout):
            return True

    class Release:
        files = type("Files", (), {"installations": lambda *args: None})()

        async def select(self, value, approval):
            assert value == plan["release"] and approval == value["sha256"]
            calls.append("select")
            return {"state": "selected"}

        async def close(self, timeout):
            return True

    source_manager, target_manager, boot = Manager(source, True), Manager(target, False), Boot()

    def build():
        def mapped(owner, home):
            def paths(operation):
                owner._validate(operation)
                root = home.control / owner.namespace
                return home, root, root / operation["id"]

            monkeypatch.setattr(owner, "_paths", paths)
            return owner

        def registration(home, manager):
            journal = mapped(service_operation_files.ServiceOperationFiles(exchange=marker(plan)), home)
            return ServiceOperations(Deployments(), journal, manager, clock, ids)

        controls = mapped(service_control_files.ServiceControlFiles(exchange=marker(plan)), target)
        control = ServiceControl(Deployments(), controls, target_manager, boot, clock, ids)

        class Steps(ExchangeSteps):
            def prepared(self, _):
                pass  # Prepared snapshot/immutable installations are injected; the actual kernel is exercised.

            async def perform(self, value, stage, *, reconcile):
                result = await super().perform(value, stage, reconcile=reconcile)
                if stage == "install_target":
                    guard = InstanceLock(target)
                    guard.acquire()
                    try:
                        with pytest.raises(BusinessError) as caught:
                            launch.consume_launch(target.control, startup(deployment))
                        assert caught.value.code == "service_activation_pending"
                    finally:
                        guard.close()
                return result

        steps = Steps(registration(source, source_manager), registration(target, target_manager), Release(), control)
        return ServiceExchange(files, steps, clock, ids)

    async def scenario():
        service = build()
        try:
            if fault == "cancelled_waiter":
                target_manager.entered, target_manager.release = asyncio.Event(), asyncio.Event()
                waiter = asyncio.create_task(service.apply(plan, plan["sha256"]))
                await target_manager.entered.wait()
                waiter.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await waiter
                assert not await service.close(0) and (target.control / "service-exchange.json").exists()
                target_manager.release.set()
                result = await service._pending
            else:
                result = await service.apply(plan, plan["sha256"])
            if fault == "unknown_boot":
                assert result["state"] == "blocked" and (target.control / "launch-ticket.json").exists()
                assert (target.control / "service-exchange.json").exists()
                assert await service.close(1)
                boot.wrong = False
                service = build()
                result = await service.apply(plan, plan["sha256"], resume=True)
            assert result["state"] == "finished" and result["ready_at_completion"] is True
            assert target_manager.identity["boot_id"] == plan["start_target"]["boot_id"]
            assert calls == ["uninstall", "select", "install", "start"]
            assert not (target.control / "service-exchange.json").exists()
        finally:
            assert await service.close(1)
            if target_manager.lock:
                target_manager.lock.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["none", "boot", "pid", "history", "unfinished", "cleanup"])
@pytest.mark.parametrize("target", ["macos-user", "macos-system"])
def test_mac_evidence_binds_current_reserved_boot_and_preserves_actual_query_cleanup(monkeypatch, fault, target):
    script = acceptance_script()
    plan, calls = exchange_plan(target), []
    deployment, boot = plan["release"]["target"], plan["start_target"]["boot_id"]

    def history():
        at = SystemClock().now()
        rows = [{"state": "intent", "stage": "", "recorded_at": at, "proof": {}}]
        for stage in ("remove_source", "select_target", "install_target", "start_target"):
            identity, sha = stage_identity(plan, stage)
            rows += [
                {"state": "dispatch", "stage": stage, "recorded_at": at, "proof": {}},
                {
                    "state": "complete",
                    "stage": stage,
                    "recorded_at": at,
                    "proof": {
                        "stage": stage,
                        "operation_id": identity,
                        "operation_sha256": sha,
                        "succeeded": True,
                        "cleanup_complete": True,
                        "error_code": "",
                        "verification": "injected",
                    },
                },
            ]
        if fault != "unfinished":
            rows.append({"state": "finished", "stage": "", "recorded_at": at, "proof": {}})
        return rows

    rows = history()

    class Files:
        count = 0

        def __init__(self, _):
            pass

        def history(self, _):
            self.count += 1
            return rows if fault != "history" or self.count == 1 else rows[:-1]

    class Registration:
        async def inspect(self, _):
            return {
                "available": True,
                "installed": True,
                "definition_present": True,
                "definition_matches": True,
                "loaded": True,
                "enabled": True,
                "state": "running",
                "pid": 43 if fault == "pid" else 42,
            }

        async def close(self, timeout):
            calls.append("close")
            return fault != "cleanup" or calls.count("close") > 1

    class Query(Registration):
        async def query(self, value):
            result = await self.inspect(value)
            if target == "macos-system":
                result.pop("enabled")  # Public system query does not attest the launchd override.
            return result

    class Readiness(Registration):
        async def probe(self, _):
            return {"ready": True, "boot_id": UUIDs().new() if fault == "boot" else boot, "pid": 42}

    monkeypatch.setattr(script, "ExchangeFiles", Files)
    monkeypatch.setattr(script, "ServiceRegistration", Registration)
    monkeypatch.setattr(script, "ServiceQuery", Query)
    monkeypatch.setattr(script, "ServiceReadiness", Readiness)
    result = asyncio.run(script.inspect(object(), {"valid": True, "eligible": True, "plan": deployment}, "ready", plan))
    assert result["expectation_passed"] is (fault in {"none", "cleanup"})
    assert result["exchange"]["current_reserved_boot_ready"] is result["expectation_passed"]
    assert result["format"] == "omni-macos-service-acceptance-2" and result["activation_exercised"] is False
    assert result["enabled_override_verified"] is (target == "macos-user")
    assert calls.count("close") == (6 if fault == "cleanup" else 3)


def test_rescue_matches_entire_predecessor_activation_reservation_without_rewriting_it(claimed, monkeypatch, tmp_path):
    original, _, _, failed = claimed
    old = original["release"]["target"]
    ids, at = UUIDs(), SystemClock().now()
    captured = json.loads(json.dumps(old["runtime"]).replace("/opt/versions/next/venv", "/opt/versions/rescue/venv"))
    supplied = {**old["request"], "home": old["request"]["home"] + "-rescue", "bundle_dir": "/tmp/rescue-bundle"}
    new = make_plan(supplied, captured, ids.new(), at, isolated_registration=True)
    authority = {
        "exchange_id": original["id"],
        "exchange_sha256": original["sha256"],
        "exchange_journal": original["journal"],
        "isolation_id": ids.new(),
        "isolation_sha256": "f" * 64,
        "release_id": original["release"]["id"],
        "release_sha256": original["release"]["sha256"],
        "source_deployment_sha256": old["sha256"],
    }
    rescue = make_release(
        old,
        new,
        {"backup": "/tmp/rescue-backup", "journal": "/tmp/rescue-release"},
        ids.new(),
        at,
        {
            "release_sha256": original["release"]["sha256"],
            "snapshot": "/tmp/original-snapshot",
            "manifest_sha256": "f" * 64,
            "isolation": authority,
            "restore_policy": "latest_failed",
            "expected_source_witness": [
                {"path": name, "sha256": "e" * 64 if i == 0 else None, "bytes": 0 if i == 0 else None}
                for i, name in enumerate(("omni.db", "omni.db-wal", "omni.db-journal"))
            ],
        },
    )
    restored = InstancePaths(tmp_path / "rescue")
    restored.initialize()
    failed.database.touch()
    files = ReleaseFiles(CurrentDeploymentFiles())
    # Original isolation verification is covered by real SQLite recovery tests;
    # inject that port here to exercise its Mac reservation under actual locks.
    monkeypatch.setattr(files, "isolation_exchange", lambda _: original)
    monkeypatch.setattr(files, "homes", lambda _: (failed, restored))
    monkeypatch.setattr(files, "_rescue_selection", lambda _: None)
    reservation = failed.control / "service-exchange.json"
    before = reservation.read_bytes()
    with files.exclusive(rescue):
        assert reservation.read_bytes() == before and json.loads(before)["activation"] is not None
    changed = json.loads(before)
    changed["activation"] = None
    reservation.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(BusinessError), files.exclusive(rescue):
        pytest.fail("An isolated predecessor's activation binding cannot be dropped")
