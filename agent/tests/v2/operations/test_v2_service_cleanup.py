"""Quarantined cleanup with real private files/locks and injected native/static proof; never changes OS registration."""

from __future__ import annotations

import asyncio
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from omni_test_support.v2.service_deployment import request, runtime

from omni.adapters.platform.releases.release_files import ReleaseFiles
from omni.adapters.platform.runtime.paths import InstanceLock, InstancePaths
from omni.adapters.platform.services import service_cleanup_files as storage
from omni.adapters.platform.services.deployment_files import CurrentDeploymentFiles
from omni.adapters.platform.services.exchange_files import ExchangeFiles
from omni.adapters.platform.services.service_cleanup_files import CleanupDeployment, CleanupFiles, CleanupPlanFiles
from omni.adapters.primitives import SystemClock, UUIDs
from omni.application.service_cleanup import CleanupReview, ServiceCleanup
from omni.application.service_operations import ServiceOperations
from omni.composition import cli
from omni.composition import service_cleanup as composition
from omni.composition.cli import parser
from omni.domain.contracts import BusinessError
from omni.domain.release_fence import fence
from omni.domain.releases import FENCE_FILES, digest, make_release
from omni.domain.service_cleanup import make_cleanup, registration_key, registry, validate_cleanup
from omni.domain.service_control import make_control
from omni.domain.service_deployment import make_plan
from omni.domain.service_exchange import make_exchange, stage_identity
from omni.domain.service_isolation import make_isolation, reference
from omni.domain.service_operations import make_operation


@pytest.fixture
def predecessor(tmp_path, monkeypatch):
    target = "windows-user" if sys.platform == "win32" else "linux-user"
    ids, at, publisher = UUIDs(), SystemClock().now(), CurrentDeploymentFiles()
    captured = runtime(target)
    captured["files"] += [{"path": name, "bytes": 100, "sha256": "e" * 64} for name in FENCE_FILES]
    old_request = {**request(target), "home": str(tmp_path / "old-home"), "bundle_dir": str(tmp_path / "old-bundle")}
    old = make_plan(old_request, captured, ids.new(), at)
    captured = json.loads(json.dumps(captured).replace("v2", "next"))
    # The runtime format/schema keep their actual names; only immutable paths change.
    captured["format"], captured["schema_head"] = "omni-service-runtime-2", "v2_0026"
    new = make_plan(
        {**old_request, "home": str(tmp_path / "failed-home"), "bundle_dir": str(tmp_path / "failed-bundle")},
        captured,
        ids.new(),
        at,
    )
    release = make_release(
        old, new, {"backup": str(tmp_path / "backup"), "journal": str(tmp_path / "release")}, ids.new(), at
    )
    plan = make_exchange(
        release,
        make_operation(old, "uninstall", ids.new(), at),
        make_operation(new, "install", ids.new(), at),
        make_control(new, "start", ids.new(), at, ids.new()),
        str(tmp_path / "exchange"),
        ids.new(),
        at,
    )
    homes = [InstancePaths(Path(item["request"]["home"])) for item in (old, new)]
    for home in homes:
        home.initialize()
        home.database.write_bytes(b"private business bytes: not SQLite, must never be opened by cleanup")
    files = ExchangeFiles(publisher)

    def append(state, stage="", proof=None):
        files.append(plan, {"state": state, "stage": stage, "recorded_at": at, "proof": proof or {}})

    with files.exclusive(plan):
        files.claim(plan)
        append("intent")
        for stage in ("remove_source", "select_target", "install_target"):
            identity, sha = stage_identity(plan, stage)
            append("dispatch", stage)
            succeeded = stage != "install_target"
            append(
                "complete" if succeeded else "blocked",
                stage,
                {
                    "stage": stage,
                    "operation_id": identity,
                    "operation_sha256": sha,
                    "succeeded": succeeded,
                    "cleanup_complete": True,
                    "error_code": "" if succeeded else "unknown",
                    "verification": "observed" if succeeded else "",
                },
            )
        history = files.history(plan)
        isolated = make_isolation(plan, len(history), digest(history), "private fixture reason", ids.new(), at)
        ref = reference(isolated)
        append("isolating", proof=ref)
        for side, home in zip(("source", "target"), homes, strict=True):
            publisher._publish_json(
                home.control / "release-fence.json", json.dumps(fence(release["id"], release["sha256"], side)).encode()
            )
        append("isolated", proof=ref)
    active = homes[1].control / "service-operations"
    active.mkdir()
    publisher._publish_json(
        active / "active.json", json.dumps({"operation_id": plan["install_target"]["id"], "effect": "unknown"}).encode()
    )
    originals = {str(p): p.read_bytes() for home in homes for p in home.home.rglob("*") if p.is_file()}
    originals.update({str(p): p.read_bytes() for p in Path(plan["journal"]).iterdir() if p.is_file()})
    history = files.history(plan)

    def review(side="target"):
        return make_cleanup(plan, len(history), digest(history), ref, side, ids.new(), ids.new(), at)

    verified = []

    def static_verify(self, selected):
        assert selected in (old, new)
        verified.append(selected["sha256"])

    monkeypatch.setattr(ReleaseFiles, "reviewed_installation", static_verify)
    return SimpleNamespace(
        exchange=plan,
        homes=homes,
        files=files,
        publisher=publisher,
        review=review,
        originals=originals,
        root=tmp_path,
        verified=verified,
    )


