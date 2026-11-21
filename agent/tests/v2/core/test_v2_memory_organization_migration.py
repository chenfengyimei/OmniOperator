"""V18 organization schema, source invalidation and restore isolation; no models, background workers or fact merging."""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
from itertools import chain

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
from omni.domain.contracts import BusinessError
from omni.domain.memory_organization import POLICY, request_fingerprint

NEW_TABLES = {"memory_organization_batches", "memory_organization_sources", "memory_organization_candidates"}
NEW_INDEXES = {
    "ix_memory_organization_batches_agent_id",
    "ix_memory_organization_batches_agent_sequence",
    "ix_memory_organization_sources_agent_id",
    "ix_memory_organization_sources_memory",
    "ix_memory_organization_candidates_agent_id",
    "ix_memory_organization_applied_keep",
    "ix_memory_organization_applied_duplicate",
}
NEW_TRIGGERS = {
    "memory_organization_batch_identity",
    "memory_organization_source_identity",
    "memory_organization_candidate_identity",
    "memory_organization_batch_keep",
    "memory_organization_source_keep",
    "memory_organization_candidate_keep",
    "memory_organization_memory_changed",
    "memory_organization_revision_changed",
    "memory_organization_anchor_inserted",
    "memory_organization_anchor_updated",
    "memory_organization_anchor_deleted",
    "memory_organization_person_deleted",
}
SOURCE_TABLES = (
    "instances",
    "agents",
    "people",
    "runs",
    "messages",
    "run_events",
    "audit",
    "history_identity",
    "history_sequences",
    "history_scopes",
    "memory_entries",
    "memory_revisions",
    "source_anchors",
    "tombstones",
    "tombstone_sources",
    "maintenance_jobs",
    "maintenance_attempts",
)


def source_rows(connection, tables=SOURCE_TABLES):
    return preserved_rows(connection, tables)


def baseline(paths):
    paths.initialize()
    with owned(paths):
        engine = create_engine("sqlite+pysqlite:///" + str(paths.database))
        try:
            with engine.begin() as connection:
                cfg = configuration()
                cfg.attributes["connection"] = connection
                command.upgrade(cfg, "v2_0017")
                add_model_headers_column(connection)
                unit = LegacySeedRepositories(connection, SystemClock(), UUIDs())
                unit.initialize()
                agent = unit.query("agents", "")[0]["id"]
                unit.accept_turn(agent, None, "v17-organization", "Preserved organization migration input", {})
                for _ in range(2):
                    unit.remember(agent, "Existing exact fact", kind="fact")
                # No current maintenance admission runs against this deliberately old schema.
            with sqlite_connection(paths.database) as connection:
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "v2_0017"
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
    assert len(s.metadata.tables) == 147 and set(s.metadata.tables) <= set(reflection.get_table_names())
    for name in NEW_TABLES:
        table = s.metadata.tables[name]
        columns = reflection.get_columns(name)
        assert {column["name"] for column in columns} == set(table.c.keys())
        assert not {"content", "quote", "object_id"} & set(table.c.keys())
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
        assert not {item["referred_table"] for item in reflection.get_foreign_keys(name)} & {
            "objects",
            "source_anchors",
        }
    ddl = unit.connection.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE name='memory_organization_batches'"
    ).scalar_one()
    assert "AUTOINCREMENT" in ddl.upper()
    indexes = {item["name"]: item for name in NEW_TABLES for item in reflection.get_indexes(name)}
    assert NEW_INDEXES <= set(indexes)
    assert indexes["ix_memory_organization_sources_memory"]["column_names"] == ["agent_id", "memory_id"]
    for name, column in (
        ("ix_memory_organization_applied_keep", "keep_source_id"),
        ("ix_memory_organization_applied_duplicate", "duplicate_source_id"),
    ):
        assert indexes[name]["column_names"] == ["agent_id", column]
        assert normalized_check(indexes[name]["dialect_options"]["sqlite_where"]) == "state='applied'"
    assert NEW_TRIGGERS <= {
        row[0] for row in unit.connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='trigger'")
    }


