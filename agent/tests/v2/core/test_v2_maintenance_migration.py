"""Real v17 migration crashes, immutable attempt evidence and restored maintenance isolation; local fixtures only."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from itertools import chain
from uuid import UUID

import pytest
from alembic import command
from omni_test_support.v2.audit_plan_migration import owned
from omni_test_support.v2.event_plan_migration import normalized_check
from omni_test_support.v2.objects import Clock, opened
from omni_test_support.v2.v2_configuration_helpers import add_model_headers_column
from omni_test_support.v2.v2_legacy_seed import LegacySeedRepositories, preserved_rows
from sqlalchemy import CheckConstraint, UniqueConstraint, create_engine, delete, insert, inspect, select, update

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.database import Database
from omni.adapters.persistence.maintenance import backup, restore, verify_snapshot
from omni.adapters.persistence.migration_config import configuration
from omni.adapters.persistence.migration_config import head as migration_head
from omni.adapters.persistence.sqlite_access import sqlite_connection
from omni.adapters.platform.runtime.paths import InstancePaths
from omni.adapters.primitives import SystemClock, UUIDs
from omni.application.documents import DocumentService
from omni.domain.contracts import BusinessError
from omni.domain.documents import TEXT_INDEX_VERSION
from omni.domain.maintenance import lease_until

NEW_TABLES = {"maintenance_jobs", "maintenance_attempts"}
NEW_INDEXES = {
    "ix_maintenance_jobs_agent_id",
    "ix_maintenance_jobs_agent_sequence",
    "ix_maintenance_jobs_active_document",
    "ix_maintenance_attempts_agent_id",
    "ix_maintenance_attempts_active_job",
}
NEW_TRIGGERS = {
    "maintenance_job_identity",
    "maintenance_attempt_identity",
    "maintenance_job_keep",
    "maintenance_attempt_keep",
}
SOURCE_TABLES = (
    "instances",
    "agents",
    "runs",
    "messages",
    "run_events",
    "audit",
    "history_identity",
    "history_sequences",
    "history_scopes",
    "audit_retention_batches",
    "audit_retention_targets",
    "event_retention_batches",
    "event_retention_targets",
)


def source_rows(connection):
    return preserved_rows(connection, SOURCE_TABLES)


def baseline(paths):
    paths.initialize()
    with owned(paths):
        engine = create_engine("sqlite+pysqlite:///" + str(paths.database))
        try:
            with engine.begin() as connection:
                cfg = configuration()
                cfg.attributes["connection"] = connection
                command.upgrade(cfg, "v2_0016")
                add_model_headers_column(connection)
                unit = LegacySeedRepositories(connection, SystemClock(), UUIDs())
                unit.initialize()
                agent = unit.query("agents", "")[0]["id"]
                unit.accept_turn(
                    agent, None, "v16-maintenance-migration", "Preserved maintenance migration message", {}
                )
            with sqlite_connection(paths.database) as connection:
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "v2_0016"
                assert not NEW_TABLES & {
                    row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                assert not connection.execute("PRAGMA foreign_key_check").fetchall()
                return source_rows(connection), connection.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
                ).fetchall()
        finally:
            engine.dispose()


def assert_schema(unit):
    reflection = inspect(unit.connection)
    assert len(s.metadata.tables) == 145
    assert set(s.metadata.tables) <= set(reflection.get_table_names())
    for name in NEW_TABLES:
        table = s.metadata.tables[name]
        columns = reflection.get_columns(name)
        assert {column["name"] for column in columns} == set(table.c.keys())
        assert {column["name"]: column["nullable"] for column in columns} == {
            column.name: column.nullable for column in table.c
        }
        assert tuple(reflection.get_pk_constraint(name)["constrained_columns"]) == tuple(
            table.primary_key.columns.keys()
        )
        assert {tuple(item["column_names"]) for item in reflection.get_unique_constraints(name)} == {
            tuple(constraint.columns.keys())
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        assert {normalized_check(item["sqltext"]) for item in reflection.get_check_constraints(name)} == {
            normalized_check(constraint.sqltext)
            for constraint in chain(table.constraints, *(column.constraints for column in table.c))
            if isinstance(constraint, CheckConstraint)
        }
        assert {
            (tuple(item["constrained_columns"]), item["referred_table"], tuple(item["referred_columns"]))
            for item in reflection.get_foreign_keys(name)
        } == {
            (
                tuple(constraint.column_keys),
                constraint.referred_table.name,
                tuple(element.column.name for element in constraint.elements),
            )
            for constraint in table.foreign_key_constraints
        }
    ddl = unit.connection.exec_driver_sql("SELECT sql FROM sqlite_master WHERE name='maintenance_jobs'").scalar_one()
    assert "AUTOINCREMENT" in ddl.upper() and tuple(s.maintenance_jobs.primary_key.columns.keys()) == ("sequence",)
    indexes = {item["name"]: item for name in NEW_TABLES for item in reflection.get_indexes(name)}
    assert NEW_INDEXES <= set(indexes)
    assert indexes["ix_maintenance_jobs_agent_sequence"]["column_names"] == ["agent_id", "sequence"]
    for name, columns, predicate in (
        (
            "ix_maintenance_jobs_active_document",
            ["agent_id", "document_id"],
            "state IN ('claimed','running','cancelling')",
        ),
        ("ix_maintenance_attempts_active_job", ["job_id"], "state IN ('claimed','running')"),
    ):
        actual = indexes[name]
        assert actual["unique"] == 1 and actual["column_names"] == columns
        assert normalized_check(actual["dialect_options"]["sqlite_where"]) == normalized_check(predicate)
    assert NEW_TRIGGERS <= {
        row[0] for row in unit.connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='trigger'")
    }


@pytest.mark.parametrize(
    "marker",
    [
        "CREATE TABLE maintenance_attempts",
        "CREATE UNIQUE INDEX ix_maintenance_attempts_active_job",
        "CREATE TRIGGER maintenance_attempt_keep",
    ],
    ids=["table", "index", "trigger"],
)
def test_v17_process_exit_rolls_back_ddl_and_preserves_v16_then_retries_with_verified_backups(tmp_path, marker):
    paths = InstancePaths.resolve(tmp_path / "instance")
    original, original_schema = baseline(paths)
    script = tmp_path / "interrupt_v17.py"
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
        os._exit(97)
Database(paths.database,SystemClock(),UUIDs(),objects_root=paths.objects)
raise AssertionError('Requested v17 interruption did not occur')
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
    assert child.returncode == 97, child.stderr
    with owned(paths):
        with sqlite_connection(paths.database) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "v2_0016"
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
        assert manifest["complete"] and manifest["database"]["schema"] == "v2_0016"
        assert manifest["database"]["integrity"] == "ok" and not manifest["database"]["foreign_key_errors"]


def test_fresh_v17_has_80_declared_tables_and_empty_maintenance_queue(tmp_path):
    paths = InstancePaths.resolve(tmp_path / "instance")
    paths.initialize()
    with owned(paths):

        async def scenario():
            database = Database(paths.database, SystemClock(), UUIDs(), objects_root=paths.objects)
            try:
                assert database.schema_version == migration_head()
                await database.read(assert_schema)
                for table in (s.maintenance_jobs, s.maintenance_attempts):
                    assert await database.read(lambda u: u.connection.execute(select(table)).all()) == []
                await database.write(lambda u: u.initialize())
                assert not (paths.database.parent / "schema-backups").exists()
            finally:
                await database.close()

        asyncio.run(scenario())


def request(document, key):
    return {
        "kind": "document_index_rebuild",
        "key": key,
        "document_id": document["id"],
        "revision": document["revision"],
    }


def job_values(row, **changes):
    values = {key: row[key] for key in s.maintenance_jobs.c.keys() if key != "sequence"}
    return {**values, **changes}


def test_job_uuid_sequence_unique_scope_and_attempt_identity_build_binding_and_terminal_evidence(tmp_path):
    async def scenario():
        clock = Clock()
        async with opened(tmp_path / "instance", clock) as (_, database, objects, agent):
            documents = DocumentService(database, objects)
            try:
                document = await documents.save(agent, "DDL source", "Source stays unchanged")
                first = await database.write(lambda u: u.maintenance_enqueue(agent, request(document, "same-key")))
                second = await database.write(lambda u: u.maintenance_enqueue(agent, request(document, "next-key")))
                assert str(UUID(first["id"])) == first["id"] and first["id"] != str(first["sequence"])
                assert second["sequence"] == first["sequence"] + 1

                async def denied(statement):
                    with pytest.raises(BusinessError) as failure:
                        await database.write(lambda u: u.connection.execute(statement))
                    assert failure.value.code == "data_constraint"

                await denied(insert(s.maintenance_jobs).values(**job_values(first, id="bad", request_key="bad-uuid")))
                await denied(insert(s.maintenance_jobs).values(**job_values(first, id=UUIDs().new())))
                await database.write(
                    lambda u: u.connection.execute(
                        insert(s.maintenance_jobs).values(
                            **job_values(first, id=UUIDs().new(), request_key="explicit-high-sequence"), sequence=700
                        )
                    )
                )
                next_job = await database.write(lambda u: u.maintenance_enqueue(agent, request(document, "after-high")))
                assert next_job["sequence"] == 701
                claims = await database.write(lambda u: u.maintenance_claim(1))
                assert len(claims) == 1 and claims[0]["id"] == first["id"]
                claim = claims[0]
                attempt = claim["attempt"]
                await denied(
                    update(s.maintenance_jobs).where(s.maintenance_jobs.c.id == second["id"]).values(state="claimed")
                )
                for changes in (
                    {"id": UUIDs().new()},
                    {"request_key": "changed"},
                    {"revision": 1},
                    {"attempt_count": 0},
                ):
                    await denied(
                        update(s.maintenance_jobs).where(s.maintenance_jobs.c.id == first["id"]).values(**changes)
                    )
                await denied(delete(s.maintenance_attempts).where(s.maintenance_attempts.c.id == attempt["id"]))
                # A targetless queued job isolates its keep trigger from attempt foreign keys.
                await denied(delete(s.maintenance_jobs).where(s.maintenance_jobs.c.id == next_job["id"]))
                for changes in ({"token": UUIDs().new()}, {"boot_id": UUIDs().new()}, {"fence": 2}, {"attempt_no": 2}):
                    await denied(
                        update(s.maintenance_attempts)
                        .where(s.maintenance_attempts.c.id == attempt["id"])
                        .values(**changes)
                    )
                duplicate = {**attempt, "id": UUIDs().new(), "job_id": second["id"]}
                await denied(insert(s.maintenance_attempts).values(**duplicate))
                duplicate = {**attempt, "id": UUIDs().new(), "token": UUIDs().new(), "fence": 0, "job_id": second["id"]}
                await denied(insert(s.maintenance_attempts).values(**duplicate))
                duplicate = {
                    **attempt,
                    "id": UUIDs().new(),
                    "token": UUIDs().new(),
                    "state": "failed",
                    "ended_at": clock.now(),
                }
                await denied(insert(s.maintenance_attempts).values(**duplicate))
                active_duplicate = {**attempt, "id": UUIDs().new(), "token": UUIDs().new(), "attempt_no": 2}
                await denied(insert(s.maintenance_attempts).values(**active_duplicate))

                def builds(unit):
                    return [
                        unit._record(
                            s.document_index_builds,
                            {
                                "revision_id": document["revision_id"],
                                "kind": "full_text",
                                "version": TEXT_INDEX_VERSION,
                                "dimension": None,
                                "state": "building",
                                "error_code": "",
                                "completed_at": None,
                            },
                            agent,
                        )["id"]
                        for _ in range(2)
                    ]

                first_build, other_build = await database.write(builds)
                await database.write(
                    lambda u: u.connection.execute(
                        update(s.maintenance_attempts)
                        .where(s.maintenance_attempts.c.id == attempt["id"])
                        .values(build_id=first_build)
                    )
                )
                for build_id in (None, other_build):
                    await denied(
                        update(s.maintenance_attempts)
                        .where(s.maintenance_attempts.c.id == attempt["id"])
                        .values(build_id=build_id)
                    )
                await database.write(lambda u: u.maintenance_start(claim))
                await database.write(
                    lambda u: u.connection.execute(
                        update(s.maintenance_attempts)
                        .where(s.maintenance_attempts.c.id == attempt["id"])
                        .values(state="succeeded", ended_at=clock.now())
                    )
                )
                for changes in (
                    {"state": "running", "ended_at": None},
                    {"state": "failed"},
                    {"error_code": "changed"},
                    {"lease_until": lease_until(clock.now()) + "changed"},
                    {"build_id": other_build},
                ):
                    await denied(
                        update(s.maintenance_attempts)
                        .where(s.maintenance_attempts.c.id == attempt["id"])
                        .values(**changes)
                    )
                await database.write(
                    lambda u: u.connection.execute(
                        update(s.maintenance_jobs)
                        .where(s.maintenance_jobs.c.id == first["id"])
                        .values(
                            state="succeeded",
                            finished_at=clock.now(),
                            updated_at=clock.now(),
                            revision=s.maintenance_jobs.c.revision + 1,
                            result_build_id=first_build,
                        )
                    )
                )
                for changes in (
                    {"state": "queued", "finished_at": None},
                    {"result_build_id": other_build},
                    {"error_code": "changed"},
                    {"revision": 99},
                ):
                    await denied(
                        update(s.maintenance_jobs).where(s.maintenance_jobs.c.id == first["id"]).values(**changes)
                    )
                other_agent = (await database.write(lambda u: u.create_agent("Other maintenance scope")))["id"]
                other_document = await documents.save(other_agent, "Other source", "Other Agent source")
                same_key = await database.write(
                    lambda u: u.maintenance_enqueue(other_agent, request(other_document, "same-key"))
                )
                assert same_key["id"] != first["id"] and same_key["agent_id"] == other_agent
                assert (await documents.get(agent, document["id"]))["content"] == "Source stays unchanged"
                report = await database.integrity()
                assert report["integrity"] == "ok" and report["foreign_keys"] == 0
            finally:
                assert await documents.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("active_state", ["claimed", "running", "cancelling"])
def test_restore_cancels_queued_and_active_maintenance_without_restarting_it(tmp_path, active_state):
    async def seed():
        async with opened(tmp_path / "instance", Clock()) as (paths, database, objects, agent):
            documents = DocumentService(database, objects)
            try:
                active_doc = await documents.save(agent, "Active source", "Active maintenance source")
                queued_doc = await documents.save(agent, "Queued source", "Queued maintenance source")
                active = await database.write(lambda u: u.maintenance_enqueue(agent, request(active_doc, "active")))
                claim = (await database.write(lambda u: u.maintenance_claim(1)))[0]
                if active_state in {"running", "cancelling"}:
                    active = await database.write(lambda u: u.maintenance_start(claim))
                else:
                    active = claim
                if active_state == "cancelling":
                    active = await database.write(
                        lambda u: u.maintenance_cancel(agent, active["id"], active["revision"])
                    )
                queued = await database.write(lambda u: u.maintenance_enqueue(agent, request(queued_doc, "queued")))
                terminal = await database.write(lambda u: u.maintenance_enqueue(agent, request(queued_doc, "terminal")))
                terminal = await database.write(
                    lambda u: u.maintenance_cancel(agent, terminal["id"], terminal["revision"])
                )
                epoch = await database.read(
                    lambda u: u.connection.execute(select(s.history_identity.c.epoch)).scalar_one()
                )
                return paths, agent, active, queued, terminal, epoch
            finally:
                assert await documents.close()

    paths, agent, active, queued, terminal, epoch = asyncio.run(seed())
    source = tmp_path / "snapshot"
    manifest = backup(paths, source)
    assert verify_snapshot(source) == manifest
    destination = tmp_path / "restored"
    report = restore(source, destination)
    assert report["maintenance_quarantined"] and report["execution_quarantined"]
    assert report["quarantine"]["cancelled_maintenance_jobs"] == 2
    assert report["quarantine"]["interrupted_maintenance_attempts"] == 1
    assert report["quarantine"]["history_epoch"] != epoch
    with sqlite_connection(destination / "data/omni.db") as connection:
        rows = {
            row[0]: row[1:]
            for row in connection.execute(
                "SELECT id,state,revision,error_code,finished_at FROM maintenance_jobs WHERE agent_id=?", (agent,)
            )
        }
        for original in (active, queued):
            state, revision, code, finished_at = rows[original["id"]]
            assert (state, revision, code) == ("cancelled", original["revision"] + 1, "backup_restored_requires_review")
            assert finished_at is not None
        assert rows[terminal["id"]][:3] == (terminal["state"], terminal["revision"], terminal["error_code"])
        attempt = connection.execute(
            "SELECT id,token,boot_id,fence,state,error_code,ended_at FROM maintenance_attempts WHERE job_id=?",
            (active["id"],),
        ).fetchone()
        assert attempt[:4] == tuple(active["attempt"][key] for key in ("id", "token", "boot_id", "fence"))
        assert attempt[4:6] == ("interrupted", "backup_restored_requires_review") and attempt[6] is not None
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM maintenance_jobs WHERE state IN ('queued','claimed','running','cancelling')"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM source_documents WHERE deleted_at IS NOT NULL").fetchone()[0] == 0
        )
        assert (
            connection.execute("SELECT epoch FROM history_identity").fetchone()[0]
            == report["quarantine"]["history_epoch"]
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM audit WHERE kind='maintenance.restored_cancelled'").fetchone()[0]
            == 2
        )
        before = connection.execute("SELECT * FROM maintenance_jobs ORDER BY sequence").fetchall()
    assert json.loads((destination / "restore-report.json").read_text())["maintenance_quarantined"] is True
    assert restore(source, destination)["already_restored"]
    with sqlite_connection(destination / "data/omni.db") as connection:
        assert connection.execute("SELECT * FROM maintenance_jobs ORDER BY sequence").fetchall() == before
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
