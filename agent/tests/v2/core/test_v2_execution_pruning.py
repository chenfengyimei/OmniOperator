"""Reviewed pruning rechecks provenance, preserves ledgers and restores original bodies from verified snapshots."""

from omni_test_support.v2.execution_pruning import (
    ActionResult as ActionResult,
)
from omni_test_support.v2.execution_pruning import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.execution_pruning import (
    Clock as Clock,
)
from omni_test_support.v2.execution_pruning import (
    InstanceLock as InstanceLock,
)
from omni_test_support.v2.execution_pruning import (
    InstancePaths as InstancePaths,
)
from omni_test_support.v2.execution_pruning import (
    LegacySeedRepositories as LegacySeedRepositories,
)
from omni_test_support.v2.execution_pruning import (
    ModelReply as ModelReply,
)
from omni_test_support.v2.execution_pruning import (
    Outcome as Outcome,
)
from omni_test_support.v2.execution_pruning import (
    RepositoryBase as RepositoryBase,
)
from omni_test_support.v2.execution_pruning import (
    SystemClock as SystemClock,
)
from omni_test_support.v2.execution_pruning import (
    TestClient as TestClient,
)
from omni_test_support.v2.execution_pruning import (
    UUIDs as UUIDs,
)
from omni_test_support.v2.execution_pruning import (
    add_model_headers_column as add_model_headers_column,
)
from omni_test_support.v2.execution_pruning import (
    asyncio as asyncio,
)
from omni_test_support.v2.execution_pruning import (
    body_fingerprint as body_fingerprint,
)
from omni_test_support.v2.execution_pruning import (
    configuration as configuration,
)
from omni_test_support.v2.execution_pruning import (
    copy as copy,
)
from omni_test_support.v2.execution_pruning import (
    create_engine as create_engine,
)
from omni_test_support.v2.execution_pruning import (
    dt as dt,
)
from omni_test_support.v2.execution_pruning import (
    fingerprint as fingerprint,
)
from omni_test_support.v2.execution_pruning import (
    http_app as http_app,
)
from omni_test_support.v2.execution_pruning import (
    inspect_database as inspect_database,
)
from omni_test_support.v2.execution_pruning import (
    json as json,
)
from omni_test_support.v2.execution_pruning import (
    migration_head as migration_head,
)
from omni_test_support.v2.execution_pruning import (
    offline_plans as offline_plans,
)
from omni_test_support.v2.execution_pruning import (
    offline_read as offline_read,
)
from omni_test_support.v2.execution_pruning import (
    opened as opened,
)
from omni_test_support.v2.execution_pruning import (
    prepare as prepare,
)
from omni_test_support.v2.execution_pruning import (
    pruning as pruning,
)
from omni_test_support.v2.execution_pruning import (
    pytest as pytest,
)
from omni_test_support.v2.execution_pruning import (
    read as read,
)
from omni_test_support.v2.execution_pruning import (
    read_action as read_action,
)
from omni_test_support.v2.execution_pruning import (
    repository_base as repository_base,
)
from omni_test_support.v2.execution_pruning import (
    restore as restore,
)
from omni_test_support.v2.execution_pruning import (
    s as s,
)
from omni_test_support.v2.execution_pruning import (
    seed as seed,
)
from omni_test_support.v2.execution_pruning import (
    select as select,
)
from omni_test_support.v2.execution_pruning import (
    sqlite_connection as sqlite_connection,
)
from omni_test_support.v2.execution_pruning import (
    subprocess as subprocess,
)
from omni_test_support.v2.execution_pruning import (
    sys as sys,
)
from omni_test_support.v2.execution_pruning import (
    update as update,
)
from omni_test_support.v2.execution_pruning import (
    validate_retention_plan as validate_retention_plan,
)


