"""Real populated V28 upgrade preserves owner commands and enforces the new grant baseline."""

import asyncio
import uuid

import pytest
from alembic import command
from omni_test_support.v2.v2_configuration_helpers import add_model_headers_column
from omni_test_support.v2.v2_legacy_seed import PreSkillSeedRepositories
from sqlalchemy import select, update

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.database import Database
from omni.adapters.persistence.migration_config import configuration
from omni.adapters.persistence.migration_config import head as migration_head
from omni.adapters.persistence.sqlalchemy_access import database_engine
from omni.adapters.primitives import SystemClock, UUIDs
from omni.domain.contracts import BusinessError


def test_populated_v28_upgrade_retains_owner_command_body_and_scoped_schema(tmp_path):
    path = tmp_path / "omni.db"
    clock, ids = SystemClock(), UUIDs()
    engine = database_engine(path)
    try:
        with engine.connect() as connection:
            cfg = configuration()
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "v2_0028")
            add_model_headers_column(connection)
            connection.commit()
            u = PreSkillSeedRepositories(connection, clock, ids, str(uuid.uuid4()))
            u.initialize()
            agent = connection.execute(select(s.agents.c.id)).scalar_one()
            session = u._record(s.sessions, {"title": "Old owner command", "revision": 1, "next_seq": 0}, agent)
            receipt = u.accept_owner_command(
                agent,
                session["id"],
                str(uuid.uuid4()),
                "a" * 64,
                {"kind": "set_text", "text": "preserve this pending body"},
            )
            connection.commit()
    finally:
        engine.dispose()

    async def scenario():
        db = Database(path, clock, ids)
        try:
            row = await db.read(lambda u: u.owner_command(agent, receipt["run_id"]))
            assert row["tool"] == "desktop_semantic" and row["arguments"]["text"] == "preserve this pending body"
            assert db.schema_version == migration_head()
            assert await db.read(lambda u: u.connection.exec_driver_sql("PRAGMA foreign_key_check").all()) == []
            assert len(s.metadata.tables) == 161
        finally:
            await db.close()

    asyncio.run(scenario())


def test_restored_history_generation_never_reuses_old_device_authority(tmp_path):
    from omni_test_support.v2.android import prepared

    async def scenario():
        async with prepared(tmp_path) as (instance, _, agent, _, _, grant):
            await instance._database.write(
                lambda u: u.connection.execute(update(s.history_identity).values(epoch=str(uuid.uuid4())))
            )
            with pytest.raises(BusinessError, match="revoked or changed"):
                await instance.android.observe(agent, grant["id"])

    asyncio.run(scenario())


def test_v29_revoked_device_grant_survives_upgrade_without_model_access(tmp_path):
    from omni_test_support.v2.android import BOOT, SERIAL

    path = tmp_path / "omni.db"
    clock, ids = SystemClock(), UUIDs()
    engine = database_engine(path)
    try:
        with engine.connect() as connection:
            cfg = configuration()
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "v2_0029")
            add_model_headers_column(connection)
            connection.commit()
            u = PreSkillSeedRepositories(connection, clock, ids, ids.new())
            u.initialize()
            agent = connection.execute(select(s.agents.c.id)).scalar_one()
            config = u.configuration_create(
                agent,
                "channel",
                "Old Android grant",
                True,
                {"driver": "android", "endpoint": "http://127.0.0.1:5037", "account": SERIAL},
            )
            grant = u.android_grant(
                agent,
                config["id"],
                config["revision_id"],
                {"serial": SERIAL, "transport_id": 7, "boot_id": BOOT, "user_id": 0, "fingerprint": "fixture"},
            )
            u.android_revoke(agent, grant["id"], grant["revision"])
            connection.commit()
    finally:
        engine.dispose()

    async def scenario():
        db = Database(path, clock, ids)
        try:
            row = await db.read(lambda u: u._one(s.android_grants, agent, grant["id"]))
            assert row["revoked_at"] and row["revision"] == 2
            assert await db.read(lambda u: u.android_access_page(agent, "", 50)) == []
            with pytest.raises(BusinessError):
                await db.read(lambda u: u.android_authorize(agent, grant["id"]))
            assert await db.read(lambda u: u.connection.exec_driver_sql("PRAGMA foreign_key_check").all()) == []
        finally:
            await db.close()

    asyncio.run(scenario())
