"""V20 review ledgers, erasable evidence, frozen execution observations and verified restore fencing."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from dataclasses import asdict
from itertools import chain

import pytest
from alembic import command
from sqlalchemy import CheckConstraint, UniqueConstraint, create_engine, delete, inspect, select, update
from test_v2_audit_plan_migration import owned
from test_v2_event_plan_migration import normalized_check
from test_v2_extensions_migration import raw_run
from test_v2_objects import Clock, opened
from v2_action_review_helpers import review_request, seed_unknown
from v2_legacy_seed import preserved_rows

from omni.adapters.persistence import maintenance
from omni.adapters.persistence import schema as s
from omni.adapters.persistence.database import Database
from omni.adapters.persistence.maintenance import backup, inspect_database, restore, verify_snapshot
from omni.adapters.persistence.migration_config import configuration
from omni.adapters.persistence.migration_config import head as migration_head
from omni.adapters.persistence.repository import Repositories
from omni.adapters.persistence.sqlite_access import sqlite_connection
from omni.adapters.platform.paths import InstancePaths
from omni.adapters.primitives import SystemClock, UUIDs
from omni.domain.contracts import ActionResult, BusinessError, Effect, Outcome

TABLES = {"action_review_heads", "action_reviews", "action_review_evidence"}
SOURCE_TABLES = (
    "principals",
    "instances",
    "agents",
    "people",
    "sessions",
    "messages",
    "runs",
    "actions",
    "audit",
    "model_calls",
    "memory_entries",
    "memory_revisions",
    "source_anchors",
    "history_identity",
    "history_sequences",
    "history_scopes",
)


def rows(connection, tables=SOURCE_TABLES):
    return preserved_rows(connection, tables, excluded_run_columns=("execution_kind",))


def baseline(paths):
    paths.initialize()
    with owned(paths):
        engine = create_engine("sqlite+pysqlite:///" + str(paths.database))
        try:
            with engine.begin() as connection:
                cfg = configuration()
                cfg.attributes["connection"] = connection
                command.upgrade(cfg, "v2_0019")
                unit = Repositories(connection, SystemClock(), UUIDs())
                unit.initialize()
                agent = unit.query("agents", "")[0]["id"]
                run = raw_run(unit, agent)
                arguments = {"path": "isolated.txt", "content": "preserved private baseline"}
                unit._record(
                    s.actions,
                    {
                        "run_id": run["id"],
                        "tool": "files_write",
                        "arguments": arguments,
                        "arguments_sha256": __import__("hashlib")
                        .sha256(
                            json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
                        )
                        .hexdigest(),
                        "writes": True,
                        "state": "unknown",
                        "effect": "unknown",
                        "policy_revision": 1,
                        "result": asdict(ActionResult(Outcome.UNKNOWN, "original outcome unknown", Effect.UNKNOWN)),
                        "ended_at": unit.clock.now(),
                    },
                    agent,
                )
                connection.execute(
                    update(s.runs).where(s.runs.c.id == run["id"]).values(state="failed", ended_at=unit.clock.now())
                )
                unit.remember(agent, "Preserved independent fact")
            with sqlite_connection(paths.database) as connection:
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "v2_0019"
                assert not TABLES & {
                    row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                assert not connection.execute("PRAGMA foreign_key_check").fetchall()
                return rows(connection), connection.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
                ).fetchall()
        finally:
            engine.dispose()


def assert_schema(unit):
    reflection = inspect(unit.connection)
    assert len(s.metadata.tables) == 125 and set(s.metadata.tables) <= set(reflection.get_table_names())
    for name in TABLES:
        table = s.metadata.tables[name]
        assert {column["name"]: column["nullable"] for column in reflection.get_columns(name)} == {
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
        assert {
            item["name"]: (tuple(item["column_names"]), bool(item["unique"])) for item in reflection.get_indexes(name)
        } == {
            index.name: (tuple(column.name for column in index.columns), bool(index.unique)) for index in table.indexes
        }
    pointer = next(
        item
        for item in reflection.get_foreign_keys("action_review_heads")
        if item["referred_table"] == "action_reviews"
    )
    assert pointer["options"]["deferrable"] is True and pointer["options"]["initially"] == "DEFERRED"
    assert "uq_actions_run_identity" in {item["name"] for item in reflection.get_indexes("actions")}
    assert (
        "AUTOINCREMENT"
        in unit.connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE name='action_review_heads'"
        ).scalar_one()
    )
    assert {"proof", "evidence_sha256"} <= set(s.action_reviews.c.keys()) and "body" not in s.action_reviews.c
    assert (s.action_reviews, s.action_reviews.c.review_audit_id) in s.AUDIT_REFERENCES
    triggers = {
        row[0] for row in unit.connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='trigger'")
    }
    assert {
        "action_unknown_origin_identity",
        "action_review_head_birth",
        "action_review_head_identity",
        "action_review_birth",
        "action_review_evidence_birth",
        "action_review_evidence_monotone",
        "action_review_body_changed",
        "action_review_source_erased",
        "action_reviews_immutable",
        *(name + "_keep" for name in TABLES),
    } <= triggers


@pytest.mark.parametrize(
    "marker",
    [
        "CREATE TABLE action_reviews",
        "CREATE INDEX ix_action_reviews_review_audit",
        "CREATE TRIGGER action_review_evidence_keep",
    ],
    ids=["table", "index", "trigger"],
)
def test_v20_process_exit_rolls_back_ddl_without_changing_v19_business_then_retries(tmp_path, marker):
    paths = InstancePaths.resolve(tmp_path / "instance")
    original, schema = baseline(paths)
    script = tmp_path / "interrupt_v20.py"
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
    if statement.strip().startswith(sys.argv[2]): os._exit(92)
Database(paths.database,SystemClock(),UUIDs(),objects_root=paths.objects)
raise AssertionError('Requested v20 interruption did not occur')
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
    assert child.returncode == 92, child.stderr
    with owned(paths):
        with sqlite_connection(paths.database) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "v2_0019"
            assert (
                connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
                == schema
            )
            assert rows(connection) == original
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

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
            assert rows(connection) == original
    snapshots = list(paths.database.parent.glob("schema-backups/*"))
    assert len(snapshots) == 2
    for snapshot in snapshots:
        report = verify_snapshot(snapshot)
        assert report["complete"] and report["database"]["schema"] == "v2_0019"
        assert report["database"]["integrity"] == "ok" and not report["database"]["foreign_key_errors"]


def test_v20_backup_failure_leaves_old_schema_rows_and_unknown_outcome_untouched(tmp_path, monkeypatch):
    paths = InstancePaths.resolve(tmp_path / "instance")
    original, schema = baseline(paths)

    def fail(*_args, **_kwargs):
        raise BusinessError("isolated_backup_failed", "Isolated snapshot failure", status=503)

    monkeypatch.setattr(maintenance, "migration_snapshot", fail)
    with owned(paths):
        with pytest.raises(BusinessError) as failure:
            Database(paths.database, SystemClock(), UUIDs(), objects_root=paths.objects)
        assert failure.value.code == "isolated_backup_failed"
        with sqlite_connection(paths.database) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "v2_0019"
            assert rows(connection) == original
            assert (
                connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
                == schema
            )
            assert not connection.execute("PRAGMA foreign_key_check").fetchall()


async def reviewed(database, agent, decision="not_applied"):
    action = await database.write(lambda u: seed_unknown(u, agent))
    detail = await database.write(lambda u: u.action_review_get(agent, action["action_id"]))
    request = review_request(detail, decision, "Owner evidence can be erased independently")
    result = await database.write(lambda u: u.action_review_commit(agent, action["action_id"], request))
    return action, detail, request, result


def test_v20_metadata_fks_and_revisions_are_exact_evidence_can_only_be_erased_once(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, database, _, agent):
            await database.read(assert_schema)
            action, _, request, result = await reviewed(database, agent)
            head = result["head"]
            review = await database.read(lambda u: u._one(s.action_reviews, agent, request["review_id"]))
            evidence = await database.read(
                lambda u: dict(u.connection.execute(select(s.action_review_evidence)).mappings().one())
            )
            assert "private source canary" not in json.dumps(review["proof"]) and "argument canary" not in json.dumps(
                review["proof"]
            )
            for table, row in (
                (s.action_review_heads, head),
                (s.action_reviews, review),
                (s.action_review_evidence, evidence),
            ):
                with pytest.raises(BusinessError) as failure:
                    await database.write(
                        lambda u, table=table, row=row: u.connection.execute(
                            delete(table).where(table.c.id == row["id"])
                        )
                    )
                assert failure.value.code == "data_constraint"
            for table, row, values in (
                (s.action_review_heads, head, {"revision": head["revision"]}),
                (s.action_reviews, review, {"decision": "applied"}),
                (s.action_review_evidence, evidence, {"body": "Different owner text"}),
            ):
                with pytest.raises(BusinessError) as failure:
                    await database.write(
                        lambda u, table=table, row=row, values=values: u.connection.execute(
                            update(table).where(table.c.id == row["id"]).values(**values)
                        )
                    )
                assert failure.value.code == "data_constraint"
            with pytest.raises(BusinessError) as failure:
                await database.write(
                    lambda u: u.connection.execute(delete(s.audit).where(s.audit.c.id == review["review_audit_id"]))
                )
            assert failure.value.code == "data_constraint"
            await database.write(
                lambda u: u.connection.execute(
                    update(s.action_review_evidence)
                    .where(s.action_review_evidence.c.id == evidence["id"])
                    .values(body="", erased_at=u.clock.now())
                )
            )
            with pytest.raises(BusinessError) as failure:
                await database.write(
                    lambda u: u.connection.execute(
                        update(s.action_review_evidence)
                        .where(s.action_review_evidence.c.id == evidence["id"])
                        .values(body=evidence["body"], erased_at=None)
                    )
                )
            assert failure.value.code == "data_constraint"
            after = await database.read(lambda u: u._one(s.actions, agent, action["action_id"]))
            assert (after["state"], after["effect"]) == ("unknown", "unknown")
            assert (await database.integrity())["foreign_keys"] == 0

    asyncio.run(scenario())


def test_unknown_origin_stays_frozen_but_source_redaction_invalidates_and_recovery_still_works(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, database, _, agent):
            action, _, request, result = await reviewed(database, agent)
            original = await database.read(lambda u: u._one(s.actions, agent, action["action_id"]))
            for values in (
                {"state": "error", "effect": "none"},
                {"arguments_sha256": "b" * 64},
                {"tool": "other"},
                {"ended_at": "2000-01-01T00:00:00+00:00"},
            ):
                with pytest.raises(BusinessError) as failure:
                    await database.write(
                        lambda u, values=values: u.connection.execute(
                            update(s.actions).where(s.actions.c.id == action["action_id"]).values(**values)
                        )
                    )
                assert failure.value.code == "data_constraint"
            await database.write(
                lambda u: u.connection.execute(
                    update(s.actions).where(s.actions.c.id == action["action_id"]).values(arguments={}, result=None)
                )
            )
            head = await database.read(lambda u: u._one(s.action_review_heads, agent, result["head"]["id"]))
            assert head["state"] == "invalidated" and head["revision"] == result["head"]["revision"] + 1
            evidence = await database.read(
                lambda u: dict(
                    u.connection.execute(
                        select(s.action_review_evidence).where(
                            s.action_review_evidence.c.review_id == request["review_id"]
                        )
                    )
                    .mappings()
                    .one()
                )
            )
            assert (
                evidence["body"] == ""
                and evidence["erased_at"]
                and evidence["body_sha256"] == result["review"]["evidence_sha256"]
            )
            after = await database.read(lambda u: u._one(s.actions, agent, action["action_id"]))
            assert after["state"] == original["state"] and after["effect"] == original["effect"]

            def interrupted(unit):
                pending = seed_unknown(unit, agent, terminal=False)
                unit.connection.execute(
                    update(s.actions)
                    .where(s.actions.c.id == pending["action_id"])
                    .values(state="executing", effect="unknown", ended_at=None)
                )
                unit.transition(agent, pending["run_id"], "interrupted")
                return pending

            pending = await database.write(interrupted)
            await database.write(lambda u: u.recover())
            recovered = await database.read(lambda u: u._one(s.actions, agent, pending["action_id"]))
            assert recovered["state"] == recovered["effect"] == "unknown" and recovered["ended_at"]

    asyncio.run(scenario())


def test_restore_invalidates_current_resolution_keeps_observation_and_evidence_history_and_is_repeatable(tmp_path):
    async def seed():
        async with opened(tmp_path / "instance", Clock()) as (paths, database, _, agent):
            action, _, request, result = await reviewed(database, agent)
            with sqlite_connection(paths.database) as connection:
                frozen = rows(connection, ("actions", "action_reviews", "action_review_evidence", "model_calls"))
            return paths, agent, action, request, result, frozen

    paths, agent, action, request, result, frozen = asyncio.run(seed())
    before = inspect_database(paths.database)
    assert before["unknown_actions"] == 1 and before["action_review_attention"] == 0
    snapshot, destination = tmp_path / "snapshot", tmp_path / "restored"
    backup(paths, snapshot)
    report = restore(snapshot, destination)
    assert report["action_reviews_quarantined"] is True and report["quarantine"]["invalidated_action_review_heads"] == 1
    assert report["database"]["unknown_actions"] == 1 and report["database"]["action_review_attention"] == 1
    assert report["quarantine"]["history_epoch"] != request["history_epoch"]
    database_path = destination / "data/omni.db"
    with sqlite_connection(database_path) as connection:
        assert rows(connection, tuple(frozen)) == frozen
        assert connection.execute(
            "SELECT revision,state,resolution,last_review_id,history_epoch FROM action_review_heads"
        ).fetchone() == (
            result["head"]["revision"] + 1,
            "invalidated",
            None,
            None,
            report["quarantine"]["history_epoch"],
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM audit WHERE kind='action_review.restored_invalidated'").fetchone()[
                0
            ]
            == 1
        )
        unchanged = rows(connection, ("action_review_heads", "action_reviews", "action_review_evidence", "audit"))
    assert restore(snapshot, destination)["already_restored"] is True
    with sqlite_connection(database_path) as connection:
        assert rows(connection, tuple(unchanged)) == unchanged

    async def read_restored():
        async with opened(destination, Clock()) as (_, database, _, _):
            current = await database.write(lambda u: u.action_review_get(agent, action["action_id"]))
            assert not current["review_valid"] and current["resolution"] is None
            assert current["history"][0]["evidence"] == request["evidence"]
            with pytest.raises(BusinessError) as failure:
                await database.write(
                    lambda u: u.action_review_commit(
                        agent, action["action_id"], {**request, "review_id": UUIDs().new()}
                    )
                )
            assert failure.value.code == "action_review_conflict"

    asyncio.run(read_restored())
