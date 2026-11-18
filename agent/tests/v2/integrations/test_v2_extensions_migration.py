"""V19 frozen DDL, empty captures, referenced source archives and restore revocation; never executes plugin code."""

from __future__ import annotations

from omni_test_support.v2.extensions_migration import (
    FROZEN as FROZEN,
)
from omni_test_support.v2.extensions_migration import (
    SOURCES as SOURCES,
)
from omni_test_support.v2.extensions_migration import (
    TABLES as TABLES,
)
from omni_test_support.v2.extensions_migration import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.extensions_migration import (
    CheckConstraint as CheckConstraint,
)
from omni_test_support.v2.extensions_migration import (
    Clock as Clock,
)
from omni_test_support.v2.extensions_migration import (
    Database as Database,
)
from omni_test_support.v2.extensions_migration import (
    InstancePaths as InstancePaths,
)
from omni_test_support.v2.extensions_migration import (
    LegacySeedRepositories as LegacySeedRepositories,
)
from omni_test_support.v2.extensions_migration import (
    SystemClock as SystemClock,
)
from omni_test_support.v2.extensions_migration import (
    UniqueConstraint as UniqueConstraint,
)
from omni_test_support.v2.extensions_migration import (
    UUIDs as UUIDs,
)
from omni_test_support.v2.extensions_migration import (
    action_fixture as action_fixture,
)
from omni_test_support.v2.extensions_migration import (
    add_model_headers_column as add_model_headers_column,
)
from omni_test_support.v2.extensions_migration import (
    assert_schema as assert_schema,
)
from omni_test_support.v2.extensions_migration import (
    asyncio as asyncio,
)
from omni_test_support.v2.extensions_migration import (
    backup as backup,
)
from omni_test_support.v2.extensions_migration import (
    baseline as baseline,
)
from omni_test_support.v2.extensions_migration import (
    capture_directory as capture_directory,
)
from omni_test_support.v2.extensions_migration import (
    chain as chain,
)
from omni_test_support.v2.extensions_migration import (
    chinese_manifest as chinese_manifest,
)
from omni_test_support.v2.extensions_migration import (
    command as command,
)
from omni_test_support.v2.extensions_migration import (
    configuration as configuration,
)
from omni_test_support.v2.extensions_migration import (
    create_engine as create_engine,
)
from omni_test_support.v2.extensions_migration import (
    delete as delete,
)
from omni_test_support.v2.extensions_migration import (
    enable as enable,
)
from omni_test_support.v2.extensions_migration import (
    encoded as encoded,
)
from omni_test_support.v2.extensions_migration import (
    inspect as inspect,
)
from omni_test_support.v2.extensions_migration import (
    inspect_database as inspect_database,
)
from omni_test_support.v2.extensions_migration import (
    installed as installed,
)
from omni_test_support.v2.extensions_migration import (
    json as json,
)
from omni_test_support.v2.extensions_migration import (
    manifest as manifest,
)
from omni_test_support.v2.extensions_migration import (
    migration_head as migration_head,
)
from omni_test_support.v2.extensions_migration import (
    normalized_check as normalized_check,
)
from omni_test_support.v2.extensions_migration import (
    opened as opened,
)
from omni_test_support.v2.extensions_migration import (
    owned as owned,
)
from omni_test_support.v2.extensions_migration import (
    preserved_rows as preserved_rows,
)
from omni_test_support.v2.extensions_migration import (
    pytest as pytest,
)
from omni_test_support.v2.extensions_migration import (
    raw_run as raw_run,
)
from omni_test_support.v2.extensions_migration import (
    registry as registry,
)
from omni_test_support.v2.extensions_migration import (
    restore as restore,
)
from omni_test_support.v2.extensions_migration import (
    s as s,
)
from omni_test_support.v2.extensions_migration import (
    select as select,
)
from omni_test_support.v2.extensions_migration import (
    sqlite3 as sqlite3,
)
from omni_test_support.v2.extensions_migration import (
    sqlite_connection as sqlite_connection,
)
from omni_test_support.v2.extensions_migration import (
    subprocess as subprocess,
)
from omni_test_support.v2.extensions_migration import (
    sys as sys,
)
from omni_test_support.v2.extensions_migration import (
    tool_spec as tool_spec,
)
from omni_test_support.v2.extensions_migration import (
    update as update,
)
from omni_test_support.v2.extensions_migration import (
    verify_snapshot as verify_snapshot,
)


