"""Memory decay rules, owner passes, recall reinforcement and hidden-not-erased forgetting; real isolated SQLite."""

from __future__ import annotations

import datetime as dt

from omni_test_support.v2.objects import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.objects import (
    Clock as Clock,
)
from omni_test_support.v2.objects import (
    asyncio as asyncio,
)
from omni_test_support.v2.objects import (
    opened as opened,
)
from omni_test_support.v2.objects import (
    pytest as pytest,
)
from omni_test_support.v2.objects import (
    s as s,
)
from sqlalchemy import select, update

from omni.domain.memory_decay import (
    CLARITY_ORDER,
    catchup_steps,
    decay_step,
    hard_forget,
    initial_clarity,
    is_protected,
    retention_score,
    target_clarity,
)


def test_retention_formula_matches_reference_values():
    assert retention_score(50, 0, 0, 0, False) == 76
    assert retention_score(50, 100, 0, 0, False) == 88
    assert retention_score(50, 0, 30, 30, False) == pytest.approx(41.95)
    assert retention_score(50, 0, 30, 30, True) == 55
    assert retention_score(50, 0, 1000, 1000, False) == -30
    assert retention_score(1, 0, 0, 0, False) == 50


@pytest.mark.parametrize(
    "retention,expected",
    [
        (68, "vivid"),
        (67.99, "clear"),
        (46, "clear"),
        (45.99, "fuzzy"),
        (24, "fuzzy"),
        (23.99, "trace"),
        (12, "trace"),
        (11.99, "forgotten"),
    ],
)
def test_clarity_cuts_are_exact(retention, expected):
    assert target_clarity(retention, False) == expected
    assert target_clarity(retention, True) == "vivid"


@pytest.mark.parametrize(
    "age,importance,protected,expected",
    [
        (61, 49, False, True),
        (60, 49, False, False),
        (61, 50, False, False),
        (61, 49, True, False),
    ],
)
def test_hard_forget_predicate_is_strict(age, importance, protected, expected):
    assert hard_forget(age, importance, protected) is expected


def test_decay_step_moves_at_most_one_level_both_directions():
    assert decay_step("vivid", "forgotten") == "clear"
    assert decay_step("trace", "vivid") == "fuzzy"
    assert decay_step("fuzzy", "fuzzy") == "fuzzy"
    with pytest.raises(ValueError):
        decay_step("vivid", "unknown")


def test_catchup_steps_clamp_and_first_run():
    assert catchup_steps(None, "2026-11-20") == 1
    assert catchup_steps("2026-11-20", "2026-11-20") == 1
    assert catchup_steps("2026-11-15", "2026-11-20") == 5
    assert catchup_steps("2026-01-01", "2026-11-20") == 30


def test_protection_and_initial_clarity_thresholds():
    assert is_protected(65, False) and is_protected(1, True) and not is_protected(64, False)
    assert initial_clarity(65, False) == "vivid"
    assert initial_clarity(64, False) == "clear"
    assert initial_clarity(1, True) == "vivid"


def test_remember_defaults_initial_clarity_and_validation(tmp_path):
    async def scenario():
        clock = Clock()
        async with opened(tmp_path / "instance", clock) as (_, db, _, agent):
            row = await db.write(lambda u: u.remember(agent, "plain fact"))
            assert row["importance"] == 50 and row["clarity"] == "clear" and not row["pinned"]
            stored = await db.read(lambda u: u._one(s.memory_entries, agent, str(row["id"])))
            assert stored["recall_count"] == 0 and stored["last_seen_at"] == clock.now()
            vivid = await db.write(lambda u: u.remember(agent, "important fact", importance=70))
            assert vivid["clarity"] == "vivid"
            pinned = await db.write(lambda u: u.remember(agent, "pinned fact", importance=10, pinned=True))
            assert pinned["clarity"] == "vivid" and pinned["pinned"]
            for bad in (0, 101, "50", True, 50.0):
                with pytest.raises(BusinessError) as caught:
                    await db.write(lambda u: u.remember(agent, "bad", importance=bad))
                assert caught.value.code == "invalid_memory" and caught.value.status == 422
            with pytest.raises(BusinessError) as caught:
                await db.write(lambda u: u.remember(agent, "bad", pinned="yes"))
            assert caught.value.code == "invalid_memory"

    asyncio.run(scenario())


