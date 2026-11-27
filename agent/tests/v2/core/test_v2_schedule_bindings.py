"""Scheduled skill bindings and task output references: bounds, save validation, immutability and frozen migration."""

from __future__ import annotations

import asyncio

import pytest
from alembic import command
from omni_test_support.v2.scheduling import interval, seed_skill, setup
from sqlalchemy import UniqueConstraint, delete, inspect, select, update

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.migration_config import configuration, head
from omni.adapters.persistence.repository import Repositories
from omni.adapters.persistence.sqlalchemy_access import database_engine
from omni.adapters.primitives import SystemClock, UUIDs
from omni.domain.contracts import BusinessError
from omni.domain.scheduling import MAX_CONTEXT_REFS, MAX_SKILLS, schedule_value

SKILL = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"


def rejected(value):
    with pytest.raises(BusinessError) as error:
        schedule_value(value)
    return error.value.code


def test_bindings_are_bounded_canonical_and_strict():
    value = schedule_value({"prompt": "p", "skills": [SKILL], "context_from": [OTHER], "continuity": True})
    assert value["skills"] == [SKILL] and value["context_from"] == [OTHER] and value["continuity"] is True
    assert schedule_value({"prompt": "p"})["continuity"] is False
    assert rejected({"prompt": "p", "skills": [SKILL, SKILL]}) == "invalid_skill"
    assert rejected({"prompt": "p", "skills": ["1111"]}) == "invalid_skill"
    assert rejected({"prompt": "p", "skills": [f"{n:08d}-1111-1111-1111-111111111111" for n in range(5)]}) == (
        "invalid_schedule"
    )
    assert rejected({"prompt": "p", "context_from": [SKILL, SKILL]}) == "invalid_context_ref"
    assert rejected({"prompt": "p", "context_from": ["1111"]}) == "invalid_context_ref"
    assert rejected({"prompt": "p", "context_from": [f"{n:08d}-1111-1111-1111-111111111111" for n in range(5)]}) == (
        "invalid_schedule"
    )
    assert rejected({"prompt": "p", "continuity": 1}) == "invalid_schedule"
    assert MAX_SKILLS == MAX_CONTEXT_REFS == 4


def test_bindings_belong_to_one_revision_and_are_immutable(tmp_path):
    async def scenario():
        db, agent, _, service = await setup(tmp_path)
        try:
            skill = await seed_skill(db, agent, "alpha-skill")
            source = await service.save(agent, "source", True, interval("source"))
            plan = await service.save(
                agent,
                "consumer",
                True,
                {**interval("consumer"), "skills": [skill["id"]], "context_from": [source["id"]], "continuity": True},
            )
            assert plan["value"]["skills"] == [skill["id"]]
            assert plan["value"]["context_from"] == [source["id"]]
            assert plan["value"]["continuity"] is True
            assert (await service.get(agent, source["id"]))["value"]["skills"] == []
            updated = await service.save(
                agent, "consumer", True, {**interval("consumer"), "continuity": True}, plan["id"], 1
            )
            assert updated["value"]["skills"] == [] and updated["value"]["context_from"] == []
            rows = await db.read(
                lambda u: [
                    (row["revision"], row["position"], row["skill_id"], row["revision_id"])
                    for row in u.connection.execute(
                        select(s.schedule_skills).where(s.schedule_skills.c.schedule_id == plan["id"])
                    ).mappings()
                ]
            )
            assert rows == [(1, 1, skill["id"], skill["revision_id"])]
            assert [row["continuity"] for row in await service.history(agent, plan["id"])] == [1, 1]
            with pytest.raises(BusinessError, match="constraint"):
                await db.write(
                    lambda u: u.connection.execute(
                        update(s.schedule_skills)
                        .where(s.schedule_skills.c.schedule_id == plan["id"])
                        .values(position=2)
                    )
                )
            with pytest.raises(BusinessError, match="constraint"):
                await db.write(
                    lambda u: u.connection.execute(
                        delete(s.schedule_skills).where(s.schedule_skills.c.schedule_id == plan["id"])
                    )
                )
        finally:
            await db.close()

    asyncio.run(scenario())


def test_bindings_require_enabled_skills_and_live_references(tmp_path):
    async def scenario():
        db, agent, _, service = await setup(tmp_path)
        try:
            usable = await seed_skill(db, agent, "beta-skill")
            foreign = await seed_skill(db, agent, "gamma-skill", platforms=("windows",))
            retired = await seed_skill(db, agent, "delta-skill")
            await db.write(
                lambda u: u.skill_state(
                    agent,
                    retired["id"],
                    retired["revision"],
                    "archived",
                    retired["revision_id"],
                    retired["manifest_sha256"],
                )
            )

            async def rejects(code, value, schedule=None, revision=0):
                with pytest.raises(BusinessError) as error:
                    await service.save(agent, "rejected", True, value, schedule, revision)
                assert error.value.code == code

            await rejects("invalid_skill", {**interval("x"), "skills": [OTHER]})
            await rejects("invalid_skill", {**interval("x"), "skills": [foreign["id"]]})
            await rejects("invalid_skill", {**interval("x"), "skills": [retired["id"]]})
            await rejects("invalid_context_ref", {**interval("x"), "context_from": [OTHER]})
            assert await service.page(agent) == []

            source = await service.save(agent, "source", True, interval("source"))
            plan = await service.save(
                agent,
                "consumer",
                True,
                {**interval("consumer"), "skills": [usable["id"]], "context_from": [source["id"]]},
            )
            assert plan["value"]["skills"] == [usable["id"]]
            await rejects("invalid_context_ref", {**interval("x"), "context_from": [plan["id"]]}, plan["id"], 1)
            await service.archive(agent, source["id"], 1)
            await rejects("invalid_context_ref", {**interval("x"), "context_from": [source["id"]]})
            assert [row["name"] for row in await service.page(agent)] == ["consumer"]
            assert {row["name"] for row in await service.page(agent, include_archived=True)} == {
                "source",
                "consumer",
            }
            assert (await service.get(agent, plan["id"]))["value"]["context_from"] == [source["id"]]
        finally:
            await db.close()

    asyncio.run(scenario())


