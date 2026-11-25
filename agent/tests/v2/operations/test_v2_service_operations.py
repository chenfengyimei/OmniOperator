"""Offline registration, durable crash fences and cancellation; never changes a native service."""

from __future__ import annotations

from omni_test_support.v2.service_operations import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.service_operations import (
    InstanceLock as InstanceLock,
)
from omni_test_support.v2.service_operations import (
    InstancePaths as InstancePaths,
)
from omni_test_support.v2.service_operations import (
    Native as Native,
)
from omni_test_support.v2.service_operations import (
    Path as Path,
)
from omni_test_support.v2.service_operations import (
    ServiceOperationFiles as ServiceOperationFiles,
)
from omni_test_support.v2.service_operations import (
    ServiceOperations as ServiceOperations,
)
from omni_test_support.v2.service_operations import (
    SystemClock as SystemClock,
)
from omni_test_support.v2.service_operations import (
    UUIDs as UUIDs,
)
from omni_test_support.v2.service_operations import (
    assert_private as assert_private,
)
from omni_test_support.v2.service_operations import (
    asyncio as asyncio,
)
from omni_test_support.v2.service_operations import (
    copy as copy,
)
from omni_test_support.v2.service_operations import (
    installation as installation,
)
from omni_test_support.v2.service_operations import (
    json as json,
)
from omni_test_support.v2.service_operations import (
    make_operation as make_operation,
)
from omni_test_support.v2.service_operations import (
    make_plan as make_plan,
)
from omni_test_support.v2.service_operations import (
    open_instance as open_instance,
)
from omni_test_support.v2.service_operations import (
    operation as operation,
)
from omni_test_support.v2.service_operations import (
    parser as parser,
)
from omni_test_support.v2.service_operations import (
    plan as plan,
)
from omni_test_support.v2.service_operations import (
    plan_for as plan_for,
)
from omni_test_support.v2.service_operations import (
    prepared as prepared,
)
from omni_test_support.v2.service_operations import (
    pytest as pytest,
)
from omni_test_support.v2.service_operations import (
    registration as registration,
)
from omni_test_support.v2.service_operations import (
    registration_goal as registration_goal,
)
from omni_test_support.v2.service_operations import (
    registration_precondition as registration_precondition,
)
from omni_test_support.v2.service_operations import (
    uuid as uuid,
)
from omni_test_support.v2.service_operations import (
    validate_operation as validate_operation,
)


def test_instance_initialization_protects_all_writable_directories_and_rejects_linked_locks(tmp_path):
    import os

    paths = InstancePaths(tmp_path / "isolated")
    paths.initialize()
    for directory in (paths.home, paths.database.parent, paths.logs, paths.objects, paths.control):
        assert_private(directory)
    lock = InstanceLock(paths)
    lock.acquire()
    assert_private(lock.path)
    lock.close()
    foreign = tmp_path / "foreign"
    foreign.write_bytes(b"preserve")
    lock.path.unlink()
    os.link(foreign, lock.path)
    with pytest.raises(BusinessError, match="one regular"):
        lock.acquire()
    assert foreign.read_bytes() == b"preserve"


@pytest.mark.parametrize("target", ["windows-user", "macos-user", "macos-system", "linux-user", "linux-system"])
def test_reviewed_operations_bind_steps_and_declared_platform_effects(target):
    value = operation(plan(target))
    assert validate_operation(value) == value
    assert value["starts_service"] is target.startswith("macos-")
    assert not value["stops_service"] and not value["deletes_instance_data"]
    removed = operation(plan(target), "uninstall")
    assert removed["stops_service"] is target.startswith("macos-") and not removed["starts_service"]
    for key, changed in (
        ("action", "uninstall"),
        ("starts_service", not value["starts_service"]),
        ("steps", ["overwrite"]),
        ("sha256", "0" * 64),
        ("deletes_instance_data", 0),
    ):
        with pytest.raises(BusinessError):
            validate_operation({**value, key: changed})
    absent, installed = registration(), registration(True)
    assert registration_precondition("install", absent) and registration_goal("uninstall", absent)
    assert registration_precondition("uninstall", installed) and registration_goal("install", installed)
    for report in (
        {**absent, "installed": None},
        {**absent, "definition_present": True},
        {**installed, "state": "running"},
    ):
        assert not registration_precondition("install", report)


def test_cli_requires_the_exact_approval_and_does_not_change_default_entry():
    args = parser().parse_args(["service", "operation-plan", "bundle", "install", "new.json"])
    assert args.action == "install"
    with pytest.raises(SystemExit):
        parser().parse_args(["service", "apply-operation", "new.json"])
    assert parser().parse_args(["service", "apply-operation", "new.json", "--approve", "a" * 64]).approve == "a" * 64


