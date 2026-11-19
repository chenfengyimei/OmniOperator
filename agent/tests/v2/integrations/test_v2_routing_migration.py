"""Existing V32 snapshots retain exact models and unknown charges without invented historical routing savings."""

import asyncio

from alembic import command
from omni_test_support.v2.v2_configuration_helpers import add_model_headers_column, model_uow
from omni_test_support.v2.v2_legacy_seed import PreRoutingSeedRepositories
from sqlalchemy import select

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.database import Database
from omni.adapters.persistence.migration_config import configuration
from omni.adapters.persistence.sqlalchemy_access import database_engine
from omni.adapters.primitives import SystemClock, UUIDs


def test_populated_v32_upgrade_backfills_honest_baseline_and_preserves_unknown(tmp_path):
    path, clock, ids = tmp_path / "omni.db", SystemClock(), UUIDs()
    engine = database_engine(path)
    try:
        with engine.connect() as connection:
            config = configuration()
            config.attributes["connection"] = connection
            command.upgrade(config, "v2_0032")
            add_model_headers_column(connection)
            unit = PreRoutingSeedRepositories(connection, clock, ids, ids.new())
            unit.initialize()
            agent = connection.execute(select(s.agents.c.id)).scalar_one()
            model_uow(unit, agent)
            receipt = unit.accept_turn(agent, None, "old", "original", {})
            unit.claim_runs(1)
            call = unit.reserve_call(agent, receipt["run_id"], 123, 10000, "fake")
            before = unit.get_run(agent, receipt["run_id"])["model_revision_id"]
            connection.commit()
    finally:
        engine.dispose()

    async def scenario():
        db = Database(path, clock, ids)
        try:
            await db.write(lambda u: u.recover())
            report = await db.read(lambda u: u.route_report(agent, receipt["run_id"]))
            assert db.schema_version == "v2_0039" and len(s.metadata.tables) == 140
            assert report["route"]["model_revision_id"] == before and report["route"]["source"] == "baseline"
            assert not report["calls"] and report["byte_delta"] == 0 and report["unsettled_calls"] == 1
            assert (await db.read(lambda u: u._one(s.model_calls, agent, call)))["state"] == "unknown"
            assert await db.read(lambda u: u.connection.exec_driver_sql("PRAGMA foreign_key_check").all()) == []
        finally:
            await db.close()

    asyncio.run(scenario())
