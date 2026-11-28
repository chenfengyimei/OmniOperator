"""K16 1a: tool-result image ledger rows (message_id NULL), frozen-trigger guards, run-terminal release and v0050 rebuild."""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid

from alembic import command
from omni_test_support.v2.execution import wait_state
from omni_test_support.v2.images import (
    BusinessError,
    ImageInfo,
    ModelReply,
    configure,
    inspect_image,
    model_tokens,
    open_instance,
    png,
    pytest,
    s,
    select,
    update,
)
from omni_test_support.v2.scheduling import Clock
from omni_test_support.v2.v2_configuration_helpers import model_uow
from omni_test_support.v2.v2_legacy_seed import PreKernelSeedRepositories
from sqlalchemy import delete

from omni.adapters.persistence.migration_config import configuration, head
from omni.adapters.persistence.sqlalchemy_access import database_engine
from omni.adapters.primitives import SystemClock, UUIDs
from omni.domain.context import CONTEXT_CHARS
from omni.domain.objects import Blob


class BlockModel:
    def __init__(self):
        self.gate = asyncio.Event()
        self.requests = []

    async def complete(self, messages, tools, settings):
        self.requests.append(messages)
        await self.gate.wait()
        return ModelReply("done", cost_micro=0)


def tool_ref(image, policy_id, part_id=None):
    return {
        "part_id": part_id or str(uuid.uuid4()),
        "object_id": image["object_id"],
        "sha256": image["sha256"],
        "width": image["width"],
        "height": image["height"],
        "media_type": image["media_type"],
        "bytes": image["bytes"],
        "token_cost": model_tokens("gpt-4o"),
        "detail": "low",
        "policy_id": policy_id,
        "role": "tool",
    }


async def ledger_rows(instance, agent, run_id):
    return await instance._database.read(
        lambda u: [
            dict(r)
            for r in u.connection.execute(
                select(s.model_call_images)
                .join(s.model_calls, s.model_calls.c.id == s.model_call_images.c.call_id)
                .where(s.model_call_images.c.agent_id == agent, s.model_calls.c.run_id == run_id)
            ).mappings()
        ]
    )


async def wait_reserved_call(instance, agent, run_id, *, with_ledger=False):
    async with asyncio.timeout(10):
        while True:
            row = await instance._database.read(
                lambda u: (
                    u.connection.execute(
                        select(s.model_calls.c.id)
                        .where(
                            s.model_calls.c.agent_id == agent,
                            s.model_calls.c.run_id == run_id,
                            s.model_calls.c.purpose == "turn",
                            s.model_calls.c.state == "reserved",
                        )
                        .order_by(s.model_calls.c.created_at)
                    )
                    .scalars()
                    .first()
                )
            )
            if row and (not with_ledger or await ledger_rows(instance, agent, run_id)):
                return str(row)
            await asyncio.sleep(0.01)


async def prepare_tool(instance, agent, run_id, call_id, ref):
    await instance._database.write(lambda u: u.model_images_prepare(agent, run_id, call_id, [ref]))


async def expect_code(instance, agent, run_id, call_id, ref, code):
    with pytest.raises(BusinessError) as exc:
        await prepare_tool(instance, agent, run_id, call_id, ref)
    assert exc.value.code == code