class Native:
    def __init__(self, plan, files):
        self.plan, self.files = plan, files
        self.present, self.matches, self.available, self.state = True, True, True, "stopped"
        self.cleanup, self.effect = True, "applied"
        self.remove, self.failure = True, False
        self.changes, self.inspections = 0, 0
        self.entered = self.release = None

    async def inspect(self, deployment):
        assert deployment == self.plan["operation"]["deployment"]
        self.inspections += 1
        return {
            "available": self.available,
            "installed": self.present,
            "definition_present": self.present,
            "definition_matches": self.matches,
            "state": self.state,
            "enabled": self.present,
            "pid": None,
        }

    async def change(self, deployment, action):
        assert deployment == self.plan["operation"]["deployment"] and action == "uninstall"
        assert self.files.dispatched(self.plan["operation"])
        for home in self.files.homes():
            assert json.loads(self.files.reservation_path(home).read_bytes()) == self.files.reservation()
            lock = InstanceLock(home)
            with pytest.raises(BusinessError) as caught:
                lock.acquire(
                    release=(self.plan["exchange"]["release"]["id"], self.plan["exchange"]["release"]["sha256"])
                )
            assert caught.value.code == "instance_busy"
        self.changes += 1
        if self.entered:
            self.entered.set()
            await self.release.wait()
        if self.remove:
            self.present = False
        if self.failure:
            raise OSError("credential=must not leave the native boundary")
        return {"effect": self.effect}

    async def close(self, timeout):
        return self.cleanup


def build(p, plan=None, native=None):
    plan = p.review() if plan is None else plan
    files = CleanupFiles(plan, p.publisher)
    selected = Native(plan, files) if native is None else native
    selected.files = files
    operations = ServiceOperations(CleanupDeployment(files), files, selected, SystemClock(), UUIDs())
    return ServiceCleanup(plan, files, operations), files, selected


def preserved(p):
    assert all(Path(name).read_bytes() == data for name, data in p.originals.items())


def test_cleanup_removes_only_exact_definition_preserves_originals_and_historical_apply_does_not_replay(predecessor):
    p = predecessor

    async def scenario():
        service, files, native = build(p)
        plan = service.plan
        result = await service.apply(plan["sha256"])
        assert result["state"] == "succeeded" and result["receipt"]["cleanup_complete"] is True
        assert result["original_effect"] == "unknown" and result["late_effects_possible"] is True
        assert native.changes == 1 and not any(files.reservation_path(home).exists() for home in p.homes)
        assert len(p.verified) >= 2 and set(p.verified) == {plan["operation"]["deployment"]["sha256"]}
        assert await service.close(1)
        observed, _, _ = build(p, plan, native)
        assert (await observed.apply(plan["sha256"]))["live_observation"] is False and native.changes == 1
        assert await observed.close(1)
        native.present = (
            True  # A late old operation can recreate the old key; historical success is not current absence.
        )
        checked, _, _ = build(p, plan, native)
        assert checked.inspect()["state"] == "succeeded" and (await checked.reconcile())["state"] == "unknown"
        assert native.changes == 1 and await checked.close(1)
        preserved(p)

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["absent", "foreign", "running", "unavailable"])
def test_cleanup_absence_or_unsafe_definition_never_dispatches(predecessor, fault):
    p = predecessor

    async def scenario():
        service, _, native = build(p)
        if fault == "absent":
            native.present = False
        if fault == "foreign":
            native.matches = False
        if fault == "running":
            native.state = "running"
        if fault == "unavailable":
            native.available = False
        result = await service.apply(service.plan["sha256"])
        assert result["state"] == ("succeeded" if fault == "absent" else "denied")
        assert native.changes == 0 and await service.close(1)
        preserved(p)

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["unknown", "interrupted", "unknown_without_effect"])
def test_unknown_cleanup_blocks_same_native_key_even_through_other_side_and_reconciles_without_replay(
    predecessor, fault
):
    p = predecessor

    async def scenario():
        service, _, native = build(p)
        plan = service.plan
        native.effect, native.failure = "unknown", fault == "interrupted"
        native.remove = fault != "unknown_without_effect"
        assert (await service.apply(plan["sha256"]))["state"] == "unknown"
        assert native.changes == 1 and await service.close(1)
        other, _, another = build(p, p.review("source"))
        assert registration_key(other.plan["operation"]) == registration_key(plan["operation"])
        with pytest.raises(BusinessError) as caught:
            await other.apply(other.plan["sha256"])
        assert caught.value.code == "service_cleanup_unresolved" and another.changes == 0
        assert await other.close(1)
        cancel, _, _ = build(p, plan, native)
        with pytest.raises(BusinessError):
            await cancel.cancel()
        assert await cancel.close(1)
        resumed, files, _ = build(p, plan, native)
        result = await resumed.reconcile()
        if fault == "unknown_without_effect":
            assert result["state"] == "unknown" and native.changes == 1
            assert await resumed.close(1)
            native.present = False  # Separately observed external completion, never an automatic resend.
            resumed, files, _ = build(p, plan, native)
            result = await resumed.reconcile()
        assert result["state"] == "succeeded" and result["live_observation"] is True and native.changes == 1
        assert not any(files.reservation_path(home).exists() for home in p.homes)
        assert await resumed.close(1)
        preserved(p)

    asyncio.run(scenario())


