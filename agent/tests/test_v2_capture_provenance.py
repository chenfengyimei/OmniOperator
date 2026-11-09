"""Real storage and V24 rollback preserve capture references, provenance and erasure guards."""

import asyncio
import uuid
from dataclasses import replace

import pytest
from alembic import command
from sqlalchemy import create_engine, event, update
from test_v2_desktop import prepared
from v2_desktop_helpers import captured_frame

from omni.adapters.object_store import FileObjects
from omni.adapters.persistence import schema as s
from omni.adapters.persistence.database import Database
from omni.adapters.persistence.migration_config import configuration
from omni.adapters.persistence.migration_config import head as migration_head
from omni.adapters.persistence.repository import Repositories
from omni.adapters.persistence.sqlite_access import sqlite_connection
from omni.adapters.primitives import SystemClock, UUIDs
from omni.domain.contracts import BusinessError


@pytest.mark.parametrize("method", ["print_window", "screen_capture_kit", "x11_composite", "pipewire_window"])
def test_each_native_capture_provenance_publishes_and_erases_through_same_object_service(tmp_path, method):
    async def scenario():
        async with prepared(tmp_path) as (instance, _backend, _clock, agent):

            async def guard():
                return None

            turn = await instance._database.write(lambda u: u.accept_turn(agent, None, "capture", "fixture", {}))
            result = await instance.objects.publish_capture(
                agent,
                turn["session_id"],
                1,
                str(uuid.uuid4()),
                replace(captured_frame(), method=method),
                before_publish=guard,
            )
            assert (
                result["method"] == method
                and (await instance.objects.describe(agent, result["object_id"]))["references"] == 1
            )
            await instance._database.write(
                lambda u: u.connection.execute(
                    update(s.sessions).where(s.sessions.c.id == turn["session_id"]).values(deleted_at=u.clock.now())
                )
            )
            assert (await instance.objects.describe(agent, result["object_id"]))["references"] == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("baseline", ["v2_0023", "v2_0024"])
def test_capture_rebuild_interruption_preserves_rows_and_guards(tmp_path, baseline):
    path = tmp_path / "omni.db"
    engine = create_engine("sqlite+pysqlite:///" + str(path))

    @event.listens_for(engine, "connect")
    def connect(connection, _):
        connection.isolation_level = None
        connection.execute("PRAGMA foreign_keys=ON")

    @event.listens_for(engine, "begin")
    def begin(connection):
        connection.exec_driver_sql("BEGIN IMMEDIATE")

    cfg = configuration()
    try:
        with engine.begin() as connection:
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, baseline)
            unit = Repositories(connection, SystemClock(), UUIDs())
            unit.initialize()
            agent = unit.query("agents", "")[0]["id"]
            turn = unit.accept_turn(agent, None, "capture", "fixture", {})
            frame = replace(captured_frame(), method="screen_capture_kit" if baseline == "v2_0024" else "print_window")
            stored = unit.capture_attach(
                FileObjects(tmp_path / "objects").put(agent, frame.content),
                turn["session_id"],
                1,
                str(uuid.uuid4()),
                frame,
                "2099-01-01T00:00:00+00:00",
            )

        def interrupt(_connection, _cursor, statement, *_):
            if statement.startswith("ALTER TABLE _alembic_tmp_session_captures RENAME"):
                raise RuntimeError("injected v24 interruption")

        event.listen(engine, "before_cursor_execute", interrupt)
        with pytest.raises(RuntimeError, match="injected"), engine.begin() as connection:
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "head")
        event.remove(engine, "before_cursor_execute", interrupt)
        with sqlite_connection(path) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == baseline
            assert connection.execute("SELECT id FROM session_captures").fetchone()[0] == stored["id"]
            assert (
                len(
                    connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'capture_%'"
                    ).fetchall()
                )
                == 3
            )
    finally:
        engine.dispose()

    async def upgraded():
        db = Database(path, SystemClock(), UUIDs(), objects_root=tmp_path / "objects")
        try:
            assert db.schema_version == migration_head()
            assert (await db.read(lambda u: u.capture_page(agent, turn["session_id"], "", 10)))[0]["id"] == stored["id"]
            with pytest.raises(BusinessError):
                await db.write(
                    lambda u: u.connection.execute(update(s.session_captures).values(method="screen_capture_kit"))
                )
            assert (await db.integrity())["foreign_keys"] == 0
        finally:
            await db.close()

    asyncio.run(upgraded())
