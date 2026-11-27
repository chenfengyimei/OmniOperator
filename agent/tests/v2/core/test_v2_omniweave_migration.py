"""The additive OWA revision preserves V2 data and enforces stored scope."""

import asyncio
import sqlite3
from uuid import uuid4

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from omni_test_support.v2.v2_configuration_helpers import model_uow
from sqlalchemy import select

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.database import Database
from omni.adapters.persistence.maintenance import verify_snapshot
from omni.adapters.persistence.migration_config import configuration
from omni.adapters.persistence.repository import Repositories
from omni.adapters.persistence.sqlalchemy_access import database_engine
from omni.adapters.persistence.sqlite_access import sqlite_connection
from omni.adapters.primitives import SystemClock, UUIDs


async def _verify_current_schema(database):
    script = ScriptDirectory.from_config(configuration())
    assert script.get_heads() == ["v2_0062"] and len(list(script.walk_revisions())) == 62
    assert database.schema_version == "v2_0062"
    integrity = await database.integrity()
    assert integrity["integrity"] == "ok" and integrity["foreign_keys"] == 0
    assert len(s.metadata.tables) == 165
    assert {
        "owa_threads",
        "owa_tickets",
        "owa_triggers",
        "owa_executions",
        "owa_tasks",
        "owa_dependencies",
        "owa_mandates",
        "owa_task_attempts",
        "owa_peer_links",
        "owa_peer_requests",
        "owa_artifact_links",
        "owa_artifacts",
        "owa_artifact_grants",
        "owa_deliveries",
    } <= set(integrity["tables"])


@pytest.mark.parametrize("revision", [f"v2_{number:04}" for number in range(53, 62)])
def test_upgrade_of_old_test_copy_preserves_admission_and_verified_backup(tmp_path, revision):
    has_main, has_mandate = revision >= "v2_0057", revision >= "v2_0058"
    original = tmp_path / "old.db"
    copy = tmp_path / "copy/omni.db"
    copy.parent.mkdir()
    engine = database_engine(original)
    clock, ids = SystemClock(), UUIDs()
    try:
        with engine.connect() as connection:
            connection.info["omni_write"] = True
            config = configuration()
            config.attributes["connection"] = connection
            command.upgrade(config, revision)
            unit = Repositories(connection, clock, ids, ids.new())
            owner = unit.initialize()["owner_id"]
            agent = connection.execute(select(s.agents.c.id)).scalar_one()
            model_uow(unit, agent)
            accepted = unit.accept_turn(agent, None, "pre-owa", "original request", {})
            if revision != "v2_0053":
                unit.omniweave_observe(
                    agent,
                    "pre-owa",
                    accepted["run_id"],
                    accepted["session_id"],
                    duplicate=False,
                    catalog={} if has_mandate else None,
                )
            if revision == "v2_0056":
                ticket = connection.execute(select(s.owa_tickets.c.id)).scalar_one()
                unit._record(s.owa_executions, {"ticket_id": ticket}, agent)
            if has_main:
                unit.omniweave_select_main(agent, accepted["session_id"])
            connection.commit()
    finally:
        engine.dispose()

    with sqlite_connection(original) as source, sqlite_connection(copy, read_only=False) as destination:
        source.backup(destination)

    async def scenario():
        database = Database(copy, clock, ids)
        try:
            await _verify_current_schema(database)
            selected = accepted["session_id"] if has_main else None
            assert await database.read(lambda u: u.omniweave_main_session(agent)) == selected
            if revision == "v2_0056":
                assert (await database.read(lambda u: u.omniweave_router(agent, accepted["run_id"])))[
                    "ticket_id"
                ] == ticket
            assert (await database.read(lambda u: u.get_run(agent, accepted["run_id"])))["state"] == "queued"
            assert await database.write(lambda u: u.accept_turn(agent, None, "pre-owa", "original request", {})) == {
                **accepted,
                "duplicate": True,
            }
            [snapshot] = list(copy.parent.joinpath("schema-backups").iterdir())
            verify_snapshot(snapshot)
            with (
                sqlite_connection(copy) as migrated,
                sqlite_connection(original) as old,
                sqlite_connection(snapshot / "data/omni.db") as backup,
            ):
                for source in (old, backup):
                    assert source.execute("SELECT version_num FROM alembic_version").fetchone() == (revision,)
                    observed = ("owa_tickets", "owa_triggers") if revision != "v2_0053" else ()
                    if observed:
                        statement = "SELECT id, agent_id, created_at, main_session_id, owner_id FROM owa_threads"
                        assert migrated.execute(statement).fetchall() == source.execute(statement).fetchall()
                    for table in ("inbox", "messages", "runs", "sessions", "work_items", *observed):
                        columns = "id, agent_id, created_at, thread_id, run_id" if table == "owa_tickets" else "*"
                        assert (
                            migrated.execute(f"SELECT {columns} FROM {table}").fetchall()
                            == source.execute(f"SELECT {columns} FROM {table}").fetchall()
                        )
                    if has_mandate:
                        assert (
                            migrated.execute("SELECT * FROM owa_mandates").fetchall()
                            == source.execute("SELECT * FROM owa_mandates").fetchall()
                        )
                assert migrated.execute("SELECT owner_id FROM instances").fetchone() == (owner,)
                uncreated = ("owa_threads", "owa_tickets", "owa_triggers") if revision == "v2_0053" else ()
                assert migrated.execute("SELECT count(*) FROM owa_executions").fetchone() == (
                    int(revision == "v2_0056"),
                )
                assert migrated.execute("SELECT count(*) FROM owa_mandates").fetchone() == (int(has_mandate),)
                if revision != "v2_0053":
                    assert migrated.execute("SELECT root_ticket_id, goal_version FROM owa_tickets").fetchall() == [
                        (None, 1)
                    ]
                for table in (
                    "owa_tasks",
                    "owa_dependencies",
                    "owa_task_attempts",
                    "owa_peer_links",
                    "owa_peer_requests",
                    "owa_deliveries",
                    *uncreated,
                ):
                    assert migrated.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)
        finally:
            await database.close()

    asyncio.run(scenario())


