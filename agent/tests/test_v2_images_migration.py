"""Populated V31 upgrade and crash recovery keep the existing message and money ledgers."""

import asyncio

from alembic import command
from sqlalchemy import select
from v2_configuration_helpers import model_uow
from v2_legacy_seed import PreRoutingSeedRepositories

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.database import Database
from omni.adapters.persistence.migration_config import configuration
from omni.adapters.persistence.sqlalchemy_access import database_engine
from omni.adapters.primitives import SystemClock, UUIDs


def test_populated_v31_upgrade_preserves_old_running_call_without_fabricating_images(tmp_path):
    path = tmp_path / "omni.db"
    clock, ids = SystemClock(), UUIDs()
    engine = database_engine(path)
    try:
        with engine.connect() as connection:
            cfg = configuration()
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "v2_0031")
            connection.commit()
            u = PreRoutingSeedRepositories(connection, clock, ids, ids.new())
            u.initialize()
            agent = connection.execute(select(s.agents.c.id)).scalar_one()
            model_uow(u, agent)
            receipt = u.accept_turn(agent, None, "old", "Original text", {})
            u.claim_runs(1)
            call = u.reserve_call(agent, receipt["run_id"], 1234, 10000, "fake")
            connection.commit()
    finally:
        engine.dispose()

    async def scenario():
        db = Database(path, clock, ids)
        try:
            await db.write(lambda u: u.recover())
            assert db.schema_version == "v2_0035"
            assert (await db.read(lambda u: u.messages(agent, receipt["session_id"])))[0]["content"] == "Original text"
            row = await db.read(lambda u: u._one(s.model_calls, agent, call))
            assert row["state"] == "unknown" and row["cost_micro"] is None
            assert not await db.read(lambda u: u.connection.execute(select(s.model_call_images.c.id)).all())
            assert await db.read(lambda u: u.connection.exec_driver_sql("PRAGMA foreign_key_check").all()) == []
        finally:
            await db.close()

    asyncio.run(scenario())