def test_touch_reinforces_recall_with_bounds_and_skips(tmp_path):
    async def scenario():
        clock = Clock()
        async with opened(tmp_path / "instance", clock) as (_, db, _, agent):
            rows = [await db.write(lambda u: u.remember(agent, f"recall fact {n}")) for n in range(3)]
            ids = [str(row["id"]) for row in rows]
            clock.current += dt.timedelta(hours=1)
            assert await db.write(lambda u: u.memory_touch(agent, ids)) == 3
            after = await db.read(lambda u: u._one(s.memory_entries, agent, ids[0]))
            assert after["recall_count"] == 1 and after["last_seen_at"] == clock.now()
            with pytest.raises(BusinessError) as caught:
                await db.write(lambda u: u.memory_touch(agent, ids * 2))
            assert caught.value.code == "memory_touch_invalid" and caught.value.status == 422
            await db.write(lambda u: u.forget(agent, ids[2]))
            person = await db.write(lambda u: u.person_create(agent, "Owner"))
            document = await db.write(
                lambda u: u.remember(agent, "profile text", kind="profile", person_id=person["id"])
            )
            skipped = await db.write(lambda u: u.memory_touch(agent, [ids[2], str(document["id"]), "missing-id"]))
            assert skipped == 0
            await db.write(
                lambda u: u.connection.execute(
                    update(s.memory_entries).where(s.memory_entries.c.id == ids[0]).values(recall_count=999)
                )
            )
            await db.write(lambda u: u.memory_touch(agent, [ids[0]]))
            capped = await db.read(lambda u: u._one(s.memory_entries, agent, ids[0]))
            assert capped["recall_count"] == 999

    asyncio.run(scenario())


def test_first_pass_promotes_fresh_memory_and_daily_gate_holds(tmp_path):
    async def scenario():
        clock = Clock()
        async with opened(tmp_path / "instance", clock) as (_, db, _, agent):
            row = await db.write(lambda u: u.remember(agent, "fresh fact"))
            assert row["clarity"] == "clear"
            result = await db.write(lambda u: u.memory_decay_pass(agent))
            assert result["steps"] == 1 and result["pass_decayed"] == 0
            assert result["last_decay_date"] == clock.now()[:10]
            promoted = await db.read(lambda u: u._one(s.memory_entries, agent, str(row["id"])))
            assert promoted["clarity"] == "vivid"
            with pytest.raises(BusinessError) as caught:
                await db.write(lambda u: u.memory_decay_pass(agent))
            assert caught.value.code == "memory_decay_already_today" and caught.value.status == 409
            forced = await db.write(lambda u: u.memory_decay_pass(agent, force=True))
            assert forced["steps"] == 1
            with pytest.raises(BusinessError) as caught:
                await db.write(lambda u: u.memory_decay_pass(agent, force="yes"))
            assert caught.value.code == "memory_decay_invalid" and caught.value.status == 422
            audits = await db.read(
                lambda u: list(
                    u.connection.execute(select(s.audit).where(s.audit.c.kind == "memory.decayed")).mappings()
                )
            )
            assert len(audits) == 2

    asyncio.run(scenario())


def test_aging_steps_down_one_level_per_day(tmp_path):
    async def scenario():
        clock = Clock()
        async with opened(tmp_path / "instance", clock) as (_, db, _, agent):
            row = await db.write(lambda u: u.remember(agent, "aging fact"))
            await db.write(lambda u: u.memory_decay_pass(agent))
            clock.current += dt.timedelta(days=30)
            first = await db.write(lambda u: u.memory_decay_pass(agent))
            assert first["pass_decayed"] == 2
            mid = await db.read(lambda u: u._one(s.memory_entries, agent, str(row["id"])))
            assert mid["clarity"] == "fuzzy" and mid["decay_steps"] == 2
            clock.current += dt.timedelta(days=60)
            second = await db.write(lambda u: u.memory_decay_pass(agent))
            assert second["pass_decayed"] == 1
            later = await db.read(lambda u: u._one(s.memory_entries, agent, str(row["id"])))
            assert later["clarity"] == "trace" and later["decay_steps"] == 3

    asyncio.run(scenario())