@pytest.mark.parametrize(
    "marker",
    [
        "CREATE TABLE memory_organization_candidates",
        "CREATE INDEX ix_memory_organization_applied_duplicate",
        "CREATE TRIGGER memory_organization_person_deleted",
    ],
    ids=["table", "index", "trigger"],
)
def test_v18_process_exit_preserves_v17_and_retries_from_verified_snapshots(tmp_path, marker):
    paths = InstancePaths.resolve(tmp_path / "instance")
    original, original_schema = baseline(paths)
    script = tmp_path / "interrupt_v18.py"
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
    if statement.strip().startswith(sys.argv[2]): os._exit(98)
Database(paths.database,SystemClock(),UUIDs(),objects_root=paths.objects)
raise AssertionError('Requested v18 interruption did not occur')
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
    assert child.returncode == 98, child.stderr
    with owned(paths):
        with sqlite_connection(paths.database) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "v2_0017"
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
        assert manifest["complete"] and manifest["database"]["schema"] == "v2_0017"
        assert manifest["database"]["integrity"] == "ok" and not manifest["database"]["foreign_key_errors"]


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def candidate_state(unit, candidate, state):
    current = unit._one(s.memory_organization_candidates, candidate["agent_id"], candidate["id"])
    values = {"state": state, "revision": current["revision"] + 1, "updated_at": unit.clock.now()}
    if state in {"applied", "rejected", "revoked"}:
        values.update(
            reviewed_at=unit.clock.now(),
            review_audit_id=unit.audit(
                current["agent_id"], "memory_organization.reviewed", current["id"], {"decision": state}
            ),
        )
    unit.connection.execute(
        update(s.memory_organization_candidates)
        .where(s.memory_organization_candidates.c.id == current["id"])
        .values(**values)
    )
    return unit._one(s.memory_organization_candidates, current["agent_id"], current["id"])


def organization(unit, *, agent=None, state="ready"):
    """DDL-valid organization relationships use real facts and exact current source revisions; no facts are changed."""
    unit.initialize()
    agent = agent or unit.query("agents", "")[0]["id"]
    person = unit.person_create(agent, "Source person")
    memories = sorted(
        [unit.remember(agent, "Exact raw fact\nSame bytes", person_id=person["id"]) for _ in range(3)],
        key=lambda row: row["id"],
    )
    epoch = unit.connection.execute(select(s.history_identity.c.epoch)).scalar_one()
    queued = state == "queued"
    request = {
        "key": UUIDs().new(),
        "sources": [{"memory_id": row["id"], "revision": row["revision"]} for row in memories],
    }
    row = unit._record(
        s.memory_organization_batches,
        {
            "request_key": request["key"],
            "request_sha256": request_fingerprint(request),
            "policy": POLICY,
            "history_epoch": epoch,
            "budget_micro": 0,
            "source_count": 3,
            "candidate_count": 0 if queued else 2,
            "state": "queued" if queued else "ready",
            "revision": 1,
            "updated_at": unit.clock.now(),
            "prepared_at": None if queued else unit.clock.now(),
            "error_code": "",
        },
        agent,
    )
    batch = unit._one(s.memory_organization_batches, agent, row["id"])
    sources = []
    for position, memory in enumerate(memories):
        version = (
            unit.connection.execute(
                select(s.memory_revisions).where(
                    s.memory_revisions.c.memory_id == memory["id"], s.memory_revisions.c.revision == memory["revision"]
                )
            )
            .mappings()
            .one()
        )
        anchors = [
            dict(anchor)
            for anchor in unit.connection.execute(
                select(s.source_anchors)
                .where(s.source_anchors.c.memory_revision_id == version["id"])
                .order_by(s.source_anchors.c.id)
            ).mappings()
        ]
        sources.append(
            unit._record(
                s.memory_organization_sources,
                {
                    "batch_id": batch["id"],
                    "memory_id": memory["id"],
                    "memory_revision_id": version["id"],
                    "revision": memory["revision"],
                    "position": position,
                    "raw_sha256": hashlib.sha256(memory["content"].encode()).hexdigest(),
                    "evidence_sha256": digest(anchors),
                    "person_id": memory["person_id"],
                    "session_id": memory.get("session_id"),
                },
                agent,
            )
        )
    candidates = []
    if not queued:
        for source in sources[1:]:
            candidates.append(
                unit._record(
                    s.memory_organization_candidates,
                    {
                        "batch_id": batch["id"],
                        "keep_source_id": sources[0]["id"],
                        "duplicate_source_id": source["id"],
                        "state": "pending",
                        "revision": 1,
                        "fingerprint": digest({"epoch": epoch, "keep": sources[0]["id"], "duplicate": source["id"]}),
                        "updated_at": unit.clock.now(),
                        "reviewed_at": None,
                        "review_audit_id": None,
                        "error_code": "",
                    },
                    agent,
                )
            )
    if state in {"complete", "cancelled"}:
        candidates = [
            candidate_state(unit, candidate, "applied" if state == "complete" or index == 0 else "cancelled")
            for index, candidate in enumerate(candidates)
        ]
        unit.connection.execute(
            update(s.memory_organization_batches)
            .where(s.memory_organization_batches.c.id == batch["id"])
            .values(state=state, revision=2, updated_at=unit.clock.now())
        )
        batch = unit._one(s.memory_organization_batches, agent, batch["id"])
    return {"batch": batch, "sources": sources, "candidates": candidates, "memories": memories, "person": person}