def test_prune_preserves_messages_ledger_objects_ids_and_restores_originals(tmp_path):
    paths, plan, agent, turn, action, _blob = prepare(tmp_path)

    def snapshot(u):
        return {
            "action": u.action(agent, action["id"]),
            "messages": u.messages(agent, turn["session_id"]),
            "events": [
                dict(r) for r in u.connection.execute(select(s.run_events).order_by(s.run_events.c.sequence)).mappings()
            ],
            "model_calls": [dict(r) for r in u.connection.execute(select(s.model_calls)).mappings()],
            "budget": [dict(r) for r in u.connection.execute(select(s.budget_accounts)).mappings()],
            "objects": [dict(r) for r in u.connection.execute(select(s.action_objects)).mappings()],
        }

    before = read(paths, snapshot)
    backup = tmp_path / "snapshot"
    result = pruning.apply_pruning(paths, plan, backup)
    assert result["complete"] and result["backup_sha256"] == fingerprint(backup / "manifest.json")
    assert pruning.apply_pruning(paths, plan, backup) == result
    after = read(paths, snapshot)
    for key in ("messages", "model_calls", "budget", "objects"):
        assert before[key] == after[key]
    assert after["action"]["arguments"] == {} and after["action"]["result"]["body_pruned"]
    assert after["action"]["result_sha256"] == body_fingerprint([{"result": before["action"]["result"]}])[0]
    assert after["action"]["body_pruned_at"]
    for key in ("id", "agent_id", "run_id", "state", "effect", "arguments_sha256", "policy_revision"):
        assert before["action"][key] == after["action"][key]
    for old, new in zip(before["events"], after["events"], strict=True):
        for key in ("sequence", "seq", "kind", "created_at", "run_id", "agent_id"):
            assert old[key] == new[key]
        assert new["data"]["body_pruned"] and new["data_sha256"] == body_fingerprint([{"data": old["data"]}])[0]
    assert read(paths, lambda u: {"items": u.retention_preview()["items"]})["items"][0]["protected_reasons"] == [
        "body_already_pruned"
    ]
    audits = read(paths, lambda u: {"rows": u.query("audit", agent)})["rows"]
    assert sum(r["kind"] == "retention.pruned" for r in audits) == 1
    assert inspect_database(paths.database)["execution_retention_attention"] == 0
    restored = tmp_path / "restored"
    report = restore(backup, restored)
    assert report["database"]["integrity"] == "ok" and not report["credentials_restored"]
    recovered = read(InstancePaths.resolve(restored), snapshot)
    assert recovered == before
    with TestClient(http_app(paths.home)) as client:
        client.headers["Authorization"] = "Bearer " + (paths.control / "owner.token").read_text().strip()
        response = client.get(f"/api/v1/agents/{agent}/runs/{turn['run_id']}/retention")
        assert response.status_code == 200 and response.json()["body_pruned_plan_id"] == plan["id"]
        response = client.put(
            f"/api/v1/agents/{agent}/runs/{turn['run_id']}/retention-pin", json={"pinned": True, "revision": 1}
        )
        assert response.status_code == 409 and response.json()["error"]["code"] == "body_already_pruned"


