"""Online source shutdown to verified release preparation; no real HTTP, OS manager or model actions."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from test_v2_releases import Manager
from test_v2_service_control import Boot, Clock, Native
from test_v2_service_exchange import base_installation as base_installation
from test_v2_service_exchange import installation as installation
from test_v2_service_exchange import release as release

from omni.adapters.persistence.releases import ReleaseData
from omni.adapters.platform.exchange_deployments import ExchangeSourceDeployment
from omni.adapters.platform.release_files import ReleaseFiles
from omni.adapters.platform.service_control_files import ServiceControlFiles
from omni.adapters.primitives import SystemClock, UUIDs
from omni.application.release_drain import ReleaseDrain
from omni.application.releases import Releases
from omni.application.service_control import ServiceControl
from omni.composition.cli import parser
from omni.domain.contracts import BusinessError
from omni.domain.releases import make_release, validate_release
from omni.domain.service_control import make_control


class DrainBoot(Boot):
    def __init__(self, native):
        super().__init__(native)
        self.ignore = False
        self.entered = self.release = None

    async def drain(self, operation):
        if self.entered:
            self.entered.set()
            await self.release.wait()
        if self.ignore:
            self.drains += 1
            return {"effect": "unknown", "accepted": True}
        return await super().drain(operation)


def build(r, plan, native, boot=None):
    files = ReleaseFiles(r.deployments)
    boot = boot or DrainBoot(native)
    control = ServiceControl(
        ExchangeSourceDeployment(plan, files), ServiceControlFiles(), native, boot, Clock(), UUIDs()
    )
    return Releases(
        r.deployments, files, ReleaseData(files), Manager(), SystemClock(), UUIDs(), drain=ReleaseDrain(control)
    ), boot


def online(r, native):
    stop = make_control(r.plan["source"], "stop", UUIDs().new(), SystemClock().now(), native.identity["boot_id"])
    return make_release(
        r.plan["source"], r.plan["target"], r.plan["paths"], r.plan["id"], r.plan["created_at"], source_stop=stop
    )


def test_online_plan_is_read_only_and_apply_drains_then_publishes_verified_snapshot(release):
    r = release

    async def scenario():
        native = Native(r.plan["source"])
        await native.start(r.plan["source"])
        service, boot = build(r, r.plan, native)
        output = r.root / "online-release.json"
        result = await service.plan_online(
            r.plan["source"], {"target": r.plan["target"]["request"], **r.plan["paths"]}, str(output)
        )
        plan = validate_release(r.deployments.read(output))
        assert (
            result["executed"] is False
            and plan["stops_service"] is True
            and plan["source_stop"]["boot_id"] == native.identity["boot_id"]
        )
        assert native.lock is not None and boot.drains == 0 and not r.files.homes(plan)[1].home.exists()
        assert await service.close(1)
        service, boot = build(r, plan, native)
        with pytest.raises(BusinessError) as caught:
            service.prepare(plan, plan["sha256"])
        assert caught.value.code == "release_requires_drain"
        assert (await service.prepare_online(plan, plan["sha256"]))["state"] == "prepared"
        assert boot.drains == 1 and native.lock is None
        phases = [row["phase"] for row in service.files.history(plan)]
        assert phases == ["drain_intent", "drained", "intent", "snapshot", "restored", "prepared"]
        prepared = service.prepared(service.files.history(plan))
        assert prepared["credentials_restored"] is False and prepared["execution_quarantined"] is True
        assert await service.close(1)
        again, again_boot = build(r, plan, native)
        assert (await again.prepare_online(plan, plan["sha256"]))["state"] == "prepared" and again_boot.drains == 0
        assert await again.close(1)
        await native.dispose()

    asyncio.run(scenario())


def test_unknown_drain_does_not_copy_or_resend_and_can_be_observed_after_exit(release):
    r = release

    async def scenario():
        native = Native(r.plan["source"])
        await native.start(r.plan["source"])
        plan = online(r, native)
        service, boot = build(r, plan, native)
        boot.ignore = True
        assert (await service.prepare_online(plan, plan["sha256"]))["state"] == "drain_unknown"
        assert boot.drains == 1 and not r.files.homes(plan)[1].home.exists()
        assert await service.close(1)
        next_service, next_boot = build(r, plan, native)
        assert (await next_service.prepare_online(plan, plan["sha256"]))["state"] == "drain_unknown"
        assert next_boot.drains == 0 and await next_service.close(1)
        await native.dispose()
        last, last_boot = build(r, plan, native)
        assert (await last.prepare_online(plan, plan["sha256"]))["state"] == "prepared"
        assert last_boot.drains == 0 and await last.close(1)

    asyncio.run(scenario())


def test_cancelled_waiter_keeps_drain_and_preparation_mutex_owned(release):
    r = release

    async def scenario():
        native = Native(r.plan["source"])
        await native.start(r.plan["source"])
        plan = online(r, native)
        service, boot = build(r, plan, native)
        boot.entered, boot.release = asyncio.Event(), asyncio.Event()
        waiter = asyncio.create_task(service.prepare_online(plan, plan["sha256"]))
        await boot.entered.wait()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not await service.close(0)
        with pytest.raises(BusinessError), ReleaseFiles(r.deployments).preparation(plan):
            pytest.fail("The cancelled waiter cannot release actual preparation ownership")
        boot.release.set()
        assert (await service._pending)["state"] == "prepared"
        assert await service.close(1)
        await native.dispose()

    asyncio.run(scenario())


def test_parent_intent_without_child_does_not_authorize_a_new_drain(release):
    r = release

    async def scenario():
        native = Native(r.plan["source"])
        await native.start(r.plan["source"])
        plan = online(r, native)
        service, boot = build(r, plan, native)
        service.files.begin(plan)
        service._append(
            plan,
            "drain_intent",
            {"operation_id": plan["source_stop"]["id"], "operation_sha256": plan["source_stop"]["sha256"]},
        )
        assert (await service.prepare_online(plan, plan["sha256"]))["state"] == "drain_unknown" and boot.drains == 0
        assert await service.close(1)
        await native.dispose()
        again, again_boot = build(r, plan, native)
        assert (await again.prepare_online(plan, plan["sha256"]))["state"] == "prepared" and again_boot.drains == 0
        assert await again.close(1)

    asyncio.run(scenario())


def test_wrong_drain_witness_and_wrong_source_control_cannot_authorize_backup(release):
    r = release

    async def scenario():
        native = Native(r.plan["source"])
        await native.start(r.plan["source"])
        try:
            plan = online(r, native)
            with pytest.raises(BusinessError):
                make_release(
                    plan["source"],
                    plan["target"],
                    plan["paths"],
                    plan["id"],
                    plan["created_at"],
                    source_stop=make_control(
                        plan["target"], "stop", UUIDs().new(), SystemClock().now(), native.identity["boot_id"]
                    ),
                )
            files = ReleaseFiles(r.deployments)
            files.begin(plan)
            files.append(
                plan,
                "drain_intent",
                {"operation_id": UUIDs().new(), "operation_sha256": plan["source_stop"]["sha256"]},
                SystemClock().now(),
            )
            with pytest.raises(BusinessError) as caught:
                files.history(plan)
            assert caught.value.code == "release_drain_witness_changed"
        finally:
            await native.dispose()

    asyncio.run(scenario())


def test_online_cli_declares_real_stop_and_requires_exact_approval():
    planned = parser().parse_args(["release", "online-plan", "source", "request", "output"])
    assert planned.release_command == "online-plan"
    with pytest.raises(SystemExit):
        parser().parse_args(["release", "prepare-online", "plan"])
    args = parser().parse_args(["release", "prepare-online", "plan", "--approve", "a" * 64])
    assert args.approve == "a" * 64


def test_cli_composition_wires_reviewed_online_plan_and_once_only_drain(release, monkeypatch):
    from omni.composition import releases as composition

    r = release

    async def scenario():
        native = Native(r.plan["source"])
        await native.start(r.plan["source"])
        boot = DrainBoot(native)
        monkeypatch.setattr(composition, "ServiceReadiness", lambda: boot)
        request_path, output = r.root / "online-request.json", r.root / "online-plan.json"
        request_path.write_text(
            json.dumps({"target": r.plan["target"]["request"], **r.plan["paths"]}), encoding="utf-8"
        )
        try:
            reviewed = await composition.run_release(
                "online-plan", Path(r.plan["source"]["request"]["bundle_dir"]), request=request_path, output=output
            )
            plan = validate_release(r.deployments.read(output))
            assert reviewed["executed"] is False and boot.drains == 0 and plan["stops_service"] is True
            assert (await composition.run_release("prepare-online", output, approval=plan["sha256"]))[
                "state"
            ] == "prepared"
            assert boot.drains == 1 and native.lock is None
        finally:
            await native.dispose()

    asyncio.run(scenario())
