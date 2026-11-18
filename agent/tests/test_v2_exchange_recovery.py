"""Quarantine, immutable unknown receipts and separate-slot rescue with isolated stores and injected managers."""

from __future__ import annotations

import asyncio
import copy
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_v2_service_control import Boot, Clock, Native
from test_v2_service_exchange import base_installation as base_installation
from test_v2_service_exchange import exchange as exchange
from test_v2_service_exchange import installation as installation
from test_v2_service_exchange import release as release
from v2_configuration_helpers import model_uow

from omni.adapters.persistence.database import Database
from omni.adapters.persistence.releases import ReleaseData
from omni.adapters.persistence.sqlite_access import sqlite_connection
from omni.adapters.platform.exchange_deployments import ExchangeSourceDeployment
from omni.adapters.platform.exchange_files import ExchangeFiles
from omni.adapters.platform.runtime.paths import InstanceLock
from omni.adapters.platform.release_files import ReleaseFiles
from omni.adapters.platform.service_control_files import ServiceControlFiles
from omni.adapters.platform.service_operation_files import ServiceOperationFiles
from omni.adapters.primitives import SystemClock, UUIDs
from omni.application.exchange_recovery import ExchangeRecovery
from omni.application.exchange_recovery_steps import ExchangeRecoverySteps
from omni.application.exchange_steps import ExchangeSteps
from omni.application.releases import Releases
from omni.application.service_control import ServiceControl
from omni.application.service_exchange import ServiceExchange
from omni.application.service_operations import ServiceOperations
from omni.domain.contracts import BusinessError, ModelReply
from omni.domain.releases import validate_release
from omni.domain.service_deployment import manager_identity, validate_plan
from omni.domain.service_exchange import marker
from omni.domain.service_isolation import validate_isolation


class Manager:
    def __init__(self, state):
        self.state = state

    async def inspect(self, plan):
        state, name = self.state, manager_identity(plan)["name"]
        present = name in state.registry
        return {
            "available": True,
            "installed": present,
            "definition_present": present,
            "definition_matches": state.registry.get(name) == plan["sha256"],
            "enabled": present,
            "state": "running"
            if state.native.lock and state.native.paths.home == Path(plan["request"]["home"])
            else "stopped",
            "pid": state.native.identity["pid"]
            if state.native.lock and state.native.paths.home == Path(plan["request"]["home"])
            else None,
        }

    async def change(self, plan, action):
        state, name = self.state, manager_identity(plan)["name"]
        state.changes.append((action, name))
        if action == state.unknown:
            return {"effect": "unknown"}
        if action == "install":
            assert name not in state.registry
            state.registry[name] = plan["sha256"]
        else:
            assert state.registry[name] == plan["sha256"]
            del state.registry[name]
        return {"effect": "applied"}

    async def start(self, plan):
        self.state.changes.append(("start", manager_identity(plan)["name"]))
        return await self.state.native.start(plan)

    async def close(self, timeout_s):
        return True


def build(r, plan, state=None):
    if state is None:
        state = SimpleNamespace(
            registry={manager_identity(plan["release"]["source"])["name"]: plan["release"]["source"]["sha256"]},
            native=Native(plan["release"]["target"]),
            changes=[],
            unknown="install",
        )
    clock, ids, reservation = Clock(), UUIDs(), marker(plan)
    files = ExchangeFiles(r.deployments)
    storage = ReleaseFiles(r.deployments, exchange=reservation)
    selection = Releases(r.deployments, storage, ReleaseData(storage), Manager(state), clock, ids)
    source = ServiceOperations(
        ExchangeSourceDeployment(plan["release"], storage),
        ServiceOperationFiles(exchange=reservation),
        Manager(state),
        clock,
        ids,
    )
    target = ServiceOperations(r.deployments, ServiceOperationFiles(exchange=reservation), Manager(state), clock, ids)
    boot = Boot(state.native)
    control = ServiceControl(r.deployments, ServiceControlFiles(exchange=reservation), Manager(state), boot, clock, ids)
    service = ServiceExchange(files, ExchangeSteps(source, target, selection, control), clock, ids)
    recovery = ExchangeRecovery(files, ExchangeRecoverySteps(files, selection), clock, ids)
    return SimpleNamespace(
        service=service,
        recovery=recovery,
        files=files,
        storage=storage,
        selection=selection,
        state=state,
        boot=boot,
        control=control,
    )