def snapshot(unit, batch_id):
    batch = dict(
        unit.connection.execute(
            select(s.memory_organization_batches).where(s.memory_organization_batches.c.id == batch_id)
        )
        .mappings()
        .one()
    )
    candidates = [
        dict(row)
        for row in unit.connection.execute(
            select(s.memory_organization_candidates)
            .where(s.memory_organization_candidates.c.batch_id == batch_id)
            .order_by(s.memory_organization_candidates.c.id)
        ).mappings()
    ]
    return batch, candidates


def test_v18_schema_has_83_tables_and_exact_identity_and_state_constraints(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, database, _, _):
            assert database.schema_version == migration_head()
            await database.read(assert_schema)
            case = await database.write(organization)
            batch, source, candidate = case["batch"], case["sources"][0], case["candidates"][0]

            async def denied(statement):
                with pytest.raises(BusinessError) as failure:
                    await database.write(lambda u: u.connection.execute(statement))
                assert failure.value.code == "data_constraint"

            for table, row in (
                (s.memory_organization_batches, batch),
                (s.memory_organization_sources, source),
                (s.memory_organization_candidates, candidate),
            ):
                await denied(delete(table).where(table.c.id == row["id"]))
            empty = {key: value for key, value in batch.items() if key != "sequence"}
            empty.update(
                id=UUIDs().new(), request_key=UUIDs().new(), state="queued", candidate_count=0, prepared_at=None
            )
            await database.write(lambda u: u.connection.execute(insert(s.memory_organization_batches).values(**empty)))
            await denied(delete(s.memory_organization_batches).where(s.memory_organization_batches.c.id == empty["id"]))
            charged = dict(empty, id=UUIDs().new(), request_key=UUIDs().new(), budget_micro=1)
            await denied(insert(s.memory_organization_batches).values(**charged))
            queued = await database.write(lambda u: organization(u, state="queued"))
            await denied(
                delete(s.memory_organization_sources).where(
                    s.memory_organization_sources.c.id == queued["sources"][0]["id"]
                )
            )
            await denied(
                update(s.memory_organization_sources)
                .where(s.memory_organization_sources.c.id == source["id"])
                .values(raw_sha256="c" * 64)
            )
            for values in ({"request_key": "different"}, {"history_epoch": UUIDs().new()}, {"source_count": 4}):
                await denied(
                    update(s.memory_organization_batches)
                    .where(s.memory_organization_batches.c.id == batch["id"])
                    .values(revision=batch["revision"] + 1, **values)
                )
            audit_id = await database.write(
                lambda u: u.audit(batch["agent_id"], "memory_organization.reviewed", candidate["id"], {})
            )
            await denied(
                update(s.memory_organization_candidates)
                .where(s.memory_organization_candidates.c.id == candidate["id"])
                .values(
                    state="applied",
                    revision=2,
                    reviewed_at=batch["created_at"],
                    review_audit_id=audit_id,
                    fingerprint="c" * 64,
                )
            )
            await denied(
                update(s.memory_organization_candidates)
                .where(s.memory_organization_candidates.c.id == candidate["id"])
                .values(state="revoked", revision=2, reviewed_at=batch["created_at"], review_audit_id=audit_id)
            )
            applied = await database.write(lambda u: candidate_state(u, candidate, "applied"))
            revoked = await database.write(lambda u: candidate_state(u, applied, "revoked"))
            await denied(
                update(s.memory_organization_candidates)
                .where(s.memory_organization_candidates.c.id == revoked["id"])
                .values(state="applied", revision=revoked["revision"] + 1)
            )
            assert await database.read(lambda u: u.connection.execute(select(s.model_calls)).all()) == []
            report = await database.integrity()
            assert report["integrity"] == "ok" and report["foreign_keys"] == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("state", ["rejected", "cancelled", "invalidated", "revoked"])