@pytest.mark.parametrize("change", ["memory", "cost", "lease", "object_pin", "approval", "body", "pin_cycle", "legacy"])
def test_apply_rechecks_protections_and_fingerprint_before_any_backup_or_prune(tmp_path, change):
    paths, plan, agent, turn, action, blob = prepare(tmp_path)

    async def alter():
        async with opened(paths.home, Clock()) as (_, db, _, _):
            if change == "memory":
                await db.write(
                    lambda u: u.remember(agent, "sourced fact", action_id=action["id"], quote="retention-result")
                )
            elif change == "cost":
                call = await db.write(lambda u: u.reserve_call(agent, turn["run_id"], 100, 1000, "fixture"))
                await db.write(lambda u: u.settle_call(agent, call, None))
            elif change == "lease":
                await db.write(lambda u: u.lease_acquire(agent, "prune-resource", turn["run_id"]))
            elif change == "object_pin":
                await db.write(
                    lambda u: u.connection.execute(
                        update(s.objects).where(s.objects.c.id == blob["id"]).values(pinned=True)
                    )
                )
            elif change == "approval":
                await db.write(
                    lambda u: u._record(
                        s.approvals,
                        {"action_id": action["id"], "state": "pending", "expires_at": "2099-01-01T00:00:00+00:00"},
                        agent,
                    )
                )
            elif change == "body":
                await db.write(
                    lambda u: u.connection.execute(
                        update(s.actions).where(s.actions.c.id == action["id"]).values(result={"changed": True})
                    )
                )
            elif change == "pin_cycle":
                await db.write(lambda u: u.retention_pin(agent, turn["run_id"], True, 1))
                await db.write(lambda u: u.retention_pin(agent, turn["run_id"], False, 2))
            else:
                await db.write(
                    lambda u: u.connection.execute(
                        update(s.runs)
                        .where(s.runs.c.id == turn["run_id"])
                        .values(error_code="legacy_configuration_unresolved")
                    )
                )

    asyncio.run(alter())
    if change in {"memory", "cost", "lease", "object_pin", "approval"}:
        assert (
            read(paths, lambda u: u.retention_inspect(agent, turn["run_id"]))["fingerprint"]
            == plan["targets"][0]["fingerprint"]
        )
    with pytest.raises(BusinessError) as failure:
        pruning.apply_pruning(paths, plan, tmp_path / "snapshot")
    assert failure.value.code == "retention_conflict"
    assert not (tmp_path / "snapshot").exists()
    assert not read(paths, lambda u: u.action(agent, action["id"]))["body_pruned_at"]


def test_audit_failure_rolls_back_entire_target_then_same_plan_resumes(tmp_path, monkeypatch):
    paths, plan, agent, _turn, action, _ = prepare(tmp_path)
    original = RepositoryBase.audit

    def fail(self, agent_id, kind, *args):
        if kind == "retention.pruned":
            raise BusinessError("audit_unavailable", "Injected")
        return original(self, agent_id, kind, *args)

    with monkeypatch.context() as patch:
        patch.setattr(RepositoryBase, "audit", fail)
        with pytest.raises(BusinessError, match="Injected"):
            pruning.apply_pruning(paths, plan, tmp_path / "snapshot")
    assert not read(paths, lambda u: u.action(agent, action["id"]))["body_pruned_at"]
    assert inspect_database(paths.database)["execution_retention_attention"] == 1
    assert pruning.apply_pruning(paths, plan, tmp_path / "snapshot")["complete"]
    assert inspect_database(paths.database)["execution_retention_attention"] == 0


def test_failed_snapshot_never_prepares_batch_or_changes_body(tmp_path, monkeypatch):
    paths, plan, agent, _, action, _ = prepare(tmp_path)

    def fail(*args):
        raise BusinessError("backup_io_failed", "Injected")

    monkeypatch.setattr(offline_plans, "create_snapshot", fail)
    with pytest.raises(BusinessError) as failure:
        pruning.apply_pruning(paths, plan, tmp_path / "snapshot")
    assert failure.value.code == "backup_io_failed"
    assert read(paths, lambda u: u.retention_batch(plan["id"]) or {}) == {}
    assert read(paths, lambda u: u.action(agent, action["id"]))["result"]


