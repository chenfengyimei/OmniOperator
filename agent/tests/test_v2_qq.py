"""QQ text admission, contextual isolation, revocation and durable owner-approved output."""

import asyncio
import copy
import json
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import func, select
from v2_configuration_helpers import model_instance
from v2_delivery_helpers import Clock

from omni.adapters.persistence import schema as s
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError, ModelReply
from omni.domain.delivery import DeliveryResult
from omni.domain.qq import ADAPTER, parse_event, qq_id


def event(identity=0, *, scene="private", sender=123, text="hello"):
    return {
        "post_type": "message",
        "self_id": 456,
        "user_id": sender,
        "message_id": identity,
        "message_type": scene,
        "sub_type": "friend" if scene == "private" else "normal",
        "group_id": 789,
        "message": ([{"type": "at", "data": {"qq": "456"}}] if scene == "group" else [])
        + [{"type": "text", "data": {"text": text}}],
    }


class Transport:
    identity = ADAPTER

    def __init__(self):
        self.sent = []
        self.authorize = None
        self.listening = asyncio.Event()
        self.cancelled = asyncio.Event()

    def account_key(self, configuration):
        return configuration["account"]

    async def listen(self, configuration, receive, connected):
        connected()
        self.listening.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled.set()

    async def send(self, message):
        if self.authorize:
            await self.authorize(message)
        self.sent.append(message)
        return DeliveryResult("sent", message.fingerprint, receipt_id="-7")

    async def close(self, timeout_s):
        return True


class Model:
    def __init__(self):
        self.requests = []

    async def complete(self, messages, tools, settings):
        self.requests.append((copy.deepcopy(messages), tools, settings.copy()))
        return ModelReply("QQ reply", cost_micro=2)


@asynccontextmanager
async def opened(tmp_path, *, scene="private", model=None):
    transport, clock = Transport(), Clock()
    model = model or Model()
    instance = await open_instance(tmp_path, model=model, clock=clock, qq_transport=transport)
    transport.authorize = lambda message: instance._database.read(lambda u: u.qq_outbound_authorized(message))
    try:
        agent = (await instance.application.query("agents", ""))[0]["id"]
        await model_instance(instance, agent)
        channel = await instance.configurations.create(
            agent, "channel", "QQ fixture", True, {"driver": "qq", "account": "456", "endpoint": "ws://127.0.0.1:3001"}
        )
        owner = await instance.people.owner(agent)
        person = await instance.people.create(agent, "QQ contact")
        account = await instance.people.bind(agent, person["id"], "qq", "123")
        await instance.people.save(agent, owner["id"], "resident", "OWNER_PRIVATE_CANARY", None, 0)
        binding = await instance.qq.save(
            agent,
            channel["id"],
            {
                "scene": scene,
                "peer_id": "789" if scene == "group" else "123",
                "enabled": True,
                "persona": "QQ_PERSONA",
                "cooldown_s": 10,
            },
        )
        yield instance, agent, channel, binding, transport, model, clock, account
    finally:
        assert (await instance.close())["complete"]