def test_cancelled_waiter_keeps_native_work_locks_and_reservations_until_actual_cleanup(predecessor):
    p = predecessor

    async def scenario():
        service, files, native = build(p)
        native.entered, native.release = asyncio.Event(), asyncio.Event()
        native.cleanup = False
        waiter = asyncio.create_task(service.apply(service.plan["sha256"]))
        await native.entered.wait()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not await service.close(0) and files._owned
        native.release.set()
        await service.operations._pending
        assert not await service.close(0) and files._owned
        native.cleanup = True
        assert await service.close(1) and not files._owned
        assert native.changes == 1 and not any(files.reservation_path(home).exists() for home in p.homes)
        preserved(p)

    asyncio.run(scenario())


def test_partial_two_home_claim_can_be_cancelled_but_not_replayed(predecessor, monkeypatch):
    p = predecessor
    service, files, native = build(p)
    publish = files._publisher._publish_json
    second = files.reservation_path(files.homes()[1])

    def interrupted(path, data):
        if path == second:
            raise OSError("injected second-home publication failure")
        return publish(path, data)

    monkeypatch.setattr(files._publisher, "_publish_json", interrupted)
    with files.exclusive(service.plan["operation"]), pytest.raises(OSError):
        files.activate(service.plan["operation"])
    monkeypatch.setattr(files._publisher, "_publish_json", publish)

    async def scenario():
        with pytest.raises(BusinessError):
            await service.apply(service.plan["sha256"])
        assert await service.close(1)
        cancelled, _, _ = build(p, service.plan, native)
        assert (await cancelled.cancel())["state"] == "cancelled"
        assert native.changes == 0 and await cancelled.close(1)
        preserved(p)

    asyncio.run(scenario())


@pytest.mark.parametrize("damage", ["fence", "reservation", "history", "review", "approval"])
def test_scope_changes_are_rejected_before_native_dispatch(predecessor, damage):
    p = predecessor
    service, files, native = build(p)
    if damage == "fence":
        (p.homes[0].control / "release-fence.json").unlink()
    elif damage == "reservation":
        (p.homes[1].control / "service-exchange.json").write_bytes(b"{}")
    elif damage == "history":
        (Path(p.exchange["journal"]) / "0000.json").write_bytes(b"{}")
    elif damage == "review":
        with files.exclusive(service.plan["operation"]):
            files._prepare()
        (Path(service.plan["journal"]) / "review.json").write_bytes(b"{}")

    async def scenario():
        with pytest.raises((BusinessError, OSError)):
            await service.apply("f" * 64 if damage == "approval" else service.plan["sha256"])
        assert native.changes == 0 and await service.close(1)

    asyncio.run(scenario())


def test_review_is_private_bounded_and_not_a_service_operation(predecessor):
    p = predecessor
    output = p.root / "cleanup.json"
    result = CleanupReview(p.files, CleanupPlanFiles(p.publisher), SystemClock(), UUIDs()).plan(
        p.exchange, "target", str(output)
    )
    plan = validate_cleanup(p.publisher.read(output))
    assert result["executed"] is False and plan["preserves_original_records"] is True
    assert not Path(plan["journal"]).exists()
    for path in (p.homes[0].home / "review.json", Path(registry(p.exchange)) / "review.json"):
        with pytest.raises(BusinessError):
            CleanupPlanFiles(p.publisher).publish(plan, str(path))
    changed = copy.deepcopy(plan)
    changed["deletes_instance_data"] = 1
    with pytest.raises(BusinessError):
        validate_cleanup(changed)
    args = parser().parse_args(["exchange", "apply-cleanup", str(output), "--approve", plan["sha256"]])
    assert args.exchange_command == "apply-cleanup"
    with pytest.raises(SystemExit):
        parser().parse_args(["exchange", "apply-cleanup", str(output)])
    preserved(p)


