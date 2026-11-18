"""Authority, stale approval, source continuity and isolated native-key guards; no external services."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from test_v2_exchange_recovery import Manager, interrupted, isolation, third_installation
from test_v2_service_deployment import request, runtime
from test_v2_service_exchange import base_installation as base_installation
from test_v2_service_exchange import exchange as exchange
from test_v2_service_exchange import installation as installation
from test_v2_service_exchange import release as release

from omni.adapters.persistence.releases import ReleaseData
from omni.adapters.persistence.sqlite_access import sqlite_connection
from omni.adapters.platform.services.exchange_files import ExchangeFiles
from omni.adapters.platform.runtime.paths import InstanceLock
from omni.adapters.platform.releases.release_files import ReleaseFiles
from omni.adapters.primitives import SystemClock, UUIDs
from omni.application.releases import Releases
from omni.domain.contracts import BusinessError
from omni.domain.releases import make_release, validate_release
from omni.domain.service_deployment import make_plan, manager_identity
from omni.domain.service_exchange import STAGES, stage_identity


def test_changed_exchange_requires_a_new_isolation_review(exchange):
    r, plan = exchange

    async def scenario():
        x = await interrupted(r, plan)
        checked = isolation(x, r, plan)
        previous = x.files.history(plan)[-1]
        with x.files.exclusive(plan):
            x.files.append(plan, {**previous, "recorded_at": SystemClock().now()})
        with pytest.raises(BusinessError) as caught:
            x.recovery.apply(checked, checked["sha256"])
        assert caught.value.code == "service_isolation_stale"
        assert not (r.files.homes(r.plan)[1].control / "release-fence.json").exists()

    asyncio.run(scenario())


def test_rescue_requires_unchanged_reviewed_source_and_does_not_copy_drift(exchange):
    r, plan = exchange

    async def scenario():
        x = await interrupted(r, plan)
        checked = isolation(x, r, plan)
        x.recovery.apply(checked, checked["sha256"])
        third_installation(r)
        path = r.root / "rescue.json"
        request_value = {
            "target": {
                **r.plan["target"]["request"],
                "home": str(r.root / "rescue-home"),
                "bundle_dir": str(r.root / "rescue-bundle"),
            },
            "backup": str(r.root / "rescue-backup"),
            "journal": str(r.root / "rescue-journal"),
        }
        x.recovery.rescue_plan(plan, request_value, str(path))
        rescue = validate_release(r.deployments.read(path))
        with sqlite_connection(r.files.homes(r.plan)[1].database, read_only=False) as connection:
            connection.execute("PRAGMA user_version=17")
        files = ReleaseFiles(r.deployments)
        service = Releases(r.deployments, files, ReleaseData(files), Manager(x.state), SystemClock(), UUIDs())
        with pytest.raises(BusinessError) as caught:
            service.prepare(rescue, rescue["sha256"])
        assert caught.value.code == "release_recovery_source_changed"
        assert not Path(rescue["paths"]["backup"]).exists()
        with sqlite_connection(r.files.homes(r.plan)[1].database) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 17
        assert await service.close(1)

    asyncio.run(scenario())


def test_regular_rollback_cannot_replace_changed_data_with_an_older_snapshot(release):
    r = release
    r.service.prepare(r.plan, r.plan["sha256"])
    asyncio.run(r.service.select(r.plan, r.plan["sha256"]))
    prepared = r.service.prepared(r.files.history(r.plan))
    r.current.update(package=r.source_package, prefix=r.source_prefix)
    target = make_plan(
        {
            **r.plan["source"]["request"],
            "home": str(r.root / "rollback-home"),
            "bundle_dir": str(r.root / "rollback-bundle"),
        },
        r.deployments.capture(),
        UUIDs().new(),
        SystemClock().now(),
    )
    rollback = make_release(
        r.plan["target"],
        target,
        {"backup": str(r.root / "rollback-backup"), "journal": str(r.root / "rollback-journal")},
        UUIDs().new(),
        SystemClock().now(),
        {
            "release_sha256": r.plan["sha256"],
            "snapshot": prepared["backup"],
            "manifest_sha256": prepared["backup_manifest_sha256"],
            "isolation": None,
            "restore_policy": "original_snapshot",
            "expected_source_witness": prepared["target_witness"],
        },
    )
    with sqlite_connection(r.files.homes(r.plan)[1].database, read_only=False) as connection:
        connection.execute("PRAGMA user_version=23")
    with pytest.raises(BusinessError) as caught:
        r.service.prepare(rollback, rollback["sha256"])
    assert caught.value.code == "release_recovery_source_changed"
    assert not Path(rollback["paths"]["backup"]).exists()


@pytest.mark.parametrize("target", ["windows-user", "linux-user", "linux-system", "macos-user", "macos-system"])
def test_isolated_registration_keys_never_alias_old_or_another_recovery(target):
    ids, at = UUIDs(), SystemClock().now()
    old = make_plan(request(target), runtime(target), ids.new(), at)
    first = make_plan(request(target), runtime(target), ids.new(), at, isolated_registration=True)
    second = make_plan(request(target), runtime(target), ids.new(), at, isolated_registration=True)
    names = {manager_identity(item)["name"] for item in (old, first, second)}
    assert len(names) == 3 and first["id"] in manager_identity(first)["name"]


def test_windows_broker_scope_accepts_exactly_one_optional_recovery_uuid():
    source = (Path(__file__).parents[1] / "src/omni/adapters/workers/windows_services.ps1").read_text(encoding="utf-8")
    pattern = re.search(r"\$TaskName -cnotmatch '([^']+)'", source).group(1)
    original = "OmniOperator-V2-" + UUIDs().new()
    assert re.fullmatch(pattern, original)
    assert re.fullmatch(pattern, original + "-" + UUIDs().new())
    assert not re.fullmatch(pattern, original + "-" + UUIDs().new() + "-" + UUIDs().new())
    assert not re.fullmatch(pattern, original + "-arbitrary-name")


def test_exchange_journal_keeps_capacity_for_offline_isolation(exchange, monkeypatch):
    from omni.adapters.platform.services import exchange_files

    # Exercise the actual file and state protocol with a small injected capacity,
    # rather than repeatedly rereading 64 private Windows files for this guard.
    monkeypatch.setattr(exchange_files, "MAX_EVENTS", 8)
    r, plan = exchange
    files = ExchangeFiles(r.deployments)
    identity, sha = stage_identity(plan, STAGES[0])
    proof = {
        "stage": STAGES[0],
        "operation_id": identity,
        "operation_sha256": sha,
        "succeeded": False,
        "cleanup_complete": True,
        "error_code": "unresolved",
        "verification": "",
    }

    def event(state, stage="", value=None):
        return {"state": state, "stage": stage, "recorded_at": SystemClock().now(), "proof": value or {}}

    with files.exclusive(plan):
        files.claim(plan)
        files.append(plan, event("intent"))
        files.append(plan, event("dispatch", STAGES[0]))
        for _ in range(4):
            files.append(plan, event("blocked", STAGES[0], proof))
        with pytest.raises(BusinessError) as caught:
            files.append(plan, event("blocked", STAGES[0], proof))
        assert caught.value.code == "service_exchange_journal_full"
    from test_v2_exchange_recovery import build

    x = build(r, plan)
    checked = isolation(x, r, plan)
    assert x.recovery.apply(checked, checked["sha256"])["state"] == "isolated"
    assert len(files.history(plan)) == 8


def test_one_isolated_source_cannot_activate_two_separate_rescues(exchange):
    r, plan = exchange

    async def scenario():
        x = await interrupted(r, plan)
        reviewed = isolation(x, r, plan)
        x.recovery.apply(reviewed, reviewed["sha256"])
        third_installation(r)
        path = r.root / "first-rescue.json"
        request_value = {
            "target": {
                **r.plan["target"]["request"],
                "home": str(r.root / "first-home"),
                "bundle_dir": str(r.root / "first-bundle"),
            },
            "backup": str(r.root / "first-backup"),
            "journal": str(r.root / "first-journal"),
        }
        x.recovery.rescue_plan(plan, request_value, str(path))
        first = validate_release(r.deployments.read(path))
        other_target = make_plan(
            {
                **first["target"]["request"],
                "home": str(r.root / "other-home"),
                "bundle_dir": str(r.root / "other-bundle"),
            },
            r.deployments.capture(),
            UUIDs().new(),
            SystemClock().now(),
            isolated_registration=True,
        )
        second = make_release(
            first["source"],
            other_target,
            {"backup": str(r.root / "other-backup"), "journal": str(r.root / "other-journal")},
            UUIDs().new(),
            SystemClock().now(),
            first["recovery"],
        )
        for value in (first, second):
            files = ReleaseFiles(r.deployments)
            service = Releases(r.deployments, files, ReleaseData(files), Manager(x.state), SystemClock(), UUIDs())
            assert service.prepare(value, value["sha256"])["state"] == "prepared"
            assert await service.close(1)
        files = ReleaseFiles(r.deployments)
        selected = Releases(r.deployments, files, ReleaseData(files), Manager(x.state), SystemClock(), UUIDs())
        assert (await selected.select(first, first["sha256"]))["state"] == "selected"
        assert await selected.close(1)
        witness = files.homes(first)[0].control / "rescue-selection.json"
        original = witness.read_bytes()
        assert witness.exists()
        competing = Releases(r.deployments, files, ReleaseData(files), Manager(x.state), SystemClock(), UUIDs())
        with pytest.raises(BusinessError) as caught:
            await competing.select(second, second["sha256"])
        assert caught.value.code == "release_rescue_already_selected"
        assert witness.read_bytes() == original and await competing.close(1)
        active = InstanceLock(files.homes(first)[1])
        active.acquire()
        active.close()
        with pytest.raises(BusinessError) as caught:
            InstanceLock(files.homes(second)[1]).acquire()
        assert caught.value.code == "release_fenced"

    asyncio.run(scenario())