async def done(instance, agent, run_id):
    async with asyncio.timeout(10):
        while True:
            run = await instance.application.get_run(agent, run_id)
            if run["state"] not in {"queued", "running", "waiting_approval", "cancelling"}:
                return run
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("scene", ["private", "group"])
def test_text_roundtrip_uses_v2_context_and_owner_approval(tmp_path, scene):
    async def scenario():
        async with opened(tmp_path, scene=scene) as (i, agent, cfg, binding, transport, model, _, _):
            receipt = await i.qq.receive(agent, cfg["id"], cfg["revision_id"], event(scene=scene))
            assert (await done(i, agent, receipt["run_id"]))["state"] == "succeeded"
            messages, tools, settings = model.requests[0]
            assert not tools and settings["persona"] == "QQ_PERSONA" and settings["workspace"] == ""
            assert "OWNER_PRIVATE_CANARY" not in json.dumps(messages)
            assert "QQ_PERSONA" in json.dumps(messages)
            rows = await i.deliveries.page(agent)
            assert len(rows) == 1 and rows[0]["state"] == "awaiting_approval" and transport.sent == []
            assert rows[0]["target"] == scene + ":" + binding["peer_id"]
            repeated = await i.qq.receive(agent, cfg["id"], cfg["revision_id"], event(scene=scene))
            assert repeated["duplicate"] and repeated["run_id"] == receipt["run_id"]
            with pytest.raises(BusinessError, match="different content"):
                await i.qq.receive(agent, cfg["id"], cfg["revision_id"], event(scene=scene, text="different"))
            with pytest.raises(BusinessError, match="cooling"):
                await i.qq.receive(agent, cfg["id"], cfg["revision_id"], event(1, scene=scene))
            with pytest.raises(BusinessError, match="QQ channel"):
                await i.application.submit_turn(agent, binding["session_id"], "local", "PRIVATE_INJECTION")
            row = rows[0]
            await i.deliveries.decide(agent, row["id"], True, row["revision"], row["fingerprint"])
            async with asyncio.timeout(10):
                while (await i.deliveries.get(agent, row["id"]))["state"] != "sent":
                    await asyncio.sleep(0.01)
            assert len(transport.sent) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("revoke", ["binding", "person", "configuration"])
def test_revocation_blocks_pending_delivery_and_new_admission(tmp_path, revoke):
    async def scenario():
        async with opened(tmp_path) as (i, agent, cfg, binding, _, _, _, account):
            receipt = await i.qq.receive(agent, cfg["id"], cfg["revision_id"], event())
            assert (await done(i, agent, receipt["run_id"]))["state"] == "succeeded"
            if revoke == "binding":
                await i.qq.save(
                    agent,
                    cfg["id"],
                    {
                        k: (False if k == "enabled" else binding[k])
                        for k in ("scene", "peer_id", "enabled", "persona", "cooldown_s")
                    },
                    binding["id"],
                    binding["revision"],
                )
            elif revoke == "person":
                await i.people.unbind(agent, account["person_id"], account["id"], account["revision"])
            else:
                await i.configurations.save(agent, cfg["id"], "disabled", False, cfg["value"], cfg["revision"])
            row = (await i.deliveries.page(agent))[0]
            with pytest.raises(BusinessError):
                await i.deliveries.decide(agent, row["id"], True, row["revision"], row["fingerprint"])
            with pytest.raises(BusinessError):
                await i.qq.receive(agent, cfg["id"], cfg["revision_id"], event(2))

    asyncio.run(scenario())


def test_unbound_or_denied_sender_creates_no_partial_business_records(tmp_path):
    async def scenario():
        async with opened(tmp_path) as (i, agent, cfg, _, _, _, _, _):
            for raw in [event(sender=999), event(scene="group")]:
                with pytest.raises(BusinessError):
                    await i.qq.receive(agent, cfg["id"], cfg["revision_id"], raw)
            counts = await i._database.read(
                lambda u: [
                    u.connection.execute(select(func.count()).select_from(t)).scalar_one()
                    for t in (s.qq_runs, s.runs, s.inbox)
                ]
            )
            assert counts == [0, 0, 0]

    asyncio.run(scenario())


def test_parallel_replay_has_one_canonical_message_run_and_outbox(tmp_path):
    async def scenario():
        async with opened(tmp_path) as (i, agent, cfg, _, _, _, _, _):
            receipts = await asyncio.gather(
                *(i.qq.receive(agent, cfg["id"], cfg["revision_id"], event(-1)) for _ in range(8))
            )
            assert sum(not r["duplicate"] for r in receipts) == 1
            assert len({r["run_id"] for r in receipts}) == 1
            assert (await done(i, agent, receipts[0]["run_id"]))["state"] == "succeeded"
            counts = await i._database.read(
                lambda u: [
                    u.connection.execute(select(func.count()).select_from(t)).scalar_one()
                    for t in (s.qq_runs, s.runs, s.inbox, s.outbox)
                ]
            )
            assert counts == [1, 1, 1, 1]

    asyncio.run(scenario())


