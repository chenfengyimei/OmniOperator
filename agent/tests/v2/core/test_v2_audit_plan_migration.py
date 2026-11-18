"""Real v15 DDL crash recovery and immutable maintenance evidence; isolated databases, no external actions."""

from __future__ import annotations

from omni_test_support.v2.audit_plan_migration import (
    NEW_INDEXES as NEW_INDEXES,
)
from omni_test_support.v2.audit_plan_migration import (
    NEW_TABLES as NEW_TABLES,
)
from omni_test_support.v2.audit_plan_migration import (
    NEW_TRIGGERS as NEW_TRIGGERS,
)
from omni_test_support.v2.audit_plan_migration import (
    SOURCE_TABLES as SOURCE_TABLES,
)
from omni_test_support.v2.audit_plan_migration import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.audit_plan_migration import (
    Database as Database,
)
from omni_test_support.v2.audit_plan_migration import (
    InstanceLock as InstanceLock,
)
from omni_test_support.v2.audit_plan_migration import (
    InstancePaths as InstancePaths,
)
from omni_test_support.v2.audit_plan_migration import (
    LegacySeedRepositories as LegacySeedRepositories,
)
from omni_test_support.v2.audit_plan_migration import (
    SystemClock as SystemClock,
)
from omni_test_support.v2.audit_plan_migration import (
    UUIDs as UUIDs,
)
from omni_test_support.v2.audit_plan_migration import (
    add_model_headers_column as add_model_headers_column,
)
from omni_test_support.v2.audit_plan_migration import (
    assert_schema as assert_schema,
)
from omni_test_support.v2.audit_plan_migration import (
    asyncio as asyncio,
)
from omni_test_support.v2.audit_plan_migration import (
    baseline as baseline,
)
from omni_test_support.v2.audit_plan_migration import (
    command as command,
)
from omni_test_support.v2.audit_plan_migration import (
    configuration as configuration,
)
from omni_test_support.v2.audit_plan_migration import (
    contextmanager as contextmanager,
)
from omni_test_support.v2.audit_plan_migration import (
    create_engine as create_engine,
)
from omni_test_support.v2.audit_plan_migration import (
    delete as delete,
)
from omni_test_support.v2.audit_plan_migration import (
    evidence_rows as evidence_rows,
)
from omni_test_support.v2.audit_plan_migration import (
    insert as insert,
)
from omni_test_support.v2.audit_plan_migration import (
    inspect as inspect,
)
from omni_test_support.v2.audit_plan_migration import (
    make_plan as make_plan,
)
from omni_test_support.v2.audit_plan_migration import (
    migration_head as migration_head,
)
from omni_test_support.v2.audit_plan_migration import (
    owned as owned,
)
from omni_test_support.v2.audit_plan_migration import (
    preserved_rows as preserved_rows,
)
from omni_test_support.v2.audit_plan_migration import (
    pytest as pytest,
)
from omni_test_support.v2.audit_plan_migration import (
    s as s,
)
from omni_test_support.v2.audit_plan_migration import (
    select as select,
)
from omni_test_support.v2.audit_plan_migration import (
    source_rows as source_rows,
)
from omni_test_support.v2.audit_plan_migration import (
    sqlite_connection as sqlite_connection,
)
from omni_test_support.v2.audit_plan_migration import (
    subprocess as subprocess,
)
from omni_test_support.v2.audit_plan_migration import (
    sys as sys,
)
from omni_test_support.v2.audit_plan_migration import (
    update as update,
)
from omni_test_support.v2.audit_plan_migration import (
    validate_audit_plan as validate_audit_plan,
)
from omni_test_support.v2.audit_plan_migration import (
    verify_snapshot as verify_snapshot,
)


@pytest.mark.parametrize(
    "marker",
    [
        "CREATE TABLE audit_retention_targets",
        "CREATE UNIQUE INDEX ix_audit_retention_pending_audit",
        "CREATE TRIGGER audit_retention_target_keep",
    ],
    ids=["table", "index", "trigger"],
)
def test_v15_process_exit_rolls_back_all_ddl_then_retries_from_verified_v14_snapshot(tmp_path, marker):
    paths = InstancePaths.resolve(tmp_path / "instance")
    original, original_schema = baseline(paths)
    script = tmp_path / "interrupt_v15.py"
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
        os._exit(95)
Database(paths.database,SystemClock(),UUIDs(),objects_root=paths.objects)
raise AssertionError('Requested v15 migration interruption did not occur')
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
    assert process.returncode == 95, process.stderr
    with owned(paths):
        with sqlite_connection(paths.database) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "v2_0014"
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
        assert manifest["complete"] and manifest["database"]["schema"] == "v2_0014"
        assert manifest["database"]["integrity"] == "ok" and not manifest["database"]["foreign_key_errors"]