def test_catchup_reaches_forgotten_hidden_but_never_erased(tmp_path):
    async def scenario():
        clock = Clock()
        async with opened(tmp_path / "instance", clock) as (_, db, _, agent):
            row = await db.write(lambda u: u.remember(agent, "zebrafish fact"))
            await db.write(lambda u: u.memory_decay_pass(agent))
            clock.current += dt.timedelta(days=400)
            result = await db.write(lambda u: u.memory_decay_pass(agent))
            assert result["steps"] == 30 and result["pass_forgotten"] == 1 and result["forgotten"] == 1
            hidden = await db.read(lambda u: u._one(s.memory_entries, agent, str(row["id"])))
            assert hidden["clarity"] == "forgotten"
            assert hidden["deleted_at"] is None and hidden["content"] == "zebrafish fact"
            assert await db.read(lambda u: u.search_memory(agent, "zebrafish")) == []
            await db.write(lambda u: u.forget(agent, str(row["id"])))
            erased = await db.read(lambda u: u._one(s.memory_entries, agent, str(row["id"])))
            assert erased["deleted_at"] is not None and erased["content"] == ""

    asyncio.run(scenario())


def test_hard_forget_ages_out_low_importance_memory(tmp_path):
    async def scenario():
        clock = Clock()
        async with opened(tmp_path / "instance", clock) as (_, db, _, agent):
            row = await db.write(lambda u: u.remember(agent, "trivial note", importance=30))
            await db.write(lambda u: u.memory_decay_pass(agent))
            clock.current += dt.timedelta(days=61)
            result = await db.write(lambda u: u.memory_decay_pass(agent))
            assert result["pass_forgotten"] == 1
            hidden = await db.read(lambda u: u._one(s.memory_entries, agent, str(row["id"])))
            assert hidden["clarity"] == "forgotten" and hidden["deleted_at"] is None

    asyncio.run(scenario())


def test_pinned_and_protected_memories_never_fade_away(tmp_path):
    async def scenario():
        clock = Clock()
        async with opened(tmp_path / "instance", clock) as (_, db, _, agent):
            pinned = await db.write(lambda u: u.remember(agent, "pinned fact", importance=10, pinned=True))
            protected = await db.write(lambda u: u.remember(agent, "owner fact", importance=70))
            await db.write(lambda u: u.memory_decay_pass(agent))
            clock.current += dt.timedelta(days=400)
            result = await db.write(lambda u: u.memory_decay_pass(agent))
            assert result["pass_forgotten"] == 0
            pinned_row = await db.read(lambda u: u._one(s.memory_entries, agent, str(pinned["id"])))
            protected_row = await db.read(lambda u: u._one(s.memory_entries, agent, str(protected["id"])))
            assert pinned_row["clarity"] == "vivid"
            assert protected_row["clarity"] == "clear"

    asyncio.run(scenario())


def test_documents_are_exempt_from_decay_and_touch(tmp_path):
    async def scenario():
        clock = Clock()
        async with opened(tmp_path / "instance", clock) as (_, db, _, agent):
            person = await db.write(lambda u: u.person_create(agent, "Owner"))
            document = await db.write(
                lambda u: u.remember(agent, "resident profile", kind="profile", person_id=person["id"])
            )
            await db.write(lambda u: u.memory_decay_pass(agent))
            clock.current += dt.timedelta(days=400)
            result = await db.write(lambda u: u.memory_decay_pass(agent))
            assert result["pass_decayed"] == 0 and result["pass_forgotten"] == 0
            row = await db.read(lambda u: u._one(s.memory_entries, agent, str(document["id"])))
            assert row["clarity"] == "clear" and row["recall_count"] == 0

    asyncio.run(scenario())