def test_composition_review_and_execution_use_only_bound_cleanup_ports(predecessor, monkeypatch):
    p = predecessor
    plan_file, original_file = p.root / "review.json", p.root / "original.json"
    p.publisher._publish_json(original_file, json.dumps(p.exchange).encode())

    async def scenario():
        result = await composition.run_cleanup("cleanup-plan", original_file, side="target", output=plan_file)
        plan = validate_cleanup(p.publisher.read(plan_file))
        assert result["executed"] is False and not Path(plan["journal"]).exists()
        captured = {}

        def files(value, deployments):
            captured["files"] = CleanupFiles(value, deployments)
            return captured["files"]

        def manager():
            captured["native"] = Native(plan, captured["files"])
            return captured["native"]

        monkeypatch.setattr(composition, "CleanupFiles", files)
        monkeypatch.setattr(composition, "ServiceRegistration", manager)
        result = await composition.run_cleanup("apply-cleanup", plan_file, approval=plan["sha256"])
        assert result["state"] == "succeeded" and result["receipt"]["cleanup_complete"] is True
        assert captured["native"].changes == 1 and not captured["files"]._owned
        preserved(p)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "state,closed,exit_code", [("succeeded", True, 0), ("unknown", True, 1), ("succeeded", False, 1)]
)
def test_cli_cleanup_dispatch_and_exit_code_require_absence_and_actual_cleanup(
    monkeypatch, capsys, state, closed, exit_code
):
    async def command(name, path, **options):
        assert name == "apply-cleanup" and path == Path("review.json") and options["approval"] == "a" * 64
        return {"state": state, "receipt": {"cleanup_complete": closed}}

    monkeypatch.setattr(composition, "run_cleanup", command)
    monkeypatch.setattr(sys, "argv", ["omni", "exchange", "apply-cleanup", "review.json", "--approve", "a" * 64])
    assert cli.main() == exit_code
    assert json.loads(capsys.readouterr().out)["state"] == state


def test_live_predecessor_lock_prevents_cleanup_without_touching_originals(predecessor):
    p = predecessor
    lock = InstanceLock(p.homes[1])
    lock.acquire(release=(p.exchange["release"]["id"], p.exchange["release"]["sha256"]))

    async def scenario():
        service, _, native = build(p)
        try:
            with pytest.raises(BusinessError) as caught:
                await service.apply(service.plan["sha256"])
            assert caught.value.code == "instance_busy" and native.changes == 0
        finally:
            assert await service.close(1)

    try:
        asyncio.run(scenario())
        preserved(p)
    finally:
        lock.close()


@pytest.mark.parametrize("side", ["source", "target"])
def test_mac_cleanup_declares_possible_unload_and_keeps_exact_shared_native_key(side):
    from omni_test_support.v2.macos_activation import exchange_plan

    plan, ids, at = exchange_plan(), UUIDs(), SystemClock().now()
    ref = reference(make_isolation(plan, 1, "e" * 64, "review", ids.new(), at))
    cleanup = make_cleanup(plan, 3, "f" * 64, ref, side, ids.new(), ids.new(), at)
    assert validate_cleanup(cleanup)["stops_service"] is True and cleanup["starts_service"] is False
    assert cleanup["operation"]["deployment"] == plan["release"][side]
    other = make_cleanup(plan, 3, "f" * 64, ref, "target" if side == "source" else "source", ids.new(), ids.new(), at)
    assert registration_key(cleanup["operation"]) == registration_key(other["operation"])


@pytest.mark.parametrize("folder", ["reservation", "registry"])
def test_unverifiable_managed_directory_permissions_deny_before_native_work(predecessor, monkeypatch, folder):
    p = predecessor
    service, _, native = build(p)
    denied = p.homes[0].control / "service-cleanups" if folder == "reservation" else Path(registry(p.exchange))
    denied.mkdir()
    verify = storage.assert_private

    def check(path):
        if path == denied:
            raise OSError("injected permission change")
        return verify(path)

    monkeypatch.setattr(storage, "assert_private", check)

    async def scenario():
        with pytest.raises(OSError):
            await service.apply(service.plan["sha256"])
        assert native.changes == 0 and await service.close(1)
        preserved(p)

    asyncio.run(scenario())
