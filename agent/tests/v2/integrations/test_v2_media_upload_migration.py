"""Populated v38 media graph migration is transactional with foreign keys on, including interrupted upgrade rollback."""

import uuid

import pytest
from alembic import command
from omni_test_support.v2.media import VIDEO
from sqlalchemy import MetaData, Table, insert

from omni.adapters.persistence.migration_config import configuration
from omni.adapters.persistence.migrations.versions import v0039
from omni.adapters.persistence.repository import Repositories
from omni.adapters.persistence.sqlalchemy_access import database_engine
from omni.adapters.primitives import SystemClock, UUIDs
from omni.domain.media import source_row


def seed(connection):
    repository = Repositories(connection, SystemClock(), UUIDs())
    repository.initialize()
    agent = repository.query("agents", "")[0]["id"]
    config = repository.configuration_create(
        agent, "channel", "Fixture", True, {"driver": "bilibili", "account": "456"}
    )
    config = repository.configuration_get(agent, config["id"])
    source = Table("media_sources", MetaData(), autoload_with=connection)
    identity = str(uuid.uuid4())
    created = SystemClock().now()
    connection.execute(
        insert(source).values(
            id=identity, agent_id=agent, created_at=created, **source_row(config["id"], config["revision_id"], VIDEO)
        )
    )
    job = str(uuid.uuid4())
    connection.exec_driver_sql(
        "INSERT INTO media_jobs(id,agent_id,created_at,source_id,operation,state,error_code) VALUES(?,?,?,?,?,'queued','')",
        (job, agent, created, identity, "transcribe"),
    )
    connection.exec_driver_sql("UPDATE media_jobs SET state='running' WHERE id=?", (job,))
    obj = str(uuid.uuid4())
    connection.exec_driver_sql(
        "INSERT INTO objects(id,agent_id,created_at,sha256,bytes,media_type,pinned,revision) VALUES(?,?,?,?,?,?,0,1)",
        (obj, agent, created, "a" * 64, 12, "application/json"),
    )
    connection.exec_driver_sql(
        "INSERT INTO media_transcripts(id,agent_id,created_at,source_id,job_id,kind,language,object_id,bytes) VALUES(?,?,?,?,?,'asr','zh',?,12)",
        (str(uuid.uuid4()), agent, created, identity, job, obj),
    )
    connection.exec_driver_sql("UPDATE media_jobs SET state='done',finished_at=? WHERE id=?", (created, job))
    return {
        name: connection.exec_driver_sql("SELECT * FROM " + name).fetchall()
        for name in ("media_sources", "media_jobs", "media_transcripts", "media_frames")
    }


@pytest.mark.parametrize("interrupt", [False, True])
def test_populated_graph_rebuild_preserves_completed_artifact_and_rolls_back_every_ddl_on_failure(
    tmp_path, monkeypatch, interrupt
):
    engine = database_engine(tmp_path / "legacy.db")
    try:
        with engine.connect() as connection:
            cfg = configuration()
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "v2_0038")
            connection.commit()
            before = seed(connection)
            connection.commit()
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
            connection.commit()
            with monkeypatch.context() as patch:
                if interrupt:
                    execute = v0039.op.execute

                    def crash(statement, *args, **kwargs):
                        if statement.startswith("DROP TABLE _v39_media_jobs"):
                            raise RuntimeError("fixture-interruption")
                        return execute(statement, *args, **kwargs)

                    patch.setattr(v0039.op, "execute", crash)
                    with pytest.raises(RuntimeError, match="fixture-interruption"):
                        command.upgrade(cfg, "head")
                    connection.rollback()
                    assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "v2_0038"
                    for name, rows in before.items():
                        assert connection.exec_driver_sql("SELECT * FROM " + name).fetchall() == rows
                    assert not connection.exec_driver_sql(
                        "SELECT name FROM sqlite_master WHERE name LIKE '_v39_%'"
                    ).fetchall()
                    connection.commit()
            command.upgrade(cfg, "head")
            connection.commit()
            for name, rows in before.items():
                actual = connection.exec_driver_sql("SELECT * FROM " + name).fetchall()
                assert (
                    [tuple(row[:-1]) for row in actual] == [tuple(row) for row in rows]
                    if name == "media_sources"
                    else actual == rows
                )
            assert (
                connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
                and not connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            )
            assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "v2_0055"
    finally:
        engine.dispose()
