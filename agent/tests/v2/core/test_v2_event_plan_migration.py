"""Real v16 DDL recovery and immutable event-retention evidence; isolated databases and no external actions."""

from __future__ import annotations

from omni_test_support.v2.event_plan_migration import (
    NEW_INDEXES as NEW_INDEXES,
)
from omni_test_support.v2.event_plan_migration import (
    NEW_TABLES as NEW_TABLES,
)
from omni_test_support.v2.event_plan_migration import (
    NEW_TRIGGERS as NEW_TRIGGERS,
)
from omni_test_support.v2.event_plan_migration import (
    SOURCE_TABLES as SOURCE_TABLES,
)
from omni_test_support.v2.event_plan_migration import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.event_plan_migration import (
    CheckConstraint as CheckConstraint,
)
from omni_test_support.v2.event_plan_migration import (
    Database as Database,
)
from omni_test_support.v2.event_plan_migration import (
    InstancePaths as InstancePaths,
)
from omni_test_support.v2.event_plan_migration import (
    LegacySeedRepositories as LegacySeedRepositories,
)
from omni_test_support.v2.event_plan_migration import (
    SystemClock as SystemClock,
)
from omni_test_support.v2.event_plan_migration import (
    UniqueConstraint as UniqueConstraint,
)
from omni_test_support.v2.event_plan_migration import (
    UUIDs as UUIDs,
)
from omni_test_support.v2.event_plan_migration import (
    add_model_headers_column as add_model_headers_column,
)
from omni_test_support.v2.event_plan_migration import (
    assert_schema as assert_schema,
)
from omni_test_support.v2.event_plan_migration import (
    asyncio as asyncio,
)
from omni_test_support.v2.event_plan_migration import (
    baseline as baseline,
)
from omni_test_support.v2.event_plan_migration import (
    chain as chain,
)
from omni_test_support.v2.event_plan_migration import (
    command as command,
)
from omni_test_support.v2.event_plan_migration import (
    configuration as configuration,
)
from omni_test_support.v2.event_plan_migration import (
    create_engine as create_engine,
)
from omni_test_support.v2.event_plan_migration import (
    delete as delete,
)
from omni_test_support.v2.event_plan_migration import (
    evidence_rows as evidence_rows,
)
from omni_test_support.v2.event_plan_migration import (
    insert as insert,
)
from omni_test_support.v2.event_plan_migration import (
    inspect as inspect,
)
from omni_test_support.v2.event_plan_migration import (
    make_plan as make_plan,
)
from omni_test_support.v2.event_plan_migration import (
    migration_head as migration_head,
)
from omni_test_support.v2.event_plan_migration import (
    normalized_check as normalized_check,
)
from omni_test_support.v2.event_plan_migration import (
    owned as owned,
)
from omni_test_support.v2.event_plan_migration import (
    preserved_rows as preserved_rows,
)
from omni_test_support.v2.event_plan_migration import (
    pytest as pytest,
)
from omni_test_support.v2.event_plan_migration import (
    s as s,
)
from omni_test_support.v2.event_plan_migration import (
    select as select,
)
from omni_test_support.v2.event_plan_migration import (
    source_rows as source_rows,
)
from omni_test_support.v2.event_plan_migration import (
    sqlite_connection as sqlite_connection,
)
from omni_test_support.v2.event_plan_migration import (
    subprocess as subprocess,
)
from omni_test_support.v2.event_plan_migration import (
    sys as sys,
)
from omni_test_support.v2.event_plan_migration import (
    update as update,
)
from omni_test_support.v2.event_plan_migration import (
    validate_event_plan as validate_event_plan,
)
from omni_test_support.v2.event_plan_migration import (
    verify_snapshot as verify_snapshot,
)


