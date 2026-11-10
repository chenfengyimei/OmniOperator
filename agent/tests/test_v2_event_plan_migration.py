"""Real v16 DDL recovery and immutable event-retention evidence; isolated databases and no external actions."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from itertools import chain

import pytest
from alembic import command
from sqlalchemy import CheckConstraint, UniqueConstraint, create_engine, delete, insert, inspect, select, update
from test_v2_audit_plan_migration import owned
from test_v2_event_retention_contract import make_plan
from v2_legacy_seed import LegacySeedRepositories, preserved_rows

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.database import Database
from omni.adapters.persistence.maintenance import verify_snapshot
from omni.adapters.persistence.migration_config import configuration
from omni.adapters.persistence.migration_config import head as migration_head
from omni.adapters.persistence.sqlite_access import sqlite_connection
from omni.adapters.platform.paths import InstancePaths
from omni.adapters.primitives import SystemClock, UUIDs
from omni.domain.contracts import BusinessError
from omni.domain.event_retention import validate_event_plan

NEW_TABLES = {"event_retention_batches", "event_retention_targets"}
NEW_INDEXES = {
    "ix_event_retention_batches_agent_id",
    "ix_event_retention_prepared_agent",
    "ix_event_retention_targets_agent_id",
    "ix_event_retention_pending_sequence",
}
NEW_TRIGGERS = {
    "event_retention_batch_identity",
    "event_retention_target_identity",
    "event_retention_batch_keep",
    "event_retention_target_keep",
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
    "retention_batches",
    "audit_retention_batches",
    "audit_retention_targets",
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
                command.upgrade(cfg, "v2_0015")
                unit = LegacySeedRepositories(connection, SystemClock(), UUIDs())
                unit.initialize()
                agent = unit.query("agents", "")[0]["id"]
                unit.accept_turn(agent, None, "v15-request", "Preserved event migration message", {})
            with sqlite_connection(paths.database) as connection:
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "v2_0015"
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


def normalized_check(text):
    value = "".join(str(text).lower().split())
    while value.startswith("(") and value.endswith(")"):
        depth = 0
        for index, char in enumerate(value):
            depth += (char == "(") - (char == ")")
            if depth == 0:
                break
        if index != len(value) - 1:
            break
        value = value[1:-1]
    return value


def assert_schema(unit):
    reflection = inspect(unit.connection)
    assert len(s.metadata.tables) == 136
    assert set(s.metadata.tables) <= set(reflection.get_table_names())
    for name in NEW_TABLES:
        table = s.metadata.tables[name]
        actual_columns = reflection.get_columns(name)
        assert {column["name"] for column in actual_columns} == set(table.c.keys())
        assert {column["name"]: column["nullable"] for column in actual_columns} == {
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
    assert not {item["referred_table"] for item in reflection.get_foreign_keys("event_retention_targets")} & {
        "run_events",
        "audit",
        "actions",
    }
    indexes = {item["name"]: item for table in NEW_TABLES for item in reflection.get_indexes(table)}
    assert NEW_INDEXES <= set(indexes)
    for name, column, state in (
        ("ix_event_retention_prepared_agent", "agent_id", "prepared"),
        ("ix_event_retention_pending_sequence", "sequence", "pending"),
    ):
        actual = indexes[name]
        assert actual["unique"] == 1 and actual["column_names"] == [column]
        assert str(actual["dialect_options"]["sqlite_where"]).replace(" ", "") == f"state='{state}'"
    assert NEW_TRIGGERS <= {
        row[0] for row in unit.connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='trigger'")
    }


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
from omni.adapters.platform.paths import InstanceLock,InstancePaths
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


def evidence_rows(unit):
    """Create DDL-valid historical evidence directly; this fixture does not authorize pruning."""
    instance = unit.initialize()
    agent = unit.query("agents", "")[0]["id"]
    run = unit.accept_turn(agent, None, "event-schema-evidence", "Preserved event evidence source", {})
    event = dict(
        unit.connection.execute(
            select(s.run_events).where(s.run_events.c.run_id == run["run_id"], s.run_events.c.kind == "run.queued")
        )
        .mappings()
        .one()
    )
    plan = make_plan(1)
    plan.update(
        instance_id=instance["id"],
        agent_id=agent,
        epoch=unit.connection.execute(select(s.history_identity.c.epoch)).scalar_one(),
        high_water=event["sequence"],
    )
    target = plan["targets"][0]
    target.update(
        agent_id=agent, run_id=run["run_id"], sequence=event["sequence"], seq=event["seq"], kind=event["kind"]
    )
    unit.connection.execute(
        insert(s.retention_batches).values(
            id=target["prune_plan_id"],
            created_at=plan["created_at"],
            instance_id=instance["id"],
            plan_sha256="c" * 64,
            policy="execution-body-v1",
            cutoff=plan["cutoff"],
            schema_version="v2_0016",
            backup_path="fixture/body-snapshot",
            backup_sha256="d" * 64,
            state="prepared",
        )
    )
    batch = {
        key: plan[key]
        for key in (
            "id",
            "agent_id",
            "created_at",
            "instance_id",
            "policy",
            "schema_version",
            "as_of",
            "cutoff",
            "epoch",
            "trim_revision",
            "high_water",
        )
    }
    batch.update(
        plan_sha256=validate_event_plan(plan, plan["created_at"]),
        backup_path="fixture/event-snapshot",
        backup_sha256="e" * 64,
        state="prepared",
        completed_at=None,
    )
    target_row = {"batch_id": plan["id"], **target, "state": "pending", "ended_at": None}
    unit.connection.execute(insert(s.event_retention_batches).values(**batch))
    unit.connection.execute(insert(s.event_retention_targets).values(**target_row))
    return batch, target_row, run


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