async def interrupted(r, plan, kind="install"):
    x = build(r, plan)
    if kind == "start":
        x.state.unknown = ""
        x.boot.wrong_boot = True
    result = await x.service.apply(plan, plan["sha256"])
    assert result["state"] == "blocked"
    assert result["active_stage"] == ("install_target" if kind == "install" else "start_target")
    assert await x.service.close(1)
    if kind == "start":
        await x.state.native.dispose()  # The test target exits normally; isolation never forces a real app to stop.
    return x


def isolation(x, r, plan):
    output = r.root / "isolation.json"
    result = x.recovery.plan(plan, "Preserve the uncertain outcome and restore in a new directory", str(output))
    value = validate_isolation(r.deployments.read(output))
    assert result["executed"] is False
    return value


def third_installation(r):
    package, prefix = r.root / "rescue-program/omni", r.root / "rescue-environment"
    shutil.copytree(r.current["package"], package)
    shutil.copytree(r.current["prefix"], prefix)
    (package / "adapters/rescue.py").write_text("BUILD='rescue'\n", encoding="utf-8")
    r.current.update(package=package, prefix=prefix)


@pytest.mark.parametrize("kind", ["install", "start"])
def test_isolate_and_rescue_complete_without_replaying_or_erasing_unknown(exchange, kind):
    r, plan = exchange

    async def scenario():
        x = await interrupted(r, plan, kind)
        target = r.files.homes(r.plan)[1]
        namespace, operation = (
            ("service-operations", plan["install_target"])
            if kind == "install"
            else ("service-controls", plan["start_target"])
        )
        db = Database(target.database, SystemClock(), UUIDs())
        try:
            agent = (await db.write(lambda u: u.create_agent("isolated financial witness")))["id"]
            await db.write(lambda u: model_uow(u, agent, "offline"))
            turn = await db.write(lambda u: u.accept_turn(agent, None, UUIDs().new(), "isolated fixture", {}))
            call = await db.write(lambda u: u.reserve_call(agent, turn["run_id"], 250000, 1000000, "offline"))
            await db.write(lambda u: u.settle_call(agent, call, None))
            spent = await db.write(lambda u: u.reserve_call(agent, turn["run_id"], 50000, 1000000, "offline"))
            await db.write(lambda u: u.settle_call(agent, spent, ModelReply("offline", cost_micro=50000)))
        finally:
            await db.close()
        folder = target.control / namespace / operation["id"]
        originals = {path.name: path.read_bytes() for path in folder.iterdir()}
        checked = isolation(x, r, plan)
        result = x.recovery.apply(checked, checked["sha256"])
        assert result["state"] == "isolated" and result["external_effect"] == "unknown"
        assert x.recovery.apply(checked, checked["sha256"]) == result
        assert (target.control / namespace / "active.json").exists()
        assert originals == {path.name: path.read_bytes() for path in folder.iterdir()}
        for home in r.files.homes(r.plan):
            with pytest.raises(BusinessError) as caught:
                InstanceLock(home).acquire()
            assert caught.value.code == "release_fenced"
        with pytest.raises(BusinessError) as caught:
            await build(r, plan, x.state).service.apply(plan, plan["sha256"], resume=True)
        assert caught.value.code == "service_exchange_isolated"
        third_installation(r)
        request = {
            "target": {
                **plan["release"]["target"]["request"],
                "home": str(r.root / "rescued-home"),
                "bundle_dir": str(r.root / "rescued-bundle"),
            },
            "backup": str(r.root / "failed-version-backup"),
            "journal": str(r.root / "rescue-release-journal"),
        }
        output = r.root / "rescue-release.json"
        result = x.recovery.rescue_plan(plan, request, str(output))
        rescued = validate_release(r.deployments.read(output))
        assert result["executed"] is False and rescued["target"]["isolated_registration"] is True
        old_name, new_name = (manager_identity(rescued[side])["name"] for side in ("source", "target"))
        assert old_name != new_name
        release_files = ReleaseFiles(r.deployments)
        selection = Releases(
            r.deployments, release_files, ReleaseData(release_files), Manager(x.state), SystemClock(), UUIDs()
        )
        assert selection.prepare(rescued, rescued["sha256"])["state"] == "prepared"
        assert selection.prepared(release_files.history(rescued))["restore_from"] == rescued["paths"]["backup"]
        assert rescued["recovery"]["restore_policy"] == "latest_failed"
        assert rescued["recovery"]["snapshot"] == r.service.prepared(r.files.history(r.plan))["backup"]
        restored_home = release_files.homes(rescued)[1]
        with sqlite_connection(restored_home.database) as connection:
            assert connection.execute(
                "SELECT state,reserved_micro,cost_micro FROM model_calls WHERE id=?", (call,)
            ).fetchone() == ("unknown", 250000, None)
            assert connection.execute(
                "SELECT held_micro,spent_micro FROM budget_accounts WHERE agent_id=?", (agent,)
            ).fetchone() == (250000, 50000)
        new_file = r.root / "rescue-exchange.json"
        # Planning uses the current installation and the prepared rescue, not the old executable.
        planner = ServiceExchange(
            ExchangeFiles(r.deployments),
            ExchangeSteps(
                ServiceOperations(r.deployments, ServiceOperationFiles(), Manager(x.state), SystemClock(), UUIDs()),
                ServiceOperations(r.deployments, ServiceOperationFiles(), Manager(x.state), SystemClock(), UUIDs()),
                selection,
                ServiceControl(
                    r.deployments, ServiceControlFiles(), Manager(x.state), Boot(x.state.native), SystemClock(), UUIDs()
                ),
            ),
            SystemClock(),
            UUIDs(),
        )
        planner.plan(rescued, str(r.root / "rescue-exchange-journal"), str(new_file))
        assert await planner.close(1)
        new_plan = r.deployments.read(new_file)
        x.state.unknown = ""
        x.state.native = Native(rescued["target"])
        resumed = build(r, new_plan, x.state)
        try:
            outcome = await resumed.service.apply(new_plan, new_plan["sha256"])
            assert outcome["state"] == "finished" and outcome["predecessor_cleanup_pending"] is True
            assert outcome["predecessor_cleanup_status"] == "not_observed"
            assert x.state.registry[new_name] == rescued["target"]["sha256"]
            # An eventual old operation changes only its old key, never the rescue definition.
            x.state.registry.pop(old_name, None)
            assert (await resumed.control.boot.probe(rescued["target"]))["ready"] is True
            assert x.state.registry[new_name] == rescued["target"]["sha256"]
            assert originals == {path.name: path.read_bytes() for path in folder.iterdir()}
            assert (target.control / "service-exchange.json").exists()
        finally:
            assert await resumed.service.close(1)
            await x.state.native.dispose()
        from omni.composition.instance import open_instance

        instance = await open_instance(restored_home.home, capabilities=())
        try:
            with sqlite_connection(restored_home.database) as connection:
                assert connection.execute(
                    "SELECT state,reserved_micro,cost_micro FROM model_calls WHERE id=?", (call,)
                ).fetchone() == ("unknown", 250000, None)
                assert connection.execute(
                    "SELECT held_micro,spent_micro FROM budget_accounts WHERE agent_id=?", (agent,)
                ).fetchone() == (250000, 50000)
        finally:
            assert (await instance.close())["complete"] is True

    asyncio.run(scenario())