def test_tool_rows_prepare_release_and_collect(tmp_path):
    async def scenario():
        clock = Clock()
        model = BlockModel()
        instance = await open_instance(tmp_path, model=model, clock=clock)
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            await configure(instance, agent, per_call=4, budget=12)
            user_image = await instance.images.upload(agent, png())
            tool_image = await instance.images.upload(agent, png((64, 32)))
            receipt = await instance.application.submit_turn(
                agent, None, "tool-ledger", "look", (user_image["object_id"],)
            )
            run_id = receipt["run_id"]
            call_id = await wait_reserved_call(instance, agent, run_id, with_ledger=True)
            policy = await instance._database.read(lambda u: u.run_image_policy(agent, run_id))
            policy_id = str(policy["id"])
            ref = tool_ref(tool_image, policy_id)
            await prepare_tool(instance, agent, run_id, call_id, ref)
            rows = await ledger_rows(instance, agent, run_id)
            tool_row = next(r for r in rows if r["part_id"] == ref["part_id"])
            user_row = next(r for r in rows if r["part_id"] != ref["part_id"])
            assert tool_row["message_id"] is None and tool_row["erased_at"] is None
            assert tool_row["object_id"] == tool_image["object_id"] and tool_row["sha256"] == ref["sha256"]
            assert user_row["message_id"] and user_row["object_id"] == user_image["object_id"]
            # While the run is live the tool object stays referenced and cannot be collected.
            with pytest.raises(BusinessError) as exc:
                await instance._database.write(
                    lambda u: u.object_remove(
                        agent, tool_image["object_id"], u.object_get(agent, tool_image["object_id"])["revision"]
                    )
                )
            assert exc.value.code == "object_protected"
            await expect_code(
                instance,
                agent,
                run_id,
                call_id,
                {**ref, "part_id": str(uuid.uuid4()), "object_id": str(uuid.uuid4())},
                "image_source_changed",
            )
            await expect_code(
                instance,
                agent,
                run_id,
                call_id,
                {**ref, "part_id": str(uuid.uuid4()), "sha256": "0" * 64},
                "image_source_changed",
            )
            await expect_code(
                instance,
                agent,
                run_id,
                call_id,
                {**ref, "part_id": str(uuid.uuid4()), "policy_id": str(uuid.uuid4())},
                "image_source_changed",
            )
            await expect_code(
                instance,
                agent,
                run_id,
                call_id,
                {**ref, "part_id": str(uuid.uuid4()), "token_cost": 86},
                "image_source_changed",
            )
            await expect_code(
                instance, agent, run_id, call_id, {**ref, "part_id": user_row["part_id"]}, "image_source_changed"
            )
            assert len(await ledger_rows(instance, agent, run_id)) == 2
            model.gate.set()
            await wait_state(instance.application, agent, run_id, {"succeeded"})
            rows = await ledger_rows(instance, agent, run_id)
            tool_row = next(r for r in rows if r["part_id"] == ref["part_id"])
            user_row = next(r for r in rows if r["part_id"] != ref["part_id"])
            # Release is not erasure: bytes reference dropped, accounting and erased_at intact.
            assert tool_row["object_id"] is None and tool_row["erased_at"] is None
            assert tool_row["sha256"] == ref["sha256"] and tool_row["state"] == "attached"
            assert user_row["object_id"] == user_image["object_id"] and user_row["erased_at"] is None
            clock.advance(2 * 86400)
            await instance._database.write(
                lambda u: u.object_remove(
                    agent, tool_image["object_id"], u.object_get(agent, tool_image["object_id"])["revision"]
                )
            )
            run = await instance._database.read(lambda u: u.get_run(agent, run_id))
            await instance._database.write(lambda u: u.erase_message_record(agent, str(run["input_message_id"])))
            rows = await ledger_rows(instance, agent, run_id)
            tool_row = next(r for r in rows if r["part_id"] == ref["part_id"])
            user_row = next(r for r in rows if r["part_id"] != ref["part_id"])
            assert user_row["object_id"] is None and user_row["erased_at"]
            assert tool_row["erased_at"] is None
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_tool_refs_share_per_call_and_run_budget(tmp_path):
    async def scenario():
        model = BlockModel()
        instance = await open_instance(tmp_path, model=model, clock=Clock())
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            await configure(instance, agent, per_call=1, budget=1)
            image = await instance.images.upload(agent, png())
            receipt = await instance.application.submit_turn(agent, None, "tool-budget", "look")
            run_id = receipt["run_id"]
            call_id = await wait_reserved_call(instance, agent, run_id)
            policy_id = str((await instance._database.read(lambda u: u.run_image_policy(agent, run_id)))["id"])
            with pytest.raises(BusinessError) as exc:
                await instance._database.write(
                    lambda u: u.model_images_prepare(
                        agent, run_id, call_id, [tool_ref(image, policy_id), tool_ref(image, policy_id)]
                    )
                )
            assert exc.value.code == "image_budget_exceeded"
            await prepare_tool(instance, agent, run_id, call_id, tool_ref(image, policy_id))
            with pytest.raises(BusinessError) as exc:
                await prepare_tool(instance, agent, run_id, call_id, tool_ref(image, policy_id))
            assert exc.value.code == "image_budget_exceeded"
            assert len(await ledger_rows(instance, agent, run_id)) == 1
            model.gate.set()
            await wait_state(instance.application, agent, run_id, {"succeeded"})
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_tool_ledger_identity_immutable_and_cancel_releases(tmp_path):
    async def scenario():
        model = BlockModel()
        instance = await open_instance(tmp_path, model=model, clock=Clock())
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            await configure(instance, agent, per_call=4, budget=12)
            image = await instance.images.upload(agent, png())
            receipt = await instance.application.submit_turn(agent, None, "tool-immutable", "look")
            run_id = receipt["run_id"]
            call_id = await wait_reserved_call(instance, agent, run_id)
            policy_id = str((await instance._database.read(lambda u: u.run_image_policy(agent, run_id)))["id"])
            ref = tool_ref(image, policy_id)
            await prepare_tool(instance, agent, run_id, call_id, ref)
            row = (await ledger_rows(instance, agent, run_id))[0]

            def tamper(values):
                async def attempt():
                    with pytest.raises(BusinessError) as exc:
                        await instance._database.write(
                            lambda u: u.connection.execute(
                                update(s.model_call_images)
                                .where(s.model_call_images.c.id == row["id"])
                                .values(**values)
                            )
                        )
                    assert exc.value.code == "data_constraint"

                return attempt()

            await tamper({"object_id": None})
            await tamper({"part_id": str(uuid.uuid4())})
            await tamper({"sha256": "0" * 64})
            await tamper({"message_id": str(uuid.uuid4())})
            await tamper({"state": "attached"})
            with pytest.raises(BusinessError) as exc:
                await instance._database.write(
                    lambda u: u.connection.execute(
                        delete(s.model_call_images).where(s.model_call_images.c.id == row["id"])
                    )
                )
            assert exc.value.code == "data_constraint"
            await instance.application.cancel(agent, run_id)
            await wait_state(instance.application, agent, run_id, {"cancelled"})
            released = (await ledger_rows(instance, agent, run_id))[0]
            assert released["object_id"] is None and released["erased_at"] is None
            assert released["part_id"] == ref["part_id"] and released["sha256"] == ref["sha256"]
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_disabled_policy_rejects_tool_refs(tmp_path):
    async def scenario():
        model = BlockModel()
        instance = await open_instance(tmp_path, model=model, clock=Clock())
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            await configure(instance, agent, protocol="disabled")
            image = await instance.images.upload(agent, png())
            receipt = await instance.application.submit_turn(agent, None, "tool-disabled", "look")
            run_id = receipt["run_id"]
            call_id = await wait_reserved_call(instance, agent, run_id)
            with pytest.raises(BusinessError) as exc:
                await prepare_tool(instance, agent, run_id, call_id, tool_ref(image, ""))
            assert exc.value.code == "image_call_invalid"
            model.gate.set()
            await wait_state(instance.application, agent, run_id, {"succeeded"})
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_v0050_upgrade_preserves_user_ledger_and_enables_tool_rows(tmp_path):
    path = tmp_path / "omni.db"
    clock, ids = SystemClock(), UUIDs()
    engine = database_engine(path)
    try:
        with engine.connect() as connection:
            cfg = configuration()
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "v2_0049")
            u = PreKernelSeedRepositories(connection, clock, ids, ids.new())
            u.initialize()
            agent = connection.execute(select(s.agents.c.id)).scalar_one()
            revision = model_uow(u, agent, model="gpt-4o")
            u.model_image_policy_add(
                agent, str(revision["revision_id"]), {"protocol": "openai_low", "per_call_limit": 4, "run_budget": 12}
            )
            content = png()
            info = inspect_image(content)
            assert isinstance(info, ImageInfo)
            expires = (dt.datetime.fromisoformat(clock.now()) + dt.timedelta(days=1)).isoformat()
            obj = u.object_register(Blob(agent, info.sha256, info.bytes), "image/png", expires)
            u.image_register(agent, str(obj["id"]), info)
            receipt = u.accept_turn(agent, None, "legacy-image", "look", {}, image_ids=(str(obj["id"]),))
            run_id = str(receipt["run_id"])
            u.claim_runs(1)
            run = u.get_run(agent, run_id)
            prepared = u.prepare_context(agent, run_id)
            u.finalize_context(
                agent,
                run_id,
                str(prepared["snapshot_id"]),
                CONTEXT_CHARS,
                message_ids=[str(run["input_message_id"])],
                include_summary=False,
            )
            call = u.reserve_call(agent, run_id, 1234, 1_000_000, "gpt-4o")
            config = u.run_image_policy(agent, run_id)
            parts = u.image_parts(agent, [str(run["input_message_id"])])
            refs = [
                {
                    **{
                        k: part[k] for k in ("part_id", "object_id", "sha256", "width", "height", "media_type", "bytes")
                    },
                    "token_cost": model_tokens("gpt-4o"),
                    "detail": "low",
                    "policy_id": str(config["id"]),
                }
                for part in parts
            ]
            u.model_images_prepare(agent, run_id, call, refs)
            before = [dict(r) for r in connection.execute(select(s.model_call_images)).mappings()]
            assert len(before) == 1 and before[0]["message_id"]
            connection.commit()
            command.upgrade(cfg, "head")
            command.upgrade(cfg, "head")
            assert head() == "v2_0064"
            assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == "v2_0064"
            after = [dict(r) for r in connection.execute(select(s.model_call_images)).mappings()]
            assert after == before
            pragma = {row[1]: row for row in connection.exec_driver_sql("PRAGMA table_info(model_call_images)")}
            assert pragma["message_id"][3] == 0
            triggers = {
                row[0] for row in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='trigger'")
            }
            assert {
                "call_image_scope",
                "call_image_budget",
                "call_image_state",
                "call_image_erasure",
                "call_image_update",
                "call_image_delete",
                "call_image_tool_release",
            } <= triggers
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            tool = tool_ref(
                {
                    "object_id": str(obj["id"]),
                    "sha256": info.sha256,
                    "width": info.width,
                    "height": info.height,
                    "media_type": info.media_type,
                    "bytes": info.bytes,
                },
                str(config["id"]),
            )
            u.model_images_prepare(agent, run_id, call, [tool])
            rows = [dict(r) for r in connection.execute(select(s.model_call_images)).mappings()]
            added = next(r for r in rows if r["part_id"] == tool["part_id"])
            assert added["message_id"] is None and added["object_id"] == str(obj["id"])
            connection.commit()
    finally:
        engine.dispose()