def test_bound_backup_cannot_be_replaced_or_tampered_on_resume(tmp_path, monkeypatch):
    paths, plan, agent, _, action, _ = prepare(tmp_path)
    original = RepositoryBase.audit

    def fail(self, agent_id, kind, *args):
        if kind == "retention.pruned":
            raise BusinessError("stop", "Injected")
        return original(self, agent_id, kind, *args)

    backup = tmp_path / "snapshot"
    with monkeypatch.context() as patch:
        patch.setattr(RepositoryBase, "audit", fail)
        with pytest.raises(BusinessError):
            pruning.apply_pruning(paths, plan, backup)
    with pytest.raises(BusinessError) as failure:
        pruning.apply_pruning(paths, plan, tmp_path / "different")
    assert failure.value.code == "retention_backup_changed"
    manifest = json.loads((backup / "manifest.json").read_text())
    manifest["created_at"] = "changed"
    (backup / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(BusinessError) as failure:
        pruning.apply_pruning(paths, plan, backup)
    assert failure.value.code == "retention_backup_changed"
    assert not read(paths, lambda u: u.action(agent, action["id"]))["body_pruned_at"]


@pytest.mark.parametrize(
    "change", ["instance", "schema", "duplicate", "preview", "future_cutoff", "revision_bool", "extra", "bad_sha"]
)
def test_invalid_or_wrong_identity_plan_never_changes_data(tmp_path, change):
    paths, plan, agent, _, action, _ = prepare(tmp_path)
    invalid = copy.deepcopy(plan)
    if change == "instance":
        invalid["instance_id"] = UUIDs().new()
    elif change == "schema":
        invalid["schema_version"] = "v2_other"
    elif change == "duplicate":
        invalid["targets"] *= 2
    elif change == "preview":
        invalid["format"] = "omni-execution-retention-preview-1"
    elif change == "future_cutoff":
        invalid["cutoff"] = "2099-01-01T00:00:00+00:00"
    elif change == "revision_bool":
        invalid["targets"][0]["retention_revision"] = True
    elif change == "bad_sha":
        invalid["targets"][0]["fingerprint"] = "G" * 64
    else:
        invalid["can_apply"] = True
    with pytest.raises(BusinessError):
        pruning.apply_pruning(paths, invalid, tmp_path / "snapshot")
    assert not (tmp_path / "snapshot").exists()
    assert not read(paths, lambda u: u.action(agent, action["id"]))["body_pruned_at"]


def test_cli_refuses_live_instance_and_preserves_reviewed_files(tmp_path):
    paths, plan, _, _, _, _ = prepare(tmp_path)
    command = [sys.executable, "-m", "omni.composition.cli", "--home", str(paths.home)]
    lock = InstanceLock(paths)
    lock.acquire()
    try:
        result = subprocess.run(
            [*command, "retention-apply", str(tmp_path / "prune.json"), "--backup", str(tmp_path / "snapshot")],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 1 and json.loads(result.stderr)["error"] == "instance_busy"
    finally:
        lock.close()
    with pytest.raises(BusinessError) as failure:
        pruning.plan_pruning(paths, tmp_path / "prune.json")
    assert failure.value.code == "destination_exists"
    with pytest.raises(BusinessError):
        pruning.plan_pruning(paths, paths.home / "unsafe.json")
    result = subprocess.run(
        [*command, "retention-apply", str(tmp_path / "prune.json"), "--backup", str(tmp_path / "snapshot")],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["complete"]
    assert validate_retention_plan(plan, SystemClock().now())


def test_pending_plan_owns_target_until_explicit_cancellation(tmp_path, monkeypatch):
    paths, plan, agent, _, action, _ = prepare(tmp_path)
    second = copy.deepcopy(plan)
    second["id"] = UUIDs().new()
    original = RepositoryBase.audit

    def fail(self, agent_id, kind, *args):
        if kind == "retention.pruned":
            raise BusinessError("stop", "Injected")
        return original(self, agent_id, kind, *args)

    with monkeypatch.context() as patch:
        patch.setattr(RepositoryBase, "audit", fail)
        with pytest.raises(BusinessError):
            pruning.apply_pruning(paths, plan, tmp_path / "snapshot")
    assert (
        "retention_batch_held"
        in read(paths, lambda u: u.retention_inspect(agent, action["run_id"]))["protected_reasons"]
    )
    with pytest.raises(BusinessError) as failure:
        pruning.apply_pruning(paths, second, tmp_path / "second-snapshot")
    assert failure.value.code == "retention_conflict" and not (tmp_path / "second-snapshot").exists()
    cancelled = pruning.cancel_pruning(paths, plan, "Changed maintenance window")
    assert cancelled["cancelled"] and not cancelled["bodies_restored"]
    assert pruning.cancel_pruning(paths, plan, "Retry") == cancelled
    assert inspect_database(paths.database)["execution_retention_attention"] == 0
    with pytest.raises(BusinessError) as failure:
        pruning.apply_pruning(paths, plan, tmp_path / "snapshot")
    assert failure.value.code == "retention_cancelled"
    assert pruning.apply_pruning(paths, second, tmp_path / "second-snapshot")["complete"]
    assert read(paths, lambda u: u.action(agent, action["id"]))["body_pruned_at"]


def test_partial_batch_cancellation_keeps_committed_bodies_and_releases_pending(tmp_path, monkeypatch):
    paths, _, agent, first, _, _ = prepare(tmp_path)

    async def add():
        clock = Clock()
        clock.current -= dt.timedelta(days=40)
        async with opened(paths.home, clock) as (_, db, _, _):

            def second_run(u):
                turn = u.accept_turn(agent, None, "second", "second independent request", {})
                u.transition(agent, turn["run_id"], "running")
                u.prepare_context(agent, turn["run_id"])
                _, action = read_action(u, agent, "files_read", turn=turn)
                u.settle_action(agent, action["id"], ActionResult(Outcome.OK, "second result"))
                u.finish(agent, turn["run_id"], "second conversation")
                return turn

            return await db.write(second_run)

    second = asyncio.run(add())
    pruning.plan_pruning(paths, tmp_path / "two.json")
    plan = pruning.read_pruning_plan(tmp_path / "two.json")
    assert {t["run_id"] for t in plan["targets"]} == {first["run_id"], second["run_id"]}
    original = RepositoryBase.audit
    calls = 0

    def fail(self, agent_id, kind, *args):
        nonlocal calls
        if kind == "retention.pruned":
            calls += 1
            if calls == 2:
                raise BusinessError("stop", "Injected second target failure")
        return original(self, agent_id, kind, *args)

    with monkeypatch.context() as patch:
        patch.setattr(RepositoryBase, "audit", fail)
        with pytest.raises(BusinessError):
            pruning.apply_pruning(paths, plan, tmp_path / "snapshot")
    first_id, pending_id = [t["run_id"] for t in plan["targets"]]
    assert read(paths, lambda u: u.retention_inspect(agent, first_id))["body_pruned_at"]
    assert not read(paths, lambda u: u.retention_inspect(agent, pending_id))["body_pruned_at"]
    assert pruning.cancel_pruning(paths, plan, "Keep remaining evidence")["cancelled"]
    assert read(paths, lambda u: u.retention_inspect(agent, first_id))["body_pruned_at"]
    assert read(paths, lambda u: u.retention_inspect(agent, pending_id))["eligible"]
    assert inspect_database(paths.database)["execution_retention_attention"] == 0


def test_snapshot_completed_before_batch_crash_requires_fresh_destination(tmp_path, monkeypatch):
    paths, plan, agent, _, action, _ = prepare(tmp_path)
    original = offline_plans.create_snapshot
    backup = tmp_path / "snapshot"

    def crash(*args):
        original(*args)
        raise BusinessError("crash_after_backup", "Injected before batch persistence")

    with monkeypatch.context() as patch:
        patch.setattr(offline_plans, "create_snapshot", crash)
        with pytest.raises(BusinessError) as failure:
            pruning.apply_pruning(paths, plan, backup)
        assert failure.value.code == "crash_after_backup"
    assert read(paths, lambda u: u.retention_batch(plan["id"]) or {}) == {}
    assert not read(paths, lambda u: u.action(agent, action["id"]))["body_pruned_at"]
    with pytest.raises(BusinessError) as failure:
        pruning.apply_pruning(paths, plan, backup)
    assert failure.value.code == "destination_exists"
    assert pruning.apply_pruning(paths, plan, tmp_path / "fresh-snapshot")["complete"]


def test_plan_publication_does_not_overwrite_competing_file(tmp_path, monkeypatch):
    paths, _, _, _, _, _ = prepare(tmp_path)
    output = tmp_path / "racing.json"
    original = offline_plans.offline_read

    def race(*args):
        result = original(*args)
        output.write_bytes(b"another owner's file")
        return result

    monkeypatch.setattr(offline_plans, "offline_read", race)
    with pytest.raises(OSError):
        pruning.plan_pruning(paths, output)
    assert output.read_bytes() == b"another owner's file"
    assert not list(tmp_path.glob("*.partial"))


def test_clock_rollback_does_not_block_cancellation_of_bound_plan(tmp_path, monkeypatch):
    paths, plan, _, _, _, _ = prepare(tmp_path)
    original = RepositoryBase.audit

    def fail(self, agent_id, kind, *args):
        if kind == "retention.pruned":
            raise BusinessError("stop", "Injected")
        return original(self, agent_id, kind, *args)

    with monkeypatch.context() as patch:
        patch.setattr(RepositoryBase, "audit", fail)
        with pytest.raises(BusinessError):
            pruning.apply_pruning(paths, plan, tmp_path / "snapshot")
    clock = Clock()
    clock.current -= dt.timedelta(days=1)
    monkeypatch.setattr(offline_plans, "SystemClock", lambda: clock)
    with pytest.raises(BusinessError) as failure:
        pruning.read_pruning_plan(tmp_path / "prune.json")
    assert failure.value.code == "invalid_retention_plan"
    original_plan = pruning.read_pruning_plan(tmp_path / "prune.json", allow_future=True)
    assert pruning.cancel_pruning(paths, original_plan, "Clock rolled back")["cancelled"]
    assert inspect_database(paths.database)["execution_retention_attention"] == 0


@pytest.mark.parametrize("interrupted", [False, True])
def test_pruning_schema_upgrade_backup_and_interrupted_ddl_rollback(tmp_path, interrupted):
    from alembic import command

    paths = InstancePaths.resolve(tmp_path / "instance")
    paths.initialize()
    engine = create_engine("sqlite+pysqlite:///" + str(paths.database))
    clock, ids = SystemClock(), UUIDs()
    cfg = configuration()
    with engine.begin() as connection, pytest.MonkeyPatch.context() as legacy_history:
        # Pre-v14 audit rows used SQLite's INTEGER PRIMARY KEY allocation.
        legacy_history.setattr(repository_base, "allocate_history", lambda *args, **kwargs: None)
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "v2_0011")
        add_model_headers_column(connection)
        u = LegacySeedRepositories(connection, clock, ids)
        u.initialize()
        agent = u.query("agents", "")[0]["id"]
    engine.dispose()
    if interrupted:
        code = """import os,sys
from pathlib import Path
from sqlalchemy import Engine,event
from omni.adapters.persistence.database import Database
from omni.adapters.primitives import SystemClock,UUIDs
@event.listens_for(Engine,'after_cursor_execute')
def cut(connection,cursor,statement,parameters,context,executemany):
    if statement.startswith('ALTER TABLE actions ADD COLUMN result_sha256'): os._exit(94)
Database(Path(sys.argv[1]),SystemClock(),UUIDs(),objects_root=Path(sys.argv[2]))
"""
        result = subprocess.run(
            [sys.executable, "-c", code, str(paths.database), str(paths.objects)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 94, result.stderr
        with sqlite_connection(paths.database) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "v2_0011"
            assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name='retention_batches'").fetchone()
            assert "body_pruned_at" not in {r[1] for r in connection.execute("PRAGMA table_info(runs)")}
            assert connection.execute("SELECT id FROM agents").fetchone()[0] == agent

    async def upgrade():
        async with opened(paths.home, Clock()) as (_, db, _, _):
            assert db.schema_version == migration_head()
            assert (await db.integrity())["foreign_keys"] == 0
            assert (await db.read(lambda u: u.query("agents", "")))[0]["id"] == agent

    asyncio.run(upgrade())
    manifests = list(paths.database.parent.glob("schema-backups/*/manifest.json"))
    assert len(manifests) == (2 if interrupted else 1)
    assert all(json.loads(p.read_text())["database"]["schema"] == "v2_0011" for p in manifests)