def test_fresh_v15_database_has_76_declared_tables_and_empty_maintenance_evidence(tmp_path):
    paths = InstancePaths.resolve(tmp_path / "instance")
    paths.initialize()
    with owned(paths):

        async def scenario():
            database = Database(paths.database, SystemClock(), UUIDs(), objects_root=paths.objects)
            try:
                assert database.schema_version == migration_head()
                await database.read(assert_schema)
                for table in (s.audit_retention_batches, s.audit_retention_targets):
                    assert await database.read(lambda u: u.connection.execute(select(table)).all()) == []
                await database.write(lambda u: u.initialize())
                report = await database.integrity()
                assert report["integrity"] == "ok" and report["foreign_keys"] == 0
                assert not (paths.database.parent / "schema-backups").exists()
            finally:
                await database.close()

        asyncio.run(scenario())


def test_v15_triggers_preserve_evidence_identity_and_a_deleted_audit_has_no_live_fk(tmp_path):
    paths = InstancePaths.resolve(tmp_path / "instance")
    paths.initialize()
    with owned(paths):

        async def scenario():
            database = Database(paths.database, SystemClock(), UUIDs(), objects_root=paths.objects)
            try:
                batch, target, run = await database.write(evidence_rows)
                batch_where = s.audit_retention_batches.c.id == batch["id"]
                target_where = s.audit_retention_targets.c.audit_id == target["audit_id"]
                ended = batch["created_at"]

                async def denied(statement):
                    with pytest.raises(BusinessError) as failure:
                        await database.write(lambda u: u.connection.execute(statement))
                    assert failure.value.code == "data_constraint"

                # Also request a legal terminal state, so identity checks cannot hide behind transition rejection.
                for change in ({"plan_sha256": "f" * 64}, {"backup_path": "fixture/changed"}, {"trim_revision": 1}):
                    await denied(
                        update(s.audit_retention_batches)
                        .where(batch_where)
                        .values(state="complete", completed_at=ended, **change)
                    )
                for change in ({"fingerprint": "f" * 64}, {"sequence": target["sequence"] + 1}):
                    await denied(
                        update(s.audit_retention_targets)
                        .where(target_where)
                        .values(state="deleted", ended_at=ended, **change)
                    )
                await denied(delete(s.audit_retention_targets).where(target_where))
                assert (
                    await database.read(
                        lambda u: dict(
                            u.connection.execute(select(s.audit_retention_batches).where(batch_where)).mappings().one()
                        )
                    )
                    == batch
                )
                assert (
                    await database.read(
                        lambda u: dict(
                            u.connection.execute(select(s.audit_retention_targets).where(target_where)).mappings().one()
                        )
                    )
                    == target
                )

                # A separate targetless terminal batch isolates its keep trigger from all FK constraints.
                orphan = {**batch, "id": UUIDs().new(), "state": "cancelled", "completed_at": ended}
                await database.write(lambda u: u.connection.execute(insert(s.audit_retention_batches).values(**orphan)))
                await denied(delete(s.audit_retention_batches).where(s.audit_retention_batches.c.id == orphan["id"]))
                await database.write(
                    lambda u: u.connection.execute(delete(s.audit).where(s.audit.c.id == target["audit_id"]))
                )
                assert not await database.read(
                    lambda u: u.connection.execute(
                        select(s.audit.c.id).where(s.audit.c.id == target["audit_id"])
                    ).first()
                )
                assert (
                    await database.read(
                        lambda u: dict(
                            u.connection.execute(select(s.audit_retention_targets).where(target_where)).mappings().one()
                        )
                    )
                    == target
                )
                await database.write(
                    lambda u: u.connection.execute(
                        update(s.audit_retention_targets).where(target_where).values(state="deleted", ended_at=ended)
                    )
                )
                await denied(
                    update(s.audit_retention_targets).where(target_where).values(state="pending", ended_at=None)
                )
                await database.write(
                    lambda u: u.connection.execute(
                        update(s.audit_retention_batches)
                        .where(batch_where)
                        .values(state="complete", completed_at=ended)
                    )
                )
                await denied(
                    update(s.audit_retention_batches).where(batch_where).values(state="prepared", completed_at=None)
                )
                await denied(delete(s.audit_retention_batches).where(batch_where))
                persisted_run = await database.read(lambda u: u.get_run(batch["agent_id"], run["run_id"]))
                # run.queued plus the routing.selected event this fixture's default route emits.
                assert persisted_run["state"] == "queued" and persisted_run["last_seq"] == 2
                messages = await database.read(lambda u: u.messages(batch["agent_id"], run["session_id"]))
                assert [row["content"] for row in messages] == ["Preserved evidence source"]
                report = await database.integrity()
                assert report["integrity"] == "ok" and report["foreign_keys"] == 0
            finally:
                await database.close()

        asyncio.run(scenario())
