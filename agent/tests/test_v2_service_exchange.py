"""Prepared release to reserved-boot readiness using isolated databases and injected managers, never real OS services."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_v2_deployment_files as deployment_tests
from test_v2_releases import release as release
from test_v2_service_control import Boot, Native

from omni.adapters.persistence.releases import ReleaseData
from omni.adapters.platform.services import deployment_files
from omni.adapters.platform.services.exchange_deployments import ExchangeSourceDeployment
from omni.adapters.platform.services.exchange_files import ExchangeFiles
from omni.adapters.platform.runtime.paths import InstanceLock
from omni.adapters.platform.runtime.permissions import private_path
from omni.adapters.platform.releases.release_files import ReleaseFiles
from omni.adapters.platform.services.service_control_files import ServiceControlFiles
from omni.adapters.platform.services.service_operation_files import ServiceOperationFiles
from omni.adapters.primitives import SystemClock, UUIDs
from omni.application.exchange_steps import ExchangeSteps
from omni.application.releases import Releases
from omni.application.service_control import ServiceControl
from omni.application.service_exchange import ServiceExchange
from omni.application.service_operations import ServiceOperations
from omni.composition.cli import parser
from omni.domain.contracts import BusinessError
from omni.domain.service_control import make_control
from omni.domain.service_exchange import (
    STAGES,
    checked_proof,
    make_exchange,
    marker,
    progress,
    stage_identity,
    validate_exchange,
)
from omni.domain.service_operations import make_operation

base_installation = deployment_tests.installation


@pytest.fixture
def installation(base_installation, monkeypatch):
    if sys.platform == "darwin":
        # Exercise the implemented Linux protocol on POSIX without claiming macOS exchange support.
        # Replace only this adapter's sys reference; never change the interpreter's global platform.
        monkeypatch.setattr(deployment_files, "sys", SimpleNamespace(platform="linux"))
    return base_installation


def plan_for(r, suffix=""):
    ids, at = UUIDs(), SystemClock().now()
    return make_exchange(
        r.plan,
        make_operation(r.plan["source"], "uninstall", ids.new(), at),
        make_operation(r.plan["target"], "install", ids.new(), at),
        make_control(r.plan["target"], "start", ids.new(), at, ids.new()),
        str(r.root / ("exchange-journal" + suffix)),
        ids.new(),
        at,
    )


class Manager:
    def __init__(self, state):
        self.state = state

    async def inspect(self, deployment):
        state = self.state
        installed = state.installed is not None
        return {
            "available": True,
            "installed": installed,
            "definition_present": installed,
            "definition_matches": state.installed == deployment["sha256"],
            "enabled": installed,
            "state": "running" if state.native.lock else "stopped",
            "pid": state.native.identity["pid"] if state.native.lock else None,
        }

    async def change(self, deployment, action):
        state = self.state
        state.changes.append(action)
        if state.entered is not None:
            state.entered.set()
            await state.wait.wait()
        if getattr(state, "unknown_without_effect", False):
            return {"effect": "unknown"}
        # Registration runs under a real isolated instance lock.
        from omni.adapters.platform.runtime.paths import InstancePaths

        with pytest.raises(BusinessError):
            InstanceLock(InstancePaths(Path(deployment["request"]["home"]))).acquire()
        state.installed = deployment["sha256"] if action == "install" else None
        return {"effect": "unknown" if state.unknown == action else "applied"}

    async def start(self, deployment):
        self.state.changes.append("start")
        return await self.state.native.start(deployment)

    async def close(self, timeout_s):
        return self.state.cleanup


def build(r, plan, state=None):
    if state is None:
        state = SimpleNamespace(
            installed=r.plan["source"]["sha256"],
            native=Native(r.plan["target"]),
            changes=[],
            unknown="",
            cleanup=True,
            entered=None,
            wait=None,
        )
    reservation = marker(plan)
    release_files = ReleaseFiles(r.deployments, exchange=reservation)
    clock, ids = SystemClock(), UUIDs()
    selection = Releases(r.deployments, release_files, ReleaseData(release_files), Manager(state), clock, ids)
    source = ServiceOperations(
        ExchangeSourceDeployment(r.plan, release_files),
        ServiceOperationFiles(exchange=reservation),
        Manager(state),
        clock,
        ids,
    )
    target = ServiceOperations(r.deployments, ServiceOperationFiles(exchange=reservation), Manager(state), clock, ids)
    control = ServiceControl(
        r.deployments, ServiceControlFiles(exchange=reservation), Manager(state), Boot(state.native), clock, ids
    )
    steps = ExchangeSteps(source, target, selection, control)
    files = ExchangeFiles(r.deployments)
    service = ServiceExchange(files, steps, clock, ids)
    return SimpleNamespace(
        service=service, state=state, files=files, steps=steps, source=source, target=target, control=control
    )


@pytest.fixture
def exchange(release):
    r = release
    r.service.prepare(r.plan, r.plan["sha256"])
    return r, plan_for(r)


def test_exchange_complete_orders_all_stages_keeps_source_and_uses_reserved_boot(exchange):
    r, plan = exchange
    source, target = r.files.homes(r.plan)
    source_sha = hashlib.sha256(source.database.read_bytes()).hexdigest()

    async def scenario():
        x = build(r, plan)
        try:
            result = await x.service.apply(plan, plan["sha256"])
            assert result["state"] == "finished" and result["ready_at_completion"] is True
            assert result["completed_stages"] == list(STAGES) and result["ready"] is False
            assert x.state.changes == ["uninstall", "install", "start"]
            assert x.state.native.identity["boot_id"] == plan["start_target"]["boot_id"]
            assert not (target.control / "release-fence.json").exists()
            assert hashlib.sha256(source.database.read_bytes()).hexdigest() == source_sha
            with pytest.raises(BusinessError):
                InstanceLock(source).acquire()
            for home in (source, target):
                assert not (home.control / "service-exchange.json").exists()
            again = build(r, plan, x.state)
            historical = await again.service.apply(plan, plan["sha256"])
            assert historical["historically_ready"] is True and historical["ready"] is False
            assert "ready_at_completion" not in historical and x.state.changes == ["uninstall", "install", "start"]
            assert await again.service.close(1)
        finally:
            assert await x.service.close(1)
            await x.state.native.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("unknown", ["uninstall", "install"])
def test_unknown_applied_registration_is_observed_on_resume_without_repeat(exchange, unknown):
    r, plan = exchange

    async def scenario():
        x = build(r, plan)
        x.state.unknown = unknown
        first = await x.service.apply(plan, plan["sha256"])
        assert first["state"] == "blocked" and "start" not in x.state.changes
        assert await x.service.close(1)
        resumed = build(r, plan, x.state)
        try:
            assert (await resumed.service.apply(plan, plan["sha256"], resume=True))["state"] == "finished"
            assert x.state.changes == ["uninstall", "install", "start"]
        finally:
            assert await resumed.service.close(1)
            await x.state.native.dispose()

    asyncio.run(scenario())


def test_cancelled_waiter_does_not_cancel_exchange_or_release_ownership(exchange):
    r, plan = exchange

    async def scenario():
        x = build(r, plan)
        x.state.entered, x.state.wait = asyncio.Event(), asyncio.Event()
        waiter = asyncio.create_task(x.service.apply(plan, plan["sha256"]))
        await x.state.entered.wait()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not await x.service.close(0)
        other = ExchangeFiles(r.deployments)
        with pytest.raises(BusinessError), other.exclusive(plan):
            pytest.fail("Cancelled waiting must retain the actual exchange lock")
        x.state.wait.set()
        await x.service._pending
        try:
            assert x.service.inspect(plan)["state"] == "finished"
            assert await x.service.close(1)
        finally:
            await x.state.native.dispose()

    asyncio.run(scenario())


def test_resume_after_parent_dispatch_without_child_intent_only_observes(exchange):
    r, plan = exchange
    x = build(r, plan)
    with x.files.exclusive(plan):
        x.files.claim(plan)
        x.service._append(plan, "intent")
        x.service._append(plan, "dispatch", STAGES[0])

    async def scenario():
        result = await x.service.apply(plan, plan["sha256"], resume=True)
        assert result["state"] == "blocked" and not x.state.changes
        assert x.source.files.latest(plan["remove_source"]) is None
        assert await x.service.close(1)
        # Explicit abandonment frees only reservations, never data, source registration or release fences.
        stopped = build(r, plan, x.state)
        result = await stopped.service.apply(plan, plan["sha256"], abandon=True)
        assert result["state"] == "abandoned" and x.state.installed == r.plan["source"]["sha256"]
        assert r.service.inspect(r.plan)["state"] == "prepared"
        assert await stopped.service.close(1)

    asyncio.run(scenario())


def test_cleanup_failure_retains_exchange_and_instance_until_actual_recovery(exchange):
    r, plan = exchange

    async def scenario():
        x = build(r, plan)
        x.state.cleanup = False
        assert (await x.service.apply(plan, plan["sha256"]))["state"] == "blocked"
        assert not await x.service.close(0)
        source, _ = r.files.homes(r.plan)
        with pytest.raises(BusinessError):
            InstanceLock(source).acquire()
        x.state.cleanup = True
        assert await x.service.close(1)
        resumed = build(r, plan, x.state)
        try:
            assert (await resumed.service.apply(plan, plan["sha256"], resume=True))["state"] == "finished"
            assert x.state.changes == ["uninstall", "install", "start"]
        finally:
            assert await resumed.service.close(1)
            await x.state.native.dispose()

    asyncio.run(scenario())


def test_reservation_blocks_unrelated_registration_control_selection_and_other_exchange(exchange):
    r, plan = exchange
    x = build(r, plan)
    with x.files.exclusive(plan):
        x.files.claim(plan)
        x.service._append(plan, "intent")
    for files, operation in (
        (ServiceOperationFiles(), plan["remove_source"]),
        (ServiceControlFiles(), plan["start_target"]),
    ):
        with pytest.raises(BusinessError) as caught, files.exclusive(operation):
            pytest.fail("Reserved service must reject an independent command")
        assert caught.value.code == "service_exchange_reserved"
    with pytest.raises(BusinessError) as caught, r.files.exclusive(r.plan):
        pytest.fail("An unrelated release selection must not cross the reservation")
    assert caught.value.code == "service_exchange_reserved"
    other = plan_for(r, "-other")
    files = ExchangeFiles(r.deployments)
    with files.exclusive(other), pytest.raises(BusinessError) as caught:
        files.claim(other)
    assert caught.value.code == "service_exchange_reserved"
    asyncio.run(x.service.close(1))


def test_claim_before_intent_and_empty_new_journal_can_resume(exchange):
    r, plan = exchange
    x = build(r, plan)
    Path(plan["journal"]).mkdir()
    private_path(Path(plan["journal"]), directory=True)
    with x.files.exclusive(plan):
        x.files.claim(plan)
    assert x.files.history(plan) == []

    async def scenario():
        try:
            assert (await x.service.apply(plan, plan["sha256"], resume=True))["state"] == "finished"
        finally:
            assert await x.service.close(1)
            await x.state.native.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("field,value", [("starts_service", 1), ("stops_service", True), ("automatic_rollback", True)])
def test_plan_rejects_changed_flags(exchange, field, value):
    _, plan = exchange
    bad = copy.deepcopy(plan)
    bad[field] = value
    with pytest.raises(BusinessError):
        validate_exchange(bad)


def test_plan_requires_matching_child_scope_and_nonoverlapping_journal(exchange):
    r, plan = exchange
    with pytest.raises(BusinessError):
        make_exchange(
            r.plan,
            plan["install_target"],
            plan["remove_source"],
            plan["start_target"],
            plan["journal"],
            UUIDs().new(),
            SystemClock().now(),
        )
    with pytest.raises(BusinessError) as caught:
        make_exchange(
            r.plan,
            plan["remove_source"],
            plan["install_target"],
            plan["start_target"],
            r.plan["paths"]["journal"],
            UUIDs().new(),
            SystemClock().now(),
        )
    assert caught.value.code == "service_exchange_journal_unsafe"


@pytest.mark.parametrize("damage", ["sequence", "hash", "foreign", "extra"])
def test_journal_tampering_is_never_treated_as_success(exchange, damage):
    r, plan = exchange
    x = build(r, plan)
    with x.files.exclusive(plan):
        x.files.claim(plan)
        x.service._append(plan, "intent")
    event = Path(plan["journal"]) / "0000.json"
    value = json.loads(event.read_text(encoding="utf-8"))
    if damage == "extra":
        (event.parent / "extra").write_bytes(b"unrecognized")
    else:
        if damage == "sequence":
            value["sequence"] = True
        elif damage == "hash":
            value["sha256"] = "0" * 64
        else:
            value["record"]["state"] = "finished"
        event.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(BusinessError):
        x.service.inspect(plan)
    asyncio.run(x.service.close(1))


def test_out_of_order_or_foreign_or_not_cleaned_proofs_cannot_complete_stage(exchange):
    _, plan = exchange
    first = {"state": "intent", "stage": "", "recorded_at": SystemClock().now(), "proof": {}}
    with pytest.raises(BusinessError):
        progress(plan, [first, {**first, "state": "dispatch", "stage": STAGES[1]}])
    identity, sha = stage_identity(plan, STAGES[0])
    proof = {
        "stage": STAGES[0],
        "operation_id": identity,
        "operation_sha256": sha,
        "succeeded": True,
        "cleanup_complete": False,
        "error_code": "",
        "verification": "observed",
    }
    with pytest.raises(BusinessError):
        checked_proof(plan, STAGES[0], {**proof, "operation_id": UUIDs().new()})
    with pytest.raises(BusinessError):
        progress(
            plan,
            [
                first,
                {**first, "state": "dispatch", "stage": STAGES[0]},
                {**first, "state": "complete", "stage": STAGES[0], "proof": proof},
            ],
        )


def test_cli_exchange_is_explicit_and_requires_reviewed_sha():
    args = parser().parse_args(["exchange", "plan", "release.json", "journal", "out.json"])
    assert args.exchange_command == "plan"
    with pytest.raises(SystemExit):
        parser().parse_args(["exchange", "apply", "plan.json"])
    args = parser().parse_args(["exchange", "resume", "plan.json", "--approve", "f" * 64])
    assert args.approve == "f" * 64


def test_unapproved_exchange_has_no_journal_reservation_or_native_effect(exchange):
    r, plan = exchange
    x = build(r, plan)

    async def scenario():
        with pytest.raises(BusinessError) as caught:
            await x.service.apply(plan, "0" * 64)
        assert caught.value.code == "service_exchange_not_approved"
        assert not Path(plan["journal"]).exists() and not x.state.changes
        assert await x.service.close(1)

    asyncio.run(scenario())


def test_read_only_resume_can_prove_absence_even_when_child_intent_is_missing(exchange):
    r, plan = exchange
    x = build(r, plan)
    x.state.installed = None
    with x.files.exclusive(plan):
        x.files.claim(plan)
        x.service._append(plan, "intent")
        x.service._append(plan, "dispatch", STAGES[0])

    async def scenario():
        try:
            assert (await x.service.apply(plan, plan["sha256"], resume=True))["state"] == "finished"
            assert x.state.changes == ["install", "start"]
            assert x.source.files.latest(plan["remove_source"])["verification"] == "observed_registration_postcondition"
        finally:
            assert await x.service.close(1)
            await x.state.native.dispose()

    asyncio.run(scenario())


def test_unresolved_native_effect_cannot_be_replayed_or_abandoned(exchange):
    r, plan = exchange

    async def scenario():
        x = build(r, plan)
        x.state.unknown_without_effect = True
        assert (await x.service.apply(plan, plan["sha256"]))["state"] == "blocked"
        assert await x.service.close(1)
        resumed = build(r, plan, x.state)
        assert (await resumed.service.apply(plan, plan["sha256"], resume=True))["state"] == "blocked"
        assert x.state.changes == ["uninstall"] and await resumed.service.close(1)
        abandoned = build(r, plan, x.state)
        with pytest.raises(BusinessError) as caught:
            await abandoned.service.apply(plan, plan["sha256"], abandon=True)
        assert caught.value.code == "service_exchange_child_unresolved"
        assert (r.files.homes(r.plan)[0].control / "service-exchange.json").exists()
        assert await abandoned.service.close(1)

    asyncio.run(scenario())


@pytest.mark.parametrize("drift", ["program", "source_data"])
def test_drift_after_source_removal_stops_selection_and_preserves_changed_files(exchange, drift, monkeypatch):
    r, plan = exchange
    x = build(r, plan)
    original = x.steps.perform
    changed = (
        r.source_package / "adapters/isolated.py"
        if drift == "program"
        else r.files.homes(r.plan)[0].database.with_name("omni.db-journal")
    )

    async def perform(value, stage, *, reconcile):
        result = await original(value, stage, reconcile=reconcile)
        if stage == STAGES[0]:
            changed.write_bytes(b"preserve drift; no target selection")
        return result

    monkeypatch.setattr(x.steps, "perform", perform)

    async def scenario():
        result = await x.service.apply(plan, plan["sha256"])
        assert result["state"] == "blocked" and result["active_stage"] == "select_target"
        assert x.state.changes == ["uninstall"] and r.service.inspect(r.plan)["state"] == "prepared"
        assert changed.read_bytes() == b"preserve drift; no target selection"
        assert await x.service.close(1)

    asyncio.run(scenario())


def test_historical_successful_start_is_rechecked_before_resuming_completion(exchange, monkeypatch):
    r, plan = exchange
    x = build(r, plan)
    original = x.files.append

    def interrupted(value, event):
        if event["state"] == "complete" and event["stage"] == "start_target":
            raise OSError("Injected crash after child success, before parent completion")
        return original(value, event)

    monkeypatch.setattr(x.files, "append", interrupted)

    async def scenario():
        with pytest.raises(OSError):
            await x.service.apply(plan, plan["sha256"])
        assert await x.service.close(1)
        await x.state.native.dispose()
        resumed = build(r, plan, x.state)
        result = await resumed.service.apply(plan, plan["sha256"], resume=True)
        assert result["state"] == "blocked" and result["active_stage"] == "start_target"
        assert x.state.changes == ["uninstall", "install", "start"]
        assert await resumed.service.close(1)

    asyncio.run(scenario())


def test_composition_plan_and_inspect_never_execute_native_changes(exchange):
    from omni.composition.service_exchange import run_exchange

    r, _ = exchange
    release_path = r.root / "reviewed-release.json"
    r.files.publish(r.plan, str(release_path))
    plan_path = r.root / "exchange-review.json"

    async def scenario():
        result = await run_exchange("plan", release_path, journal=r.root / "planned-exchange", output=plan_path)
        plan = validate_exchange(r.deployments.read(plan_path))
        assert result["executed"] is False and result["sha256"] == plan["sha256"]
        assert not Path(plan["journal"]).exists()
        inspected = await run_exchange("inspect", plan_path)
        assert inspected["state"] == "not_started" and inspected["ready"] is False

    asyncio.run(scenario())


@pytest.mark.parametrize("target", ["macos-system"])
def test_macos_system_exchange_declares_activation_effects_without_any_native_calls(target):
    from test_v2_service_deployment import request, runtime

    from omni.domain.releases import FENCE_FILES, make_release
    from omni.domain.service_deployment import make_plan

    ids, at = UUIDs(), SystemClock().now()
    captured = runtime(target)
    captured["files"] += [{"path": name, "bytes": 100, "sha256": "e" * 64} for name in FENCE_FILES]
    source = make_plan(request(target), captured, ids.new(), at)
    next_runtime = json.loads(json.dumps(captured).replace("/opt/versions/v2/venv", "/opt/versions/next/venv"))
    next_request = request(target)
    for name in ("home", "bundle_dir"):
        next_request[name] += "-next"
    destination = make_plan(next_request, next_runtime, ids.new(), at)
    release = make_release(
        source, destination, {"journal": "/tmp/reviewed-release", "backup": "/tmp/reviewed-snapshot"}, ids.new(), at
    )
    value = make_exchange(
        release,
        make_operation(source, "uninstall", ids.new(), at),
        make_operation(destination, "install", ids.new(), at),
        make_control(destination, "start", ids.new(), at, ids.new()),
        "/tmp/exchange-journal",
        ids.new(),
        at,
    )
    assert value["stops_service"] and value["releases_activation_on_abandon"]
    assert validate_exchange(value) == value