def test_fresh_database_rejects_cross_agent_and_cross_thread_bindings(tmp_path):
    path = tmp_path / "omni.db"
    clock, ids = SystemClock(), UUIDs()

    def seed(unit):
        owner = unit.initialize()["owner_id"]
        first = unit.connection.execute(select(s.agents.c.id)).scalar_one()
        second = unit.create_agent("Other Agent")["id"]
        model_uow(unit, first)
        model_uow(unit, second)
        matched = unit.accept_turn(first, None, "first", "valid", {})
        unit.omniweave_observe(first, "first", matched["run_id"], matched["session_id"], duplicate=False)
        other_thread = unit.accept_turn(first, None, "second", "other thread", {})
        other_agent = unit.accept_turn(second, None, "third", "other agent", {})
        return owner, first, second, other_thread, other_agent

    async def scenario():
        database = Database(path, clock, ids)
        try:
            await _verify_current_schema(database)
            return await database.write(seed)
        finally:
            await database.close()

    owner, first, second, other_thread, other_agent = asyncio.run(scenario())
    with sqlite_connection(path, read_only=False) as connection:
        thread = connection.execute("SELECT id FROM owa_threads").fetchone()[0]
        ticket = connection.execute("SELECT id FROM owa_tickets").fetchone()[0]
        stamp = clock.now()
        with pytest.raises(sqlite3.IntegrityError, match="invalid OWA main session binding"):
            connection.execute(
                "INSERT INTO owa_threads (id, agent_id, created_at, main_session_id, owner_id) VALUES (?, ?, ?, ?, ?)",
                (str(uuid4()), second, stamp, other_thread["session_id"], owner),
            )
        for agent, run in ((first, other_thread["run_id"]), (second, other_agent["run_id"])):
            with pytest.raises(sqlite3.IntegrityError, match="invalid OWA Ticket binding"):
                connection.execute(
                    "INSERT INTO owa_tickets (id, agent_id, created_at, thread_id, run_id) VALUES (?, ?, ?, ?, ?)",
                    (str(uuid4()), agent, stamp, thread, run),
                )
        inbox, digest = connection.execute("SELECT id, request_fingerprint FROM inbox WHERE key='second'").fetchone()
        with pytest.raises(sqlite3.IntegrityError, match="invalid OWA Trigger binding"):
            connection.execute(
                """INSERT INTO owa_triggers
                   (id, agent_id, created_at, ticket_id, inbox_id, source, version, payload_sha256, decision)
                   VALUES (?, ?, ?, ?, ?, 'owner_web', 1, ?, 'observe')""",
                (str(uuid4()), first, stamp, ticket, inbox, digest),
            )
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