def test_real_journal_atomic_registration_duplicate_apply_and_explicit_removal(installation, tmp_path):
    async def scenario():
        files = installation[0]
        deployment = await prepared(files, tmp_path)
        store = ServiceOperationFiles()
        native = Native()
        service = ServiceOperations(files, store, native, SystemClock(), UUIDs())
        output = tmp_path / "install-operation.json"
        reviewed = service.plan(str(tmp_path / "bundle"), "install", str(output))
        assert reviewed["executed"] is False and native.calls == []
        assert reviewed["starts_service"] is deployment["request"]["target"].startswith("macos-")
        assert reviewed["stops_service"] is False
        value = files.read(output)
        with pytest.raises(BusinessError, match="exact reviewed"):
            await service.apply(value, "0" * 64)
        assert store.latest(value) is None
        receipt = await service.apply(value, value["sha256"])
        assert receipt["state"] == "succeeded" and receipt["effect"] == "applied" and receipt["ready"] is False
        assert native.calls == ["inspect", "install", "inspect"]
        assert await service.close()
        again = Native()
        retry = ServiceOperations(files, store, again, SystemClock(), UUIDs())
        assert await retry.apply(value, value["sha256"]) == receipt
        assert again.calls == [] and await retry.close()
        remove = operation(deployment, "uninstall")
        native = Native(registration(True))
        service = ServiceOperations(files, store, native, SystemClock(), UUIDs())
        result = await service.apply(remove, remove["sha256"])
        assert result["state"] == "succeeded" and native.calls == ["inspect", "uninstall", "inspect"]
        assert await service.close()
        assert (Path(deployment["request"]["home"]) / "data/omni.db").is_file()
        assert not (Path(deployment["request"]["home"]) / "run/service-operations/active.json").exists()

    asyncio.run(scenario())


def test_unknown_write_blocks_new_operations_and_reconciles_without_replay(installation, tmp_path):
    async def scenario():
        files = installation[0]
        deployment = await prepared(files, tmp_path)
        store = ServiceOperationFiles()
        value = operation(deployment)
        native = Native()
        native.failure = OSError("unreturned credential=must_not_escape")
        service = ServiceOperations(files, store, native, SystemClock(), UUIDs())
        result = await service.apply(value, value["sha256"])
        assert result["state"] == "unknown" and result["effect"] == "unknown" and result["terminal"] is False
        assert "must_not_escape" not in json.dumps(result)
        assert await service.close()
        new = operation(deployment)
        fresh = ServiceOperations(files, store, Native(), SystemClock(), UUIDs())
        with pytest.raises(BusinessError) as blocked:
            await fresh.apply(new, new["sha256"])
        assert blocked.value.code == "service_operation_unresolved"
        assert await fresh.close()
        retry = ServiceOperations(files, store, Native(), SystemClock(), UUIDs())
        with pytest.raises(BusinessError, match="never replay"):
            await retry.apply(value, value["sha256"])
        assert await retry.close()
        cancellation = ServiceOperations(files, store, Native(), SystemClock(), UUIDs())
        with pytest.raises(BusinessError, match="cannot be cancelled"):
            await cancellation.cancel(value)
        assert await cancellation.close()
        observed = Native(registration(True))
        reconcile = ServiceOperations(files, store, observed, SystemClock(), UUIDs())
        result = await reconcile.reconcile(value)
        assert result["state"] == "succeeded" and observed.calls == ["inspect"]
        assert result["verification"] == "observed_registration_postcondition"
        assert await reconcile.close()

    asyncio.run(scenario())


def test_only_undispatched_intent_can_be_cancelled_after_readonly_unknown_observation(installation, tmp_path):
    async def scenario():
        files = installation[0]
        deployment = await prepared(files, tmp_path)
        value = operation(deployment)
        store = ServiceOperationFiles()
        with store.exclusive(value):
            store.activate(value)
            store.append(
                value,
                {
                    "operation_id": value["id"],
                    "operation_sha256": value["sha256"],
                    "state": "intent",
                    "effect": "none",
                    "terminal": False,
                },
            )
        probe = Native()
        service = ServiceOperations(files, store, probe, SystemClock(), UUIDs())
        assert (await service.reconcile(value))["state"] == "unknown"
        assert probe.calls == ["inspect"] and await service.close()
        native = Native()
        cancel = ServiceOperations(files, store, native, SystemClock(), UUIDs())
        result = await cancel.cancel(value)
        assert (
            result["state"] == "cancelled"
            and result["effect"] == "none"
            and result["verification"] == "no_persisted_dispatch"
        )
        assert native.calls == [] and await cancel.close()
        assert not (tmp_path / "instance/run/service-operations/active.json").exists()
        with store.exclusive(operation(deployment)):
            pass

    asyncio.run(scenario())