def test_terminal_candidate_decisions_never_return_to_applied(state, tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, database, _, _):
            case = await database.write(organization)
            candidate = case["candidates"][0]
            if state == "revoked":
                candidate = await database.write(lambda u: candidate_state(u, candidate, "applied"))
            candidate = await database.write(lambda u: candidate_state(u, candidate, state))
            audit_id = await database.write(
                lambda u: u.audit(candidate["agent_id"], "memory_organization.reviewed", candidate["id"], {})
            )
            with pytest.raises(BusinessError) as failure:
                await database.write(
                    lambda u: u.connection.execute(
                        update(s.memory_organization_candidates)
                        .where(s.memory_organization_candidates.c.id == candidate["id"])
                        .values(
                            state="applied",
                            revision=candidate["revision"] + 1,
                            reviewed_at=u.clock.now(),
                            review_audit_id=audit_id,
                        )
                    )
                )
            assert failure.value.code == "data_constraint"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "mutation",
    [
        "revision",
        "content",
        "deleted_at",
        "person_id",
        "session_id",
        "kind",
        "captured_revision",
        "anchor_insert",
        "anchor_update",
        "anchor_delete",
        "person_deleted",
    ],
)
def test_source_changes_invalidate_pending_and_applied_relationships(mutation, tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, database, _, _):
            case = await database.write(organization)
            await database.write(lambda u: candidate_state(u, case["candidates"][0], "applied"))
            before_batch, before_candidates = await database.read(lambda u: snapshot(u, case["batch"]["id"]))
            source, person = case["sources"][0], case["person"]

            def change(unit):
                if mutation == "captured_revision":
                    unit.connection.execute(
                        update(s.memory_revisions)
                        .where(s.memory_revisions.c.id == source["memory_revision_id"])
                        .values(content="")
                    )
                elif mutation.startswith("anchor_"):
                    anchor = dict(
                        unit.connection.execute(
                            select(s.source_anchors).where(s.source_anchors.c.memory_id == source["memory_id"])
                        )
                        .mappings()
                        .one()
                    )
                    if mutation == "anchor_insert":
                        unit._record(
                            s.source_anchors,
                            {
                                key: value
                                for key, value in anchor.items()
                                if key not in {"id", "created_at", "agent_id"}
                            },
                            source["agent_id"],
                        )
                    elif mutation == "anchor_update":
                        unit.connection.execute(
                            update(s.source_anchors)
                            .where(s.source_anchors.c.id == anchor["id"])
                            .values(quote="Changed historical evidence")
                        )
                    else:
                        unit.connection.execute(delete(s.source_anchors).where(s.source_anchors.c.id == anchor["id"]))
                elif mutation == "person_deleted":
                    unit.connection.execute(
                        update(s.people).where(s.people.c.id == person["id"]).values(deleted_at=unit.clock.now())
                    )
                else:
                    values = {
                        "revision": 2,
                        "content": "Changed raw fact",
                        "deleted_at": unit.clock.now(),
                        "kind": "note",
                    }
                    if mutation == "person_id":
                        value = unit.person_create(source["agent_id"], "Different person")["id"]
                    elif mutation == "session_id":
                        value = unit._record(
                            s.sessions, {"title": "Different scope", "revision": 1, "next_seq": 0}, source["agent_id"]
                        )["id"]
                    else:
                        value = values[mutation]
                    unit.connection.execute(
                        update(s.memory_entries)
                        .where(s.memory_entries.c.id == source["memory_id"])
                        .values(**{mutation: value})
                    )

            await database.write(change)
            after_batch, after_candidates = await database.read(lambda u: snapshot(u, case["batch"]["id"]))
            assert after_batch["state"] == "invalidated" and after_batch["revision"] == before_batch["revision"] + 1
            previous = {row["id"]: row for row in before_candidates}
            for row in after_candidates:
                assert row["state"] == "invalidated" and row["revision"] == previous[row["id"]]["revision"] + 1
                assert row["review_audit_id"] == previous[row["id"]]["review_audit_id"]
                assert row["fingerprint"] == previous[row["id"]]["fingerprint"]
            assert not (await database.integrity())["foreign_keys"]

    asyncio.run(scenario())