def test_revocation_during_model_settles_cost_without_publishing_output(tmp_path):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        class HeldModel(Model):
            async def complete(self, messages, tools, settings):
                started.set()
                await release.wait()
                return await super().complete(messages, tools, settings)

        async with opened(tmp_path, model=HeldModel()) as (i, agent, cfg, binding, _, _, _, _):
            try:
                receipt = await i.qq.receive(agent, cfg["id"], cfg["revision_id"], event())
                await asyncio.wait_for(started.wait(), 5)
                await i.qq.save(
                    agent,
                    cfg["id"],
                    {
                        k: (False if k == "enabled" else binding[k])
                        for k in ("scene", "peer_id", "enabled", "persona", "cooldown_s")
                    },
                    binding["id"],
                    binding["revision"],
                )
                release.set()
                run = await done(i, agent, receipt["run_id"])
                assert run["state"] == "failed" and run["error_code"] == "qq_binding_revoked"
                assert await i.deliveries.page(agent) == []
                calls = await i._database.read(
                    lambda u: [
                        dict(r)
                        for r in u.connection.execute(
                            select(s.model_calls).where(s.model_calls.c.run_id == run["id"])
                        ).mappings()
                    ]
                )
                assert len(calls) == 1 and calls[0]["cost_micro"] == 2
            finally:
                release.set()

    asyncio.run(scenario())


def test_outbound_account_cooldown_defers_without_spending_an_attempt(tmp_path):
    async def scenario():
        async with opened(tmp_path) as (i, agent, cfg, _, transport, _, clock, _):
            await i.qq.save(
                agent,
                cfg["id"],
                {"scene": "group", "peer_id": "789", "enabled": True, "persona": "Group persona", "cooldown_s": 10},
            )
            for raw in [event(1), event(2, scene="group")]:
                receipt = await i.qq.receive(agent, cfg["id"], cfg["revision_id"], raw)
                assert (await done(i, agent, receipt["run_id"]))["state"] == "succeeded"
            rows = await i.deliveries.page(agent)
            for row in rows:
                await i.deliveries.decide(agent, row["id"], True, row["revision"], row["fingerprint"])
            async with asyncio.timeout(5):
                while (await i.deliveries.get(agent, rows[1]["id"]))["error_code"] != "qq_cooldown":
                    await asyncio.sleep(0.01)
            assert len(transport.sent) == 1
            assert await i.deliveries.attempts(agent, rows[1]["id"]) == []
            status = await i.qq.status(agent)
            assert status["cooldowns"][0]["send_available_at"] > clock.now()
            clock.advance(3)
            await i.deliveries.tick()
            async with asyncio.timeout(5):
                while (await i.deliveries.get(agent, rows[1]["id"]))["state"] != "sent":
                    await asyncio.sleep(0.01)
            assert len(transport.sent) == 2 and len(await i.deliveries.attempts(agent, rows[1]["id"])) == 1

    asyncio.run(scenario())


def test_supervisor_reclaims_connection_when_configuration_is_disabled(tmp_path):
    async def scenario():
        async with opened(tmp_path) as (i, agent, cfg, _, transport, _, _, _):
            await asyncio.wait_for(transport.listening.wait(), 5)
            status = await i.qq.status(agent)
            assert status["connections"][0]["state"] == "connected"
            assert "ws://" not in json.dumps(status)
            await i.configurations.save(agent, cfg["id"], "disabled", False, cfg["value"], cfg["revision"])
            await asyncio.wait_for(transport.cancelled.wait(), 5)
            assert i.health()["ready"]  # Optional channel disconnection does not take the core down.

    asyncio.run(scenario())


@pytest.mark.parametrize("value", [True, 0, -1, "01", "1.0", "９９", str(2**63)])
def test_ids_are_strict(value):
    with pytest.raises(BusinessError):
        qq_id(value)


def test_parser_accepts_signed_message_ids_and_exact_mentions_only():
    assert parse_event(event(-3), "456").message_id == -3
    raw = event(scene="group")
    raw["message"] = [{"type": "text", "data": {"text": "[CQ:at,qq=456] hello"}}]
    assert parse_event(raw, "456") is None
    raw["message"] = [{"type": "at", "data": {"qq": "all"}}]
    assert parse_event(raw, "456") is None
    raw["message_id"] = True
    with pytest.raises(BusinessError):
        parse_event(raw, "456")