def test_cancelled_caller_retains_actual_write_journal_and_instance_lock(installation, tmp_path):
    async def scenario():
        files = installation[0]
        deployment = await prepared(files, tmp_path)
        native = Native()
        native.release.clear()
        store = ServiceOperationFiles()
        value = operation(deployment)
        service = ServiceOperations(files, store, native, SystemClock(), UUIDs())
        caller = asyncio.create_task(service.apply(value, value["sha256"]))
        await asyncio.wait_for(native.entered.wait(), 5)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert not await service.close(0)
        with pytest.raises(BusinessError, match="already running"):
            InstanceLock(InstancePaths(tmp_path / "instance")).acquire()
        assert store.latest(value)["state"] == "dispatch"
        native.release.set()
        assert await service.close(5)
        assert store.latest(value)["state"] == "succeeded" and native.calls.count("install") == 1
        lock = InstanceLock(InstancePaths(tmp_path / "instance"))
        lock.acquire()
        lock.close()

    asyncio.run(scenario())


def test_native_cleanup_residual_keeps_instance_owned(installation, tmp_path):
    async def scenario():
        files = installation[0]
        deployment = await prepared(files, tmp_path)
        native = Native()
        native.cleanup = False
        value = operation(deployment)
        service = ServiceOperations(files, ServiceOperationFiles(), native, SystemClock(), UUIDs())
        receipt = await service.apply(value, value["sha256"])
        assert receipt["cleanup_complete"] is False
        active = InstancePaths(tmp_path / "instance").control / "service-operations/active.json"
        assert active.exists()
        assert not await service.close(0)
        with pytest.raises(BusinessError):
            InstanceLock(InstancePaths(tmp_path / "instance")).acquire()
        native.cleanup = True
        assert await service.close(5)
        assert receipt["cleanup_complete"] is True and not active.exists()

    asyncio.run(scenario())


def test_changed_bundle_live_instance_and_foreign_definition_reject_before_native_write(installation, tmp_path):
    async def scenario():
        files = installation[0]
        deployment = await prepared(files, tmp_path)
        value = operation(deployment)
        native = Native({**registration(True), "definition_matches": False})
        store = ServiceOperationFiles()
        service = ServiceOperations(files, store, native, SystemClock(), UUIDs())
        result = await service.apply(value, value["sha256"])
        assert result["state"] == "denied" and result["effect"] == "none" and native.calls == ["inspect"]
        assert await service.close()
        live = await open_instance(tmp_path / "instance", capabilities=())
        try:
            new = operation(deployment)
            native = Native()
            service = ServiceOperations(files, store, native, SystemClock(), UUIDs())
            with pytest.raises(BusinessError, match="already running"):
                await service.apply(new, new["sha256"])
            assert native.calls == [] and await service.close()
        finally:
            assert (await live.close())["complete"]
        original_verify = files.verify

        def replaced(bundle):
            verified = original_verify(bundle)
            changed = copy.deepcopy(verified["plan"])
            changed["id"] = str(uuid.uuid4())
            verified["plan"] = make_plan(changed["request"], changed["runtime"], changed["id"], changed["created_at"])
            return verified

        files.verify = replaced
        service = ServiceOperations(files, store, Native(), SystemClock(), UUIDs())
        changed_operation = operation(deployment)
        with pytest.raises(BusinessError, match="differs"):
            await service.apply(changed_operation, changed_operation["sha256"])
        assert await service.close()

    asyncio.run(scenario())


def test_interrupted_initial_journal_is_recoverable_but_never_replayed(installation, tmp_path, monkeypatch):
    async def scenario():
        files = installation[0]
        deployment = await prepared(files, tmp_path)
        value = operation(deployment)
        store = ServiceOperationFiles()
        original = store._publisher._publish_json

        def crash(target, data):
            if target.name == "operation.json":
                raise OSError("simulated process interruption")
            return original(target, data)

        monkeypatch.setattr(store._publisher, "_publish_json", crash)
        with store.exclusive(value), pytest.raises(OSError):
            store.activate(value)
        assert store.latest(value)["state"] == "intent"
        monkeypatch.setattr(store._publisher, "_publish_json", original)
        native = Native(registration(True))
        service = ServiceOperations(files, store, native, SystemClock(), UUIDs())
        result = await service.reconcile(value)
        assert result["state"] == "succeeded" and native.calls == ["inspect"]
        assert await service.close()

    asyncio.run(scenario())


def test_journal_fingerprint_change_and_foreign_output_paths_are_rejected(installation, tmp_path):
    async def scenario():
        files = installation[0]
        deployment = await prepared(files, tmp_path)
        value = operation(deployment)
        store = ServiceOperationFiles()
        for output in (tmp_path / "instance/unsafe.json", tmp_path / "bundle/extra.json"):
            with pytest.raises(BusinessError):
                store.publish(value, str(output))
        with store.exclusive(value):
            store.activate(value)
            store.append(
                value,
                {
                    "operation_id": value["id"],
                    "operation_sha256": value["sha256"],
                    "state": "dispatch",
                    "terminal": False,
                },
            )
        path = tmp_path / "instance/run/service-operations" / value["id"] / "0000.json"
        document = json.loads(path.read_bytes())
        document["record"]["terminal"] = True
        path.write_text(json.dumps(document), encoding="utf-8")
        with pytest.raises(BusinessError, match="fingerprint"):
            store.latest(value)

    asyncio.run(scenario())
