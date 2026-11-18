"""Reviewed start/stop, boot reservations, shared journals and unknown outcomes; no real manager or process control."""

from __future__ import annotations

from omni_test_support.v2.service_control import (
    Boot as Boot,
)
from omni_test_support.v2.service_control import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.service_control import (
    Clock as Clock,
)
from omni_test_support.v2.service_control import (
    InstanceLock as InstanceLock,
)
from omni_test_support.v2.service_control import (
    InstancePaths as InstancePaths,
)
from omni_test_support.v2.service_control import (
    Native as Native,
)
from omni_test_support.v2.service_control import (
    Path as Path,
)
from omni_test_support.v2.service_control import (
    PrivateFileLock as PrivateFileLock,
)
from omni_test_support.v2.service_control import (
    RuntimeIdentityFiles as RuntimeIdentityFiles,
)
from omni_test_support.v2.service_control import (
    ServiceControl as ServiceControl,
)
from omni_test_support.v2.service_control import (
    ServiceControlFiles as ServiceControlFiles,
)
from omni_test_support.v2.service_control import (
    ServiceOperationFiles as ServiceOperationFiles,
)
from omni_test_support.v2.service_control import (
    SimpleNamespace as SimpleNamespace,
)
from omni_test_support.v2.service_control import (
    SystemClock as SystemClock,
)
from omni_test_support.v2.service_control import (
    UUIDs as UUIDs,
)
from omni_test_support.v2.service_control import (
    asyncio as asyncio,
)
from omni_test_support.v2.service_control import (
    boot_identity as boot_identity,
)
from omni_test_support.v2.service_control import (
    consume_launch as consume_launch,
)
from omni_test_support.v2.service_control import (
    controls as controls,
)
from omni_test_support.v2.service_control import (
    installation as installation,
)
from omni_test_support.v2.service_control import (
    json as json,
)
from omni_test_support.v2.service_control import (
    make_control as make_control,
)
from omni_test_support.v2.service_control import (
    parser as parser,
)
from omni_test_support.v2.service_control import (
    prepared as prepared,
)
from omni_test_support.v2.service_control import (
    pytest as pytest,
)
from omni_test_support.v2.service_control import (
    registration_operation as registration_operation,
)
from omni_test_support.v2.service_control import (
    startup as startup,
)
from omni_test_support.v2.service_control import (
    validate_control as validate_control,
)


def test_start_proves_reserved_boot_stop_waits_for_actual_instance_release_and_never_replays(controls):
    r = controls

    async def scenario():
        controller = r.service()
        result = await controller.plan(str(r.root / "bundle"), "start", str(r.root / "start.json"))
        operation = r.files.read(result["path"])
        assert validate_control(operation)["boot_id"] == result["boot_id"]
        assert (await controller.apply(operation, result["sha256"]))["state"] == "succeeded"
        assert r.native.starts == 1 and r.native.identity["boot_id"] == operation["boot_id"]
        assert not (r.native.paths.control / "launch-ticket.json").exists()
        replay = r.service()
        assert (await replay.apply(operation, result["sha256"]))["state"] == "succeeded" and r.native.starts == 1
        stop = r.service()
        result = await stop.plan(str(r.root / "bundle"), "stop", str(r.root / "stop.json"))
        stop_op = r.files.read(result["path"])
        assert stop_op["boot_id"] == operation["boot_id"]
        assert (await stop.apply(stop_op, result["sha256"]))["state"] == "succeeded"
        assert r.boot.drains == 1 and r.native.lock is None
        metadata = json.loads((r.native.paths.control / "runtime.json").read_text())
        assert metadata["state"] == "stopped" and metadata["boot_id"] == operation["boot_id"]

    asyncio.run(scenario())


def test_wrong_boot_is_unknown_blocks_other_maintenance_and_reconcile_does_not_start_again(controls):
    r = controls

    async def scenario():
        op = make_control(r.deployment, "start", UUIDs().new(), SystemClock().now(), UUIDs().new())
        r.boot.wrong_boot = True
        assert (await r.service().apply(op, op["sha256"]))["state"] == "unknown"
        assert r.native.starts == 1
        with (
            pytest.raises(BusinessError, match="earlier service maintenance"),
            ServiceOperationFiles().exclusive(registration_operation(r.deployment)),
        ):
            pytest.fail("unresolved start cannot permit registration changes")
        r.boot.wrong_boot = False
        assert (await r.service().apply(op, "", reconcile=True))["state"] == "succeeded"
        assert r.native.starts == 1

    asyncio.run(scenario())