def test_moving_an_anchor_invalidates_both_old_and_new_source_batches(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, database, _, _):
            left = await database.write(organization)
            right = await database.write(organization)
            await database.write(lambda u: candidate_state(u, left["candidates"][0], "applied"))
            old, new = left["sources"][0], right["sources"][0]

            def move(unit):
                anchor_id = unit.connection.execute(
                    select(s.source_anchors.c.id).where(s.source_anchors.c.memory_id == old["memory_id"])
                ).scalar_one()
                unit.connection.execute(
                    update(s.source_anchors)
                    .where(s.source_anchors.c.id == anchor_id)
                    .values(memory_id=new["memory_id"], memory_revision_id=new["memory_revision_id"])
                )

            await database.write(move)
            for case in (left, right):
                batch, candidates = await database.read(lambda u, case=case: snapshot(u, case["batch"]["id"]))
                assert batch["state"] == "invalidated" and all(row["state"] == "invalidated" for row in candidates)
            assert not (await database.integrity())["foreign_keys"]

    asyncio.run(scenario())


def test_restore_cancels_queue_invalidates_ready_and_applied_relations_and_preserves_fact_truth(tmp_path):
    async def seed():
        async with opened(tmp_path / "instance", Clock()) as (paths, database, _, _):
            cases = [
                await database.write(lambda u, state=state: organization(u, state=state))
                for state in ("queued", "ready", "complete", "cancelled")
            ]
            with sqlite_connection(paths.database) as connection:
                facts = source_rows(
                    connection,
                    ("memory_entries", "memory_revisions", "source_anchors", "tombstones", "tombstone_sources"),
                )
            return paths, cases, facts

    paths, cases, facts = asyncio.run(seed())
    source, destination = tmp_path / "snapshot", tmp_path / "restored"
    backup(paths, source)
    verify_snapshot(source)
    report = restore(source, destination)
    assert report["memory_organization_quarantined"] and report["execution_quarantined"]
    counts = report["quarantine"]
    assert counts["cancelled_memory_organization_batches"] == 1
    assert counts["invalidated_memory_organization_batches"] == 3
    assert counts["invalidated_memory_organization_candidates"] == 5
    assert counts["history_epoch"] != cases[0]["batch"]["history_epoch"]
    with sqlite_connection(destination / "data/omni.db") as connection:
        assert source_rows(connection, tuple(facts)) == facts
        for index, case in enumerate(cases):
            state, revision, epoch = connection.execute(
                "SELECT state,revision,history_epoch FROM memory_organization_batches WHERE id=?",
                (case["batch"]["id"],),
            ).fetchone()
            assert state == ("cancelled" if index == 0 else "invalidated")
            assert revision == case["batch"]["revision"] + 1 and epoch == case["batch"]["history_epoch"]
            for candidate in case["candidates"]:
                state, revision, review_id = connection.execute(
                    "SELECT state,revision,review_audit_id FROM memory_organization_candidates WHERE id=?",
                    (candidate["id"],),
                ).fetchone()
                active = candidate["state"] in {"pending", "applied"}
                assert state == ("invalidated" if active else candidate["state"])
                assert revision == candidate["revision"] + int(active) and review_id == candidate["review_audit_id"]
        before = source_rows(connection, tuple(sorted(NEW_TABLES)))
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM audit WHERE kind LIKE 'memory_organization.restored_%'"
            ).fetchone()[0]
            == 4
        )
        assert connection.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0] == 0
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    assert json.loads((destination / "restore-report.json").read_text())["memory_organization_quarantined"] is True
    assert restore(source, destination)["already_restored"]
    with sqlite_connection(destination / "data/omni.db") as connection:
        assert source_rows(connection, tuple(sorted(NEW_TABLES))) == before
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM audit WHERE kind LIKE 'memory_organization.restored_%'"
            ).fetchone()[0]
            == 4
        )