def test_isolation_stale_approval_and_running_target_are_rejected(exchange):
    r, plan = exchange

    async def scenario():
        x = build(r, plan)
        x.state.unknown = ""
        x.boot.wrong_boot = True
        assert (await x.service.apply(plan, plan["sha256"]))["state"] == "blocked"
        assert await x.service.close(1)
        checked = isolation(x, r, plan)
        with pytest.raises(BusinessError) as caught:
            x.recovery.apply(checked, "0" * 64)
        assert caught.value.code == "service_isolation_not_approved"
        with pytest.raises(BusinessError):
            x.recovery.apply(checked, checked["sha256"])
        assert x.files.history(plan)[-1]["state"] == "isolating"
        await x.state.native.dispose()
        assert x.recovery.apply(checked, checked["sha256"])["state"] == "isolated"

    asyncio.run(scenario())


def test_partial_fence_crash_resumes_exact_plan_without_touching_unknown(exchange, monkeypatch):
    r, plan = exchange

    async def scenario():
        x = await interrupted(r, plan)
        checked = isolation(x, r, plan)
        original = x.storage._publish_fence

        def fail(value, side):
            if side == "target":
                raise OSError("injected durable source fence, before target fence")
            return original(value, side)

        monkeypatch.setattr(x.storage, "_publish_fence", fail)
        with pytest.raises(OSError):
            x.recovery.apply(checked, checked["sha256"])
        assert x.files.history(plan)[-1]["state"] == "isolating"
        monkeypatch.setattr(x.storage, "_publish_fence", original)
        assert x.recovery.apply(checked, checked["sha256"])["state"] == "isolated"

    asyncio.run(scenario())


