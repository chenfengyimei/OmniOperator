"""V2 transactional kernel regressions. Every case has an isolated empty instance."""

import asyncio
import uuid

import pytest
from sqlalchemy import insert, select

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.database import Database
from omni.adapters.primitives import SystemClock, UUIDs
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError, ModelReply


def test_empty_schema_and_scoped_foreign_keys(tmp_path):
    async def scenario():
        db = Database(tmp_path / "omni.db", SystemClock(), UUIDs())
        await db.write(lambda u: u.initialize())
        agents = await db.read(lambda u: u.query("agents", ""))
        first = agents[0]["id"]
        second = (await db.write(lambda u: u.create_agent("second")))["id"]
        receipt = await db.write(lambda u: u.accept_turn(first, None, "key", "hello", {}))
        with pytest.raises(BusinessError, match="does not exist"):
            await db.read(lambda u: u.get_run(second, receipt["run_id"]))
        with pytest.raises(BusinessError, match="constraint"):
            await db.write(
                lambda u: u.connection.execute(
                    insert(s.messages).values(
                        id=str(uuid.uuid4()),
                        agent_id=second,
                        created_at=SystemClock().now(),
                        session_id=receipt["session_id"],
                        seq=5,
                        role="user",
                        content="leak",
                    )
                )
            )
        report = await db.integrity()
        assert report["integrity"] == "ok" and report["foreign_keys"] == 0
        assert {"alembic_version", "source_anchors", "budget_accounts"} <= set(report["tables"])
        await db.close()

    asyncio.run(scenario())


def test_deduplication_atomic_output_and_budget_unknown(tmp_path):
    async def scenario():
        db = Database(tmp_path / "omni.db", SystemClock(), UUIDs())
        await db.write(lambda u: u.initialize())
        agent = (await db.read(lambda u: u.query("agents", "")))[0]["id"]
        one = await db.write(lambda u: u.accept_turn(agent, None, "one", "hello", {}))
        duplicate = await db.write(lambda u: u.accept_turn(agent, None, "one", "hello", {}))
        assert duplicate["duplicate"] and duplicate["run_id"] == one["run_id"]
        await db.write(lambda u: u.transition(agent, one["run_id"], "running"))
        call = await db.write(lambda u: u.reserve_call(agent, one["run_id"], 100, 100, "test"))
        await db.write(lambda u: u.settle_call(agent, call, None))
        await db.write(lambda u: u.recover())
        with pytest.raises(BusinessError, match="exhausted"):
            await db.write(lambda u: u.reserve_call(agent, one["run_id"], 1, 100, "test"))
        await db.write(lambda u: u.settle_call(agent, call, ModelReply("", cost_micro=25)))
        with pytest.raises(BusinessError, match="already settled"):
            await db.write(lambda u: u.settle_call(agent, call, ModelReply("", cost_micro=25)))
        accounts = await db.read(
            lambda u: [dict(r) for r in u.connection.execute(select(s.budget_accounts)).mappings()]
        )
        assert accounts[0]["held_micro"] == 0 and accounts[0]["spent_micro"] == 25
        assert (await db.read(lambda u: u.get_run(agent, one["run_id"])))["state"] == "interrupted"
        await db.close()

    asyncio.run(scenario())


def test_model_turn_and_exclusive_instance(tmp_path):
    class Model:
        async def complete(self, messages, tools, settings):
            assert messages[-1]["content"] == "hello"
            return ModelReply("answer", cost_micro=0)

    async def scenario():
        instance = await open_instance(tmp_path, model=Model())
        app = instance.application
        with pytest.raises(BusinessError, match="already running"):
            await open_instance(tmp_path, model=Model())
        agent = (await app.query("agents", ""))[0]["id"]
        cfg = await app.settings(agent)
        await app.save_settings(agent, {**cfg["value"], "model": "fake"}, cfg["revision"])
        receipt = await app.submit_turn(agent, None, "hello", "hello")
        for _ in range(100):
            run = await app.get_run(agent, receipt["run_id"])
            if run["state"] == "succeeded":
                break
            await asyncio.sleep(0.02)
        assert run["state"] == "succeeded", run
        assert [m["content"] for m in await app.messages(agent, receipt["session_id"])] == ["hello", "answer"]
        assert (await instance.close())["complete"]
        reopened = await open_instance(tmp_path, model=Model())
        assert reopened.health()["ready"]
        await reopened.close()

    asyncio.run(scenario())