def test_decay_state_requires_real_agent(tmp_path):
    async def scenario():
        clock = Clock()
        async with opened(tmp_path / "instance", clock) as (_, db, _, agent):
            assert await db.read(lambda u: u.memory_decay_state(agent)) == {
                "last_decay_date": None,
                "last_decay_at": None,
                "decayed": 0,
                "forgotten": 0,
            }
            with pytest.raises(BusinessError) as caught:
                await db.read(lambda u: u.memory_decay_state("missing-agent"))
            assert caught.value.code == "not_found" and caught.value.status == 404

    asyncio.run(scenario())


def test_engine_search_reinforces_returned_memories(tmp_path):
    from omni_test_support.v2.memory_semantics import NoModel

    from omni.composition.instance import open_instance

    async def scenario():
        instance = await open_instance(tmp_path / "instance", model=NoModel(), capabilities=())
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            row = await instance.application.remember(agent, "engine recall tea")
            items = await instance.application.search_memory(agent, "tea")
            assert [str(item["id"]) for item in items] == [str(row["id"])]
            after = await instance._database.read(lambda u: u._one(s.memory_entries, agent, str(row["id"])))
            assert after["recall_count"] == 1
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_v0047_adds_decay_columns_and_state_table_idempotently(tmp_path):
    from alembic import command
    from sqlalchemy import inspect

    from omni.adapters.persistence.migration_config import configuration, head
    from omni.adapters.persistence.sqlalchemy_access import database_engine

    engine = database_engine(tmp_path / "omni.db")
    try:
        with engine.connect() as connection:
            config = configuration()
            config.attributes["connection"] = connection
            command.upgrade(config, "v2_0046")
            assert "memory_decay_state" not in inspect(connection).get_table_names()
            columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(memory_entries)")}
            assert not {"importance", "clarity", "recall_count", "decay_steps", "pinned", "last_seen_at"} & columns
            command.upgrade(config, "head")
            command.upgrade(config, "head")
            assert head() == "v2_0059"
            assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == "v2_0059"
            info = {row[1]: row for row in connection.exec_driver_sql("PRAGMA table_info(memory_entries)")}
            assert info["importance"][4] == "50" and info["clarity"][4] == "'vivid'"
            assert info["recall_count"][4] == "0" and info["decay_steps"][4] == "0" and info["pinned"][4] == "0"
            reflection = inspect(connection)
            table = s.metadata.tables["memory_decay_state"]
            assert {c["name"]: c["nullable"] for c in reflection.get_columns("memory_decay_state")} == {
                c.name: c.nullable for c in table.c
            }
            triggers = {
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='memory_decay_state'"
                )
            }
            assert triggers == {"memory_decay_identity", "memory_decay_keep"}
            with pytest.raises(RuntimeError):
                command.downgrade(config, "v2_0046")
            connection.commit()
    finally:
        engine.dispose()


def test_decay_http_state_pass_gate_and_force(tmp_path, monkeypatch):
    import uuid

    from fastapi.testclient import TestClient

    from omni.composition import http

    clock = Clock()
    clock.current = clock.current.replace(hour=3, minute=0, second=0, microsecond=0)
    original_open = http.open_instance

    async def open_with_clock(*args, **kwargs):
        return await original_open(*args, **kwargs, clock=clock)

    monkeypatch.setattr(http, "open_instance", open_with_clock)
    with TestClient(http.http_app(tmp_path)) as client:
        assert client.get(f"/api/v1/agents/{uuid.uuid4()}/memory/decay").status_code == 401
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text()
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        path = f"/api/v1/agents/{agent}/memory/decay"
        assert client.get(path).json() == {
            "last_decay_date": None,
            "last_decay_at": None,
            "decayed": 0,
            "forgotten": 0,
        }
        first = client.post(path, json={})
        assert first.status_code == 200 and first.json()["steps"] == 1
        assert client.post(path, json={}).status_code == 409
        assert client.post(path, json={"force": True}).status_code == 200
        assert client.post(path, json={"force": "yes"}).status_code == 422
        assert client.post(path, json={"other": 1}).status_code == 422
        assert client.get(f"/api/v1/agents/{uuid.uuid4()}/memory/decay").status_code == 404


def test_clarity_order_is_reference_sequence():
    assert CLARITY_ORDER == ("vivid", "clear", "fuzzy", "trace", "forgotten")
