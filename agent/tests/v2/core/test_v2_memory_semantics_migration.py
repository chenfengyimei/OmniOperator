"""Populated V33 upgrade, transactional DDL rollback and recovery of derived work."""

import asyncio

import pytest
from alembic import command
from omni_test_support.v2.v2_configuration_helpers import add_model_headers_column, model_uow
from omni_test_support.v2.v2_legacy_seed import PreSkillSeedRepositories
from sqlalchemy import select

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.database import Database
from omni.adapters.persistence.migration_config import configuration
from omni.adapters.persistence.migrations.versions.v0034 import SQL
from omni.adapters.persistence.sqlalchemy_access import database_engine
from omni.adapters.primitives import SystemClock, UUIDs


@pytest.mark.parametrize("interrupted", [False, True])
def test_v33_upgrade_keeps_memory_models_and_unknown_cost_without_inventing_vectors(tmp_path, interrupted):
    path, clock, ids = tmp_path / "omni.db", SystemClock(), UUIDs()
    engine = database_engine(path)
    try:
        with engine.connect() as connection:
            cfg = configuration()
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "v2_0033")
            add_model_headers_column(connection)
            unit = PreSkillSeedRepositories(connection, clock, ids, ids.new())
            unit.initialize()
            agent = connection.execute(select(s.agents.c.id)).scalar_one()
            model_uow(unit, agent)
            memory = unit.remember(agent, "Persistent original")
            turn = unit.accept_turn(agent, None, "old", "Original request", {})
            unit.transition(agent, turn["run_id"], "running")
            call = unit.reserve_call(agent, turn["run_id"], 123, 10000, "fixture")
            connection.commit()
        if interrupted:
            with pytest.raises(RuntimeError, match="interrupted"), engine.begin() as connection:
                for statement in SQL[: len(SQL) // 2]:
                    connection.exec_driver_sql(statement)
                raise RuntimeError("interrupted")
            with engine.connect() as connection:
                assert not connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE name='memory_embeddings'"
                ).all()
    finally:
        engine.dispose()

    async def scenario():
        database = Database(path, clock, ids)
        try:
            await database.write(lambda u: u.recover())
            assert database.schema_version == "v2_0051" and len(s.metadata.tables) == 147
            assert (await database.read(lambda u: u.search_memory(agent, "Persistent")))[0]["id"] == memory["id"]
            assert (await database.read(lambda u: u._one(s.model_calls, agent, call)))["state"] == "unknown"
            assert not await database.read(lambda u: u.connection.execute(select(s.memory_embeddings)).all())
            assert not (await database.integrity())["foreign_keys"]
        finally:
            await database.close()

    asyncio.run(scenario())