@pytest.mark.parametrize(
    "marker",
    [
        "CREATE TABLE event_retention_targets",
        "CREATE UNIQUE INDEX ix_event_retention_pending_sequence",
        "CREATE TRIGGER event_retention_target_keep",
    ],
    ids=["table", "index", "trigger"],
)
def test_v16_process_exit_rolls_back_all_ddl_then_retries_from_verified_v15_snapshot(tmp_path, marker):
    paths = InstancePaths.resolve(tmp_path / "instance")
    original, original_schema = baseline(paths)
    script = tmp_path / "interrupt_v16.py"
    script.write_text(
        """import os,sys
from pathlib import Path
from sqlalchemy import Engine,event
from omni.adapters.persistence.database import Database
from omni.adapters.platform.runtime.paths import InstanceLock,InstancePaths
from omni.adapters.primitives import SystemClock,UUIDs

paths=InstancePaths.resolve(Path(sys.argv[1]))
lock=InstanceLock(paths)
lock.acquire()
@event.listens_for(Engine,'after_cursor_execute')
def cut(connection,cursor,statement,parameters,context,executemany):
    if statement.strip().startswith(sys.argv[2]):
        os._exit(96)
Database(paths.database,SystemClock(),UUIDs(),objects_root=paths.objects)
raise AssertionError('Requested v16 migration interruption did not occur')
""",
        encoding="utf-8",
    )
    process = subprocess.run(
        [sys.executable, str(script), str(paths.home), marker],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert process.returncode == 96, process.stderr
    with owned(paths):
        with sqlite_connection(paths.database) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "v2_0015"
            assert (
                connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
                == original_schema
            )
            assert source_rows(connection) == original
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert not connection.execute("PRAGMA foreign_key_check").fetchall()

        async def retry():
            database = Database(paths.database, SystemClock(), UUIDs(), objects_root=paths.objects)
            try:
                assert database.schema_version == migration_head()
                await database.read(assert_schema)
                report = await database.integrity()
                assert report["integrity"] == "ok" and report["foreign_keys"] == 0
            finally:
                await database.close()

        asyncio.run(retry())
        with sqlite_connection(paths.database) as connection:
            assert source_rows(connection) == original
    snapshots = list(paths.database.parent.glob("schema-backups/*"))
    assert len(snapshots) == 2
    for snapshot in snapshots:
        manifest = verify_snapshot(snapshot)
        assert manifest["complete"] and manifest["database"]["schema"] == "v2_0015"
        assert manifest["database"]["integrity"] == "ok" and not manifest["database"]["foreign_key_errors"]


def test_fresh_v16_database_has_78_declared_tables_and_empty_event_retention_evidence(tmp_path):
    paths = InstancePaths.resolve(tmp_path / "instance")
    paths.initialize()
    with owned(paths):

        async def scenario():
            database = Database(paths.database, SystemClock(), UUIDs(), objects_root=paths.objects)
            try:
                assert database.schema_version == migration_head()
                await database.read(assert_schema)
                for table in (s.event_retention_batches, s.event_retention_targets):
                    assert await database.read(lambda u: u.connection.execute(select(table)).all()) == []
                await database.write(lambda u: u.initialize())
                report = await database.integrity()
                assert report["integrity"] == "ok" and report["foreign_keys"] == 0
                assert not (paths.database.parent / "schema-backups").exists()
            finally:
                await database.close()

        asyncio.run(scenario())


def test_v16_identity_keep_kind_and_unique_constraints_and_absence_of_live_event_fk(tmp_path):
    paths = InstancePaths.resolve(tmp_path / "instance")
    paths.initialize()
    with owned(paths):

        async def scenario():
            database = Database(paths.database, SystemClock(), UUIDs(), objects_root=paths.objects)
            try:
                batch, target, run = await database.write(evidence_rows)
                batch_where = s.event_retention_batches.c.id == batch["id"]
                target_where = (s.event_retention_targets.c.batch_id == batch["id"]) & (
                    s.event_retention_targets.c.sequence == target["sequence"]
                )
                ended = batch["created_at"]

                async def denied(statement):
                    with pytest.raises(BusinessError) as failure:
                        await database.write(lambda u: u.connection.execute(statement))
                    assert failure.value.code == "data_constraint"

                for change in ({"plan_sha256": "f" * 64}, {"backup_path": "fixture/changed"}, {"trim_revision": 1}):
                    await denied(
                        update(s.event_retention_batches)
                        .where(batch_where)
                        .values(state="complete", completed_at=ended, **change)
                    )
                for change in ({"fingerprint": "f" * 64}, {"seq": target["seq"] + 1}, {"kind": "run.failed"}):
                    await denied(
                        update(s.event_retention_targets)
                        .where(target_where)
                        .values(state="deleted", ended_at=ended, **change)
                    )
                await denied(delete(s.event_retention_targets).where(target_where))
                assert (
                    await database.read(
                        lambda u: dict(
                            u.connection.execute(select(s.event_retention_batches).where(batch_where)).mappings().one()
                        )
                    )
                    == batch
                )
                assert (
                    await database.read(
                        lambda u: dict(
                            u.connection.execute(select(s.event_retention_targets).where(target_where)).mappings().one()
                        )
                    )
                    == target
                )
                duplicate_local = {
                    **target,
                    "sequence": target["sequence"] + 100,
                    "state": "cancelled",
                    "ended_at": ended,
                }
                await denied(insert(s.event_retention_targets).values(**duplicate_local))
                forbidden_kind = {**duplicate_local, "seq": target["seq"] + 1, "kind": "run.interrupted"}
                await denied(insert(s.event_retention_targets).values(**forbidden_kind))

                def other_agent(unit):
                    other = unit.create_agent("Other event evidence Agent")["id"]
                    other_run = unit.accept_turn(other, None, "other-event", "Independent event evidence", {})
                    other_batch = {**batch, "id": UUIDs().new(), "agent_id": other}
                    unit.connection.execute(insert(s.event_retention_batches).values(**other_batch))
                    return other_batch, other_run

                other_batch, other_run = await database.write(other_agent)
                duplicate_global = {
                    **target,
                    "batch_id": other_batch["id"],
                    "agent_id": other_batch["agent_id"],
                    "run_id": other_run["run_id"],
                }
                await denied(insert(s.event_retention_targets).values(**duplicate_global))
                historical = {**duplicate_global, "state": "cancelled", "ended_at": ended}
                await database.write(
                    lambda u: u.connection.execute(insert(s.event_retention_targets).values(**historical))
                )
                orphan = {**batch, "id": UUIDs().new(), "state": "cancelled", "completed_at": ended}
                await database.write(lambda u: u.connection.execute(insert(s.event_retention_batches).values(**orphan)))
                await denied(delete(s.event_retention_batches).where(s.event_retention_batches.c.id == orphan["id"]))
                await database.write(
                    lambda u: u.connection.execute(
                        delete(s.run_events).where(s.run_events.c.sequence == target["sequence"])
                    )
                )
                assert not await database.read(
                    lambda u: u.connection.execute(
                        select(s.run_events.c.sequence).where(s.run_events.c.sequence == target["sequence"])
                    ).first()
                )
                assert (
                    await database.read(
                        lambda u: dict(
                            u.connection.execute(select(s.event_retention_targets).where(target_where)).mappings().one()
                        )
                    )
                    == target
                )
                await database.write(
                    lambda u: u.connection.execute(
                        update(s.event_retention_targets).where(target_where).values(state="deleted", ended_at=ended)
                    )
                )
                await denied(
                    update(s.event_retention_targets).where(target_where).values(state="pending", ended_at=None)
                )
                await database.write(
                    lambda u: u.connection.execute(
                        update(s.event_retention_batches)
                        .where(batch_where)
                        .values(state="complete", completed_at=ended)
                    )
                )
                await denied(
                    update(s.event_retention_batches).where(batch_where).values(state="prepared", completed_at=None)
                )
                persisted_run = await database.read(lambda u: u.get_run(batch["agent_id"], run["run_id"]))
                # run.queued plus the routing.selected event this fixture's default route emits.
                assert persisted_run["state"] == "queued" and persisted_run["last_seq"] == 2
                # Two runs each emitted run.queued plus routing.selected; deleting one event row and
                # every denied retention write must leave the allocator untouched.
                assert (
                    await database.read(
                        lambda u: u.connection.execute(
                            select(s.history_sequences.c.last_issued).where(s.history_sequences.c.kind == "events")
                        ).scalar_one()
                    )
                ) == 4
                messages = await database.read(lambda u: u.messages(batch["agent_id"], run["session_id"]))
                assert [row["content"] for row in messages] == ["Preserved event evidence source"]
                report = await database.integrity()
                assert report["integrity"] == "ok" and report["foreign_keys"] == 0
            finally:
                await database.close()

        asyncio.run(scenario())
