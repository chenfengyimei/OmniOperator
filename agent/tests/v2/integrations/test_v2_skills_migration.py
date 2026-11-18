"""V34 upgrade adds empty skill authority and rolls back interrupted frozen DDL."""

import asyncio

import pytest
from alembic import command
from omni_test_support.v2.v2_configuration_helpers import add_model_headers_column, model_uow
from omni_test_support.v2.v2_legacy_seed import PreSkillSeedRepositories
from sqlalchemy import select

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.database import Database
from omni.adapters.persistence.migration_config import configuration
from omni.adapters.persistence.migrations.versions.v0035 import SQL
from omni.adapters.persistence.sqlalchemy_access import database_engine
from omni.adapters.primitives import SystemClock, UUIDs


@pytest.mark.parametrize("interrupted", [False, True])
def test_v34_upgrade_does_not_invent_skill_grants_or_lose_unknown_cost(tmp_path, interrupted):
    path, clock, ids = tmp_path / "omni.db", SystemClock(), UUIDs()
    engine = database_engine(path)
    try:
        with engine.connect() as connection:
            cfg = configuration()
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "v2_0034")
            add_model_headers_column(connection)
            unit = PreSkillSeedRepositories(connection, clock, ids, ids.new())
            unit.initialize()
            agent = connection.execute(select(s.agents.c.id)).scalar_one()
            model_uow(unit, agent)
            turn = unit.accept_turn(agent, None, "old", "Original request", {})
            unit.transition(agent, turn["run_id"], "running")
            call = unit.reserve_call(agent, turn["run_id"], 123, 10000, "fixture")
            connection.commit()
        if interrupted:
            with pytest.raises(RuntimeError), engine.begin() as connection:
                for sql in SQL[: len(SQL) // 2]:
                    connection.exec_driver_sql(sql)
                raise RuntimeError("interrupted")
            with engine.connect() as connection:
                assert "skill_capture_epoch" not in {
                    r[1] for r in connection.exec_driver_sql("PRAGMA table_info(runs)")
                }
                assert not connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE name='skills'").all()
    finally:
        engine.dispose()

    async def scenario():
        db = Database(path, clock, ids)
        try:
            await db.write(lambda u: u.recover())
            assert db.schema_version == "v2_0039" and len(s.metadata.tables) == 136
            assert (await db.read(lambda u: u._one(s.model_calls, agent, call)))["state"] == "unknown"
            report = await db.read(lambda u: u.skill_run_report(agent, turn["run_id"], "windows"))
            assert not report["captured"] and not report["items"]
            assert not await db.read(lambda u: u.connection.execute(select(s.skills)).all())
            assert not (await db.integrity())["foreign_keys"]
        finally:
            await db.close()

    asyncio.run(scenario())