def test_plan2_rejects_legacy_shape_and_non_boolean_registration_slot(exchange):
    _, plan = exchange
    for value in (1, "yes", None):
        changed = copy.deepcopy(plan["release"]["target"])
        changed["isolated_registration"] = value
        with pytest.raises(BusinessError):
            validate_plan(changed)
    changed = copy.deepcopy(plan["release"]["target"])
    changed["format"] = "omni-service-plan-1"
    changed.pop("isolated_registration")
    with pytest.raises(BusinessError):
        validate_plan(changed)


def test_cli_composition_publishes_isolation_and_new_slot_rescue_without_native_changes(exchange):
    from omni.composition.service_exchange import run_exchange

    r, plan = exchange

    async def scenario():
        x = await interrupted(r, plan)
        input_path, isolation_path = r.root / "exchange-input.json", r.root / "isolation-review.json"
        x.files.publish(plan, str(input_path))
        reviewed = await run_exchange(
            "isolation-plan", input_path, reason="Retain uncertain effects", output=isolation_path
        )
        assert reviewed["executed"] is False
        assert (await run_exchange("apply-isolation", isolation_path, approval=reviewed["sha256"]))[
            "state"
        ] == "isolated"
        third_installation(r)
        request_path, output = r.root / "rescue-request.json", r.root / "rescue-cli.json"
        request_path.write_text(
            json.dumps(
                {
                    "target": {
                        **plan["release"]["target"]["request"],
                        "home": str(r.root / "rescue-cli-home"),
                        "bundle_dir": str(r.root / "rescue-cli-bundle"),
                    },
                    "backup": str(r.root / "rescue-cli-backup"),
                    "journal": str(r.root / "rescue-cli-journal"),
                }
            ),
            encoding="utf-8",
        )
        result = await run_exchange("rescue-plan", input_path, request=request_path, output=output)
        rescued = validate_release(r.deployments.read(output))
        assert result["executed"] is False and rescued["recovery"]["restore_policy"] == "latest_failed"
        assert rescued["target"]["isolated_registration"] is True
        assert len(x.state.changes) == 2  # Only the initial injected uninstall/install; no rescue action yet.

    asyncio.run(scenario())