@pytest.mark.parametrize(
    "marker",
    [
        "CREATE TABLE extension_tools",
        "CREATE INDEX ix_grants_review_audit",
        "CREATE TRIGGER extension_install_requests_keep",
    ],
    ids=["table", "index", "trigger"],
)
def test_v19_process_exit_rolls_back_frozen_ddl_then_retries_with_verified_v18_snapshots(tmp_path, marker):
    paths = InstancePaths.resolve(tmp_path / "instance")
    original, old_schema = baseline(paths)
    script = tmp_path / "interrupt_v19.py"
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
    if statement.strip().startswith(sys.argv[2]): os._exit(99)
Database(paths.database,SystemClock(),UUIDs(),objects_root=paths.objects)
raise AssertionError('Requested v19 interruption did not occur')
""",
        encoding="utf-8",
    )
    child = subprocess.run(
        [sys.executable, str(script), str(paths.home), marker],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert child.returncode == 99, child.stderr
    with owned(paths):
        with sqlite_connection(paths.database) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "v2_0018"
            assert (
                connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
                == old_schema
            )
            assert preserved_rows(connection, SOURCES + ("extensions", "grants")) == original
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert not connection.execute("PRAGMA foreign_key_check").fetchall()

        async def retry():
            database = Database(paths.database, SystemClock(), UUIDs(), objects_root=paths.objects)
            try:
                assert database.schema_version == migration_head()
                await database.read(assert_schema)
                assert (await database.integrity())["foreign_keys"] == 0
            finally:
                await database.close()

        asyncio.run(retry())
        with sqlite_connection(paths.database) as connection:
            assert preserved_rows(connection, SOURCES) == {
                key: value for key, value in original.items() if key in SOURCES
            }
            epoch = connection.execute("SELECT epoch FROM history_identity WHERE slot=1").fetchone()[0]
            assert connection.execute("SELECT DISTINCT extension_capture_epoch FROM runs").fetchall() == [(epoch,)]
    snapshots = list(paths.database.parent.glob("schema-backups/*"))
    assert len(snapshots) == 2
    for snapshot in snapshots:
        report = verify_snapshot(snapshot)
        assert report["complete"] and report["database"]["schema"] == "v2_0018"
        assert report["database"]["integrity"] == "ok" and report["database"]["foreign_key_errors"] == 0


@pytest.mark.parametrize("placeholder", ["extensions", "grants"])
def test_v19_refuses_nonempty_placeholders_without_mutation_and_keeps_restorable_backup(tmp_path, placeholder):
    paths = InstancePaths.resolve(tmp_path / "instance")
    original, old_schema = baseline(paths, placeholder=placeholder)
    with owned(paths):
        with pytest.raises(BusinessError) as failure:
            Database(paths.database, SystemClock(), UUIDs(), objects_root=paths.objects)
        assert failure.value.code == "extension_placeholder_not_empty"
        with sqlite_connection(paths.database) as connection:
            assert preserved_rows(connection, SOURCES + ("extensions", "grants")) == original
            assert (
                connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
                == old_schema
            )
    snapshots = list(paths.database.parent.glob("schema-backups/*"))
    assert len(snapshots) == 1 and verify_snapshot(snapshots[0])["database"]["schema"] == "v2_0018"
    destination = tmp_path / "restored-v18"
    report = restore(snapshots[0], destination)
    assert report["database"]["schema"] == "v2_0018" and report["extensions_quarantined"] is True
    with sqlite_connection(destination / "data/omni.db") as connection:
        assert preserved_rows(connection, ("extensions", "grants")) == {
            key: original[key] for key in ("extensions", "grants")
        }
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()


def test_v19_deferred_cycle_full_schema_source_object_and_audit_references_are_real(tmp_path):
    async def scenario():
        async with opened(tmp_path / "instance", Clock()) as (paths, database, objects, agent):
            assert database.schema_version == migration_head()
            await database.read(assert_schema)
            slot, source, package = await installed(database, objects, agent, tmp_path / "source", chinese_manifest())
            assert slot["enabled"] is False and slot["file_count"] == 2 and slot["package_sha256"] == source["sha256"]
            assert slot["manifest"] == package.manifest and slot["files"] == list(package.files)
            assert len(json.dumps(slot["manifest"]).encode()) > 65_536
            reference = await database.read(lambda u: u.object_get(agent, source["id"]))
            assert reference["references"] == 1
            assert source["id"] in registry(paths.database)[3]
            slot = await enable(database, agent, slot)
            grant = await database.read(
                lambda u: dict(
                    u.connection.execute(select(s.grants).where(s.grants.c.extension_id == slot["id"]))
                    .mappings()
                    .first()
                )
            )
            with pytest.raises(BusinessError) as failure:
                await database.write(
                    lambda u: u.connection.execute(delete(s.audit).where(s.audit.c.id == grant["review_audit_id"]))
                )
            assert failure.value.code == "data_constraint"
            with pytest.raises(BusinessError) as failure:
                await database.write(
                    lambda u: u.connection.execute(delete(s.objects).where(s.objects.c.id == source["id"]))
                )
            assert failure.value.code == "data_constraint"
            report = inspect_database(paths.database)
            assert report["enabled_extensions"] == 1 and report["extension_attention"] == 0
            assert (await database.integrity())["foreign_keys"] == 0

    asyncio.run(scenario())


def test_empty_run_capture_cannot_gain_later_grants_or_have_marker_cleared(tmp_path):
    async def scenario():
        async with opened(tmp_path / "instance", Clock()) as (_, database, objects, agent):
            old = await database.write(lambda u: u.accept_turn(agent, None, "empty", "No enabled extensions yet", {}))
            marker = (await database.read(lambda u: u.get_run(agent, old["run_id"])))["extension_capture_epoch"]
            assert marker and await database.read(lambda u: u.extension_run_tools(agent, old["run_id"])) == []
            slot, _, _ = await installed(database, objects, agent, tmp_path / "source")
            await enable(database, agent, slot)
            await database.write(lambda u: u.extension_capture_run(agent, old["run_id"]))
            assert await database.read(lambda u: u.extension_run_tools(agent, old["run_id"])) == []
            with pytest.raises(BusinessError) as failure:
                await database.write(
                    lambda u: u.connection.execute(
                        update(s.runs).where(s.runs.c.id == old["run_id"]).values(extension_capture_epoch=None)
                    )
                )
            assert failure.value.code == "data_constraint"
            new = await database.write(lambda u: u.accept_turn(agent, None, "new", "New capture sees enabled tool", {}))
            rows = await database.read(lambda u: u.extension_run_tools(agent, new["run_id"]))
            assert len(rows) == 1
            raw = await database.read(lambda u: u._one(s.run_extension_tools, agent, rows[0]["binding_id"]))
            with pytest.raises(BusinessError) as failure:
                await database.write(
                    lambda u: u._record(
                        s.run_extension_tools,
                        {
                            key: value
                            for key, value in {**raw, "run_id": old["run_id"]}.items()
                            if key not in {"id", "created_at", "agent_id"}
                        },
                        agent,
                    )
                )
            assert failure.value.code == "data_constraint"

    asyncio.run(scenario())


def test_v19_captured_rows_and_grant_identity_are_immutable_and_never_deleted(tmp_path):
    async def scenario():
        async with opened(tmp_path / "instance", Clock()) as (_, database, objects, agent):
            slot, _, _ = await installed(database, objects, agent, tmp_path / "source")
            slot = await enable(database, agent, slot)
            await database.write(lambda u: action_fixture(u, agent, "pending"))
            for name in TABLES:
                table = s.metadata.tables[name]
                row = await database.read(
                    lambda u, table=table: dict(u.connection.execute(select(table)).mappings().first())
                )
                with pytest.raises(BusinessError) as failure:
                    await database.write(
                        lambda u, table=table, row=row: u.connection.execute(
                            delete(table).where(table.c.id == row["id"])
                        )
                    )
                assert failure.value.code == "data_constraint"
                values = {"created_at": "2000-01-01T00:00:00.000000+00:00"}
                if name == "extensions":
                    values.update(revision=row["revision"] + 1, authority_epoch=row["authority_epoch"] + 1)
                with pytest.raises(BusinessError) as failure:
                    await database.write(
                        lambda u, table=table, row=row, values=values: u.connection.execute(
                            update(table).where(table.c.id == row["id"]).values(**values)
                        )
                    )
                assert failure.value.code == "data_constraint"
            grant = await database.read(lambda u: dict(u.connection.execute(select(s.grants)).mappings().one()))
            await database.write(
                lambda u: u.connection.execute(
                    update(s.grants).where(s.grants.c.id == grant["id"]).values(revoked_at=u.clock.now())
                )
            )
            with pytest.raises(BusinessError) as failure:
                await database.write(
                    lambda u: u.connection.execute(
                        update(s.grants).where(s.grants.c.id == grant["id"]).values(revoked_at=None)
                    )
                )
            assert failure.value.code == "data_constraint"

    asyncio.run(scenario())


def test_restore_revokes_all_plugin_authority_in_one_transaction_and_preserves_captured_evidence(tmp_path):
    async def seed():
        async with opened(tmp_path / "instance", Clock()) as (paths, database, objects, agent):
            slot, object_row, package = await installed(database, objects, agent, tmp_path / "source")
            slot = await enable(database, agent, slot)
            actions = [
                await database.write(lambda u, state=state: action_fixture(u, agent, state))
                for state in ("pending", "approved", "executing")
            ]
            with sqlite_connection(paths.database) as connection:
                frozen = preserved_rows(connection, FROZEN + ("objects",))
                epoch = connection.execute("SELECT epoch FROM history_identity WHERE slot=1").fetchone()[0]
            return paths, slot, object_row, package, actions, frozen, epoch

    paths, slot, source, package, actions, frozen, old_epoch = asyncio.run(seed())
    destination, snapshot = tmp_path / "restored", tmp_path / "snapshot"
    backup(paths, snapshot)
    report = restore(snapshot, destination)
    assert report["extensions_quarantined"] is True and report["execution_quarantined"] is True
    counts = report["quarantine"]
    assert counts["disabled_extensions"] == 1 and counts["revoked_extension_grants"] == 1
    assert counts["cancelled_extension_actions"] == 2 and counts["cancelled_extension_approvals"] == 3
    restored_paths = InstancePaths.resolve(destination)
    assert counts["history_epoch"] != old_epoch
    with sqlite_connection(restored_paths.database) as connection:
        assert preserved_rows(connection, FROZEN + ("objects",)) == frozen
        assert connection.execute("SELECT enabled,state,revision,authority_epoch FROM extensions").fetchone() == (
            0,
            "disabled",
            slot["revision"] + 1,
            slot["authority_epoch"] + 1,
        )
        assert connection.execute("SELECT COUNT(*) FROM grants WHERE revoked_at IS NULL").fetchone()[0] == 0
        assert connection.execute("SELECT DISTINCT history_epoch FROM grants").fetchall() == [(old_epoch,)]
        for index, action in enumerate(actions):
            assert connection.execute(
                "SELECT state,effect FROM actions WHERE id=?", (action["action_id"],)
            ).fetchone() == (("unknown", "unknown") if index == 2 else ("cancelled", "none"))
            assert (
                connection.execute("SELECT state FROM approvals WHERE id=?", (action["approval_id"],)).fetchone()[0]
                == "cancelled"
            )
        assert (
            connection.execute("SELECT COUNT(*) FROM audit WHERE kind='extension.restored_disabled'").fetchone()[0] == 1
        )
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
        unchanged = preserved_rows(
            connection, ("extensions", "grants", "actions", "approvals", "audit", "run_extension_tools")
        )
    assert (
        restored_paths.objects / f"{source['agent_id']}/{source['sha256'][:2]}/{source['sha256']}.blob"
    ).read_bytes() == package.content
    assert restore(snapshot, destination)["already_restored"] is True
    with sqlite_connection(restored_paths.database) as connection:
        assert (
            preserved_rows(connection, ("extensions", "grants", "actions", "approvals", "audit", "run_extension_tools"))
            == unchanged
        )

    async def verify_revoked():
        async with opened(destination, Clock()) as (_, database, _, agent):
            for action in actions:
                assert (
                    await database.read(lambda u, action=action: u.extension_run_tools(agent, action["run_id"])) == []
                )

    asyncio.run(verify_revoked())


def test_restore_audit_failure_rolls_back_epoch_grants_actions_and_run_state_together(tmp_path):
    async def seed():
        async with opened(tmp_path / "instance", Clock()) as (paths, database, objects, agent):
            slot, _, _ = await installed(database, objects, agent, tmp_path / "source")
            await enable(database, agent, slot)
            for state in ("pending", "approved", "executing"):
                await database.write(lambda u, state=state: action_fixture(u, agent, state))
            await database.write(
                lambda u: u.connection.exec_driver_sql(
                    "CREATE TRIGGER reject_extension_restore_audit BEFORE INSERT ON audit "
                    "WHEN new.kind='extension.restored_disabled' "
                    "BEGIN SELECT RAISE(ABORT,'isolated restore audit failure'); END"
                )
            )
            with sqlite_connection(paths.database) as connection:
                before = preserved_rows(
                    connection, ("extensions", "grants", "actions", "approvals", "runs", "audit", "history_identity")
                )
            return paths, before

    paths, before = asyncio.run(seed())
    snapshot, destination = tmp_path / "snapshot", tmp_path / "restored"
    backup(paths, snapshot)
    with pytest.raises(sqlite3.IntegrityError, match="isolated restore audit failure"):
        restore(snapshot, destination)
    assert (destination / "restore-in-progress.json").is_file()
    assert not (destination / "restore-report.json").exists()
    with sqlite_connection(destination / "data/omni.db") as connection:
        assert preserved_rows(connection, tuple(before)) == before
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