def test_cancelled_caller_keeps_native_start_and_shared_mutex_owned(controls):
    r = controls

    async def scenario():
        controller = r.service()
        r.native.entered, r.native.release = asyncio.Event(), asyncio.Event()
        op = make_control(r.deployment, "start", UUIDs().new(), SystemClock().now(), UUIDs().new())
        task = asyncio.create_task(controller.apply(op, op["sha256"]))
        await r.native.entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not await controller.close(0)
        with pytest.raises(BusinessError, match="already running"):
            PrivateFileLock(r.native.paths.control / "service-maintenance.lock").acquire()
        r.native.release.set()
        assert (await controller._pending)["state"] == "succeeded"
        r.native.cleanup = False
        assert not await controller.close(0)
        r.native.cleanup = True
        assert await controller.close(1)

    asyncio.run(scenario())


def test_once_consumed_ticket_never_reuses_boot_identity_after_failed_start(controls):
    r = controls
    operation = make_control(r.deployment, "start", UUIDs().new(), SystemClock().now(), UUIDs().new())
    r.store.reserve(operation)
    assert consume_launch(r.native.paths.control, startup(r.deployment)) == operation["boot_id"]
    with pytest.raises(BusinessError, match="already claimed"):
        consume_launch(r.native.paths.control, startup(r.deployment))
    with pytest.raises(BusinessError, match="another reviewed deployment"):
        consume_launch(r.native.paths.control, None)


def test_cleanup_residual_preserves_audit_marker_until_actual_ports_close(controls):
    r = controls

    async def scenario():
        controller = r.service()
        r.native.cleanup = False
        op = make_control(r.deployment, "start", UUIDs().new(), SystemClock().now(), UUIDs().new())
        result = await controller.apply(op, op["sha256"])
        marker = r.native.paths.control / "service-controls/active.json"
        assert result["state"] == "succeeded" and result["cleanup_complete"] is False and marker.exists()
        assert r.store.latest(op)["cleanup_complete"] is False
        assert not await controller.close(0) and marker.exists()
        r.native.cleanup = True
        assert await controller.close(1) and not marker.exists()
        assert r.store.latest(op)["cleanup_complete"] is True

    asyncio.run(scenario())


def test_unknown_or_dispatched_intent_cannot_be_cancelled_but_undispatched_intent_can(controls):
    r = controls

    async def scenario():
        op = make_control(r.deployment, "start", UUIDs().new(), SystemClock().now(), UUIDs().new())
        with r.store.exclusive(op):
            r.store.activate(op)
            r.store.append(
                op, {"operation_id": op["id"], "operation_sha256": op["sha256"], "state": "intent", "terminal": False}
            )
        assert (await r.service().apply(op, "", cancel=True))["state"] == "cancelled"
        second = make_control(r.deployment, "start", UUIDs().new(), SystemClock().now(), UUIDs().new())
        with r.store.exclusive(second):
            r.store.activate(second)
            r.store.append(
                second,
                {
                    "operation_id": second["id"],
                    "operation_sha256": second["sha256"],
                    "state": "dispatch",
                    "terminal": False,
                },
            )
        assert (await r.service().apply(second, "", cancel=True))["state"] == "unknown"
        assert r.native.starts == 0

    asyncio.run(scenario())


def test_plan_flags_and_cli_are_explicit_and_fixed(controls):
    value = make_control(controls.deployment, "start", UUIDs().new(), SystemClock().now(), UUIDs().new())
    for field, replacement in (("graceful_only", 1), ("wait_seconds", True), ("boot_id", UUIDs().new())):
        with pytest.raises(BusinessError):
            validate_control({**value, field: replacement})
    assert parser().parse_args(["service", "control-plan", "bundle", "stop", "new.json"]).action == "stop"
    with pytest.raises(SystemExit):
        parser().parse_args(["service", "apply-control", "review.json"])