def test_context_cycles_are_rejected(tmp_path):
    async def scenario():
        db, agent, _, service = await setup(tmp_path)
        try:
            first = await service.save(agent, "first", True, interval("first"))
            second = await service.save(agent, "second", True, {**interval("second"), "context_from": [first["id"]]})
            with pytest.raises(BusinessError) as error:
                await service.save(
                    agent, "first", True, {**interval("first"), "context_from": [second["id"]]}, first["id"], 1
                )
            assert error.value.code == "invalid_context_cycle"
            assert (await service.get(agent, first["id"]))["value"]["context_from"] == []
        finally:
            await db.close()

    asyncio.run(scenario())


def test_v0049_adds_bindings_preserves_rows_and_rejects_downgrade(tmp_path):
    engine = database_engine(tmp_path / "omni.db")
    try:
        with engine.connect() as connection:
            config = configuration()
            config.attributes["connection"] = connection
            command.upgrade(config, "v2_0048")
            reflection = inspect(connection)
            assert not {"schedule_skills", "schedule_context_refs"} & set(reflection.get_table_names())
            columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(schedule_revisions)")}
            assert "continuity" not in columns
            unit = Repositories(connection, SystemClock(), UUIDs(), UUIDs().new())
            unit.initialize()
            agent = str(unit.query("agents", "")[0]["id"])
            stamp = SystemClock().now()
            connection.exec_driver_sql(
                "INSERT INTO sessions(id,agent_id,created_at,title,revision,next_seq) VALUES(?,?,?,'legacy',1,0)",
                (UUIDs().new(), agent, stamp),
            )
            session = connection.exec_driver_sql(
                "SELECT id FROM sessions WHERE agent_id=? AND title='legacy'", (agent,)
            ).scalar_one()
            schedule_id = UUIDs().new()
            # The root row owns the deferred pointer to its revision, so it is inserted first;
            # schedule_revisions points back through an immediate foreign key.
            connection.exec_driver_sql(
                "INSERT INTO schedules(id,agent_id,created_at,name,revision,enabled) VALUES(?,?,?,'legacy',1,1)",
                (schedule_id, agent, stamp),
            )
            connection.exec_driver_sql(
                "INSERT INTO schedule_revisions(id,agent_id,created_at,schedule_id,revision,name,enabled,kind,"
                "timezone,tzdb_version,expression,interval_seconds,start_at,session_id,prompt,misfire) "
                "VALUES(?,?,?,?,1,'legacy',1,'interval','UTC','2026a','',60,?,?,'legacy prompt','latest')",
                (UUIDs().new(), agent, stamp, schedule_id, "2026-11-03T00:00:00.000000+00:00", session),
            )
            command.upgrade(config, "head")
            command.upgrade(config, "head")
            assert head() == "v2_0063"
            assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == "v2_0063"
            kept = connection.exec_driver_sql(
                "SELECT prompt,continuity,misfire FROM schedule_revisions WHERE schedule_id=?", (schedule_id,)
            ).one()
            assert tuple(kept) == ("legacy prompt", 0, "latest")
            info = {row[1]: row for row in connection.exec_driver_sql("PRAGMA table_info(schedule_revisions)")}
            assert info["continuity"][3] == 1 and info["continuity"][4] == "0"
            reflection = inspect(connection)
            for name in ("schedule_skills", "schedule_context_refs"):
                table = s.metadata.tables[name]
                assert {item["name"]: item["nullable"] for item in reflection.get_columns(name)} == {
                    column.name: column.nullable for column in table.c
                }
                assert {tuple(item["column_names"]) for item in reflection.get_unique_constraints(name)} == {
                    tuple(constraint.columns.keys())
                    for constraint in table.constraints
                    if isinstance(constraint, UniqueConstraint)
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
                assert {item["name"] for item in reflection.get_indexes(name)} == {f"ix_{name}_agent_id"}
            triggers = {
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name IN "
                    "('schedule_skills','schedule_context_refs')"
                )
            }
            assert triggers == {
                "schedule_skill_identity",
                "schedule_skill_keep",
                "schedule_context_ref_identity",
                "schedule_context_ref_keep",
            }
            assert not connection.exec_driver_sql("PRAGMA foreign_key_check").all()
            with pytest.raises(RuntimeError):
                command.downgrade(config, "v2_0048")
            connection.commit()
    finally:
        engine.dispose()
