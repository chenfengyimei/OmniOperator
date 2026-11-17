"""V23 migration is atomic; existing model runs cannot acquire owner execution authority."""

from v2_configuration_helpers import add_model_headers_column
import asyncio

import pytest
from alembic import command
from sqlalchemy import create_engine, event, select, update
from test_v2_extensions_migration import raw_run

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.database import Database
from omni.adapters.persistence.migration_config import configuration
from omni.adapters.persistence.repository import Repositories
from omni.adapters.persistence.sqlite_access import sqlite_connection
from omni.adapters.primitives import SystemClock, UUIDs
from omni.domain.contracts import BusinessError


def test_v23_interrupted_ddl_rolls_back_then_preserves_model_run_with_new_kind(tmp_path):
    path = tmp_path / "omni.db"
    engine = create_engine("sqlite+pysqlite:///" + str(path))

    @event.listens_for(engine, "connect")
    def connect(connection, _):
        connection.isolation_level = None
        connection.execute("PRAGMA foreign_keys=ON")

    @event.listens_for(engine, "begin")
    def begin(connection):
        connection.exec_driver_sql("BEGIN IMMEDIATE")

    try:
        with engine.begin() as connection:
            cfg = configuration()
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "v2_0022")
            add_model_headers_column(connection)
            unit = Repositories(connection, SystemClock(), UUIDs())
            unit.initialize()
            agent = unit.query("agents", "")[0]["id"]
            run = raw_run(unit, agent)

        def interrupt(_connection, _cursor, statement, *_):
            if statement.lstrip().startswith("CREATE TABLE owner_commands"):
                raise RuntimeError("injected migration interruption")

        event.listen(engine, "before_cursor_execute", interrupt)
        with pytest.raises(RuntimeError, match="injected"), engine.begin() as connection:
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "head")
        event.remove(engine, "before_cursor_execute", interrupt)
        with sqlite_connection(path) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "v2_0022"
            assert "execution_kind" not in {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
            assert not connection.execute("SELECT name FROM sqlite_master WHERE name='owner_commands'").fetchall()
    finally:
        engine.dispose()

    async def upgraded():
        db = Database(path, SystemClock(), UUIDs())
        try:
            stored = await db.read(lambda u: u.get_run(agent, run["id"]))
            assert stored["execution_kind"] == "model" and stored["model_revision_id"] == run["model_revision_id"]
            assert await db.read(lambda u: u.connection.execute(select(s.owner_commands)).first()) is None
            with pytest.raises(BusinessError) as error:
                await db.write(
                    lambda u: u.connection.execute(
                        update(s.runs).where(s.runs.c.id == run["id"]).values(execution_kind="owner")
                    )
                )
            assert error.value.code == "data_constraint"
        finally:
            await db.close()

    asyncio.run(upgraded())
