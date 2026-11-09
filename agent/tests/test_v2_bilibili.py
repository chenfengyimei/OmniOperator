"""V2 Bilibili publication consistency and authority; fake provider only, real isolated SQLite."""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import func, select, update

from omni.adapters.persistence import schema as s
from omni.composition.instance import open_instance
from omni.domain.bilibili import ADAPTER, connection, mentions_value, target
from omni.domain.contracts import BusinessError, ModelReply
from omni.domain.delivery import DeliveryResult

BV = "BV1xx411c7mD"
URL = "https://www.bilibili.com/video/" + BV
FORM = {
    "account": "#account",
    "target": "#target",
    "editor": "#editor",
    "submit": "#submit",
    "records": ".record",
    "record_author": ".author",
    "record_text": ".body",
    "challenge": "#challenge",
    "logged_out": "#loggedout",
    "mention_input": "#mention-query",
    "mention_options": ".candidate",
    "mention_nodes": ".mention",
    "record_mentions": ".mention",
}
TEXT = "Hello😀 @Alice\nnext"
MENTIONS = [{"start": TEXT.index("@"), "end": TEXT.index("@") + 6, "uid": "222", "name": "Alice"}]


class Secrets:
    def get(self, reference):
        return None

    def set(self, reference, value):
        pass

    def delete(self, reference):
        pass


class Model:
    async def complete(self, messages, tools, settings):
        return ModelReply(TEXT, cost_micro=1)


class Gateway:
    identity = ADAPTER

    def __init__(self, status="sent"):
        self.calls = []
        self.authorize = None
        self.status = status

    def account_key(self, value):
        return connection(value)["account"]

    async def read(self, configuration, operation, target, limit=10):
        return {"uid": configuration["account"], "operation": operation, "target": target}

    async def send(self, message):
        spec = await self.authorize(message)
        self.calls.append((message, spec))
        return DeliveryResult(
            self.status,
            message.fingerprint,
            receipt_id="99" if self.status == "sent" else "",
            error_code="bilibili_publish_unknown" if self.status == "unknown" else "",
        )

    async def close(self, timeout_s=30):
        return True


async def terminal(i, agent, run_id):
    async with asyncio.timeout(8):
        while True:
            run = await i.application.get_run(agent, run_id)
            if run["state"] not in {"queued", "running", "waiting_approval", "cancelling"}:
                return run
            await asyncio.sleep(0.01)


@asynccontextmanager
async def opened(tmp_path, status="sent"):
    gateway = Gateway(status)
    i = await open_instance(
        tmp_path, model=Model(), capabilities=(), bilibili_backend=gateway, credential_backend=Secrets()
    )
    gateway.authorize = lambda message: i._database.read(lambda u: u.bilibili_outbound_authorized(message))
    try:
        agent = (await i.application.query("agents", ""))[0]["id"]
        from v2_configuration_helpers import model_instance

        await model_instance(i, agent)
        config = await i.configurations.create(
            agent,
            "channel",
            "Bili fixture",
            True,
            {"driver": "bilibili", "account": "456", "credential_ref": "fixture/bili"},
        )
        profile = await i.bilibili.save_profile(
            agent, config["id"], True, {"comment": FORM, "private_message": FORM}, 0
        )
        receipt = await i.application.submit_turn(agent, None, "fixture-source", "Draft source")
        assert (await terminal(i, agent, receipt["run_id"]))["state"] == "succeeded"
        message = (await i.application.messages(agent, receipt["session_id"]))[-1]
        yield i, agent, config, profile, message, gateway
    finally:
        assert (await i.close())["complete"]


@pytest.mark.parametrize("kind", ["comment", "private_message"])
def test_prepare_approve_and_send_uses_shared_outbox_and_exact_spec(tmp_path, kind):
    async def scenario():
        async with opened(tmp_path) as (i, agent, config, _, message, gateway):
            url = URL if kind == "comment" else "https://message.bilibili.com/#/whisper/mid123"
            plan = await i.bilibili.prepare(
                agent, message["id"], config["id"], kind, url, MENTIONS if kind == "comment" else [], "first"
            )
            row = plan["deliveries"][0]
            assert row["state"] == "awaiting_approval" and not gateway.calls
            assert row["publication"]["target"] == url
            replay = await i.bilibili.prepare(
                agent, message["id"], config["id"], kind, url, MENTIONS if kind == "comment" else [], "another-key"
            )
            assert replay["id"] == plan["id"] and len(await i.deliveries.page(agent)) == 1
            assert all("name" not in a for a in plan["mentions"])
            await i.deliveries.decide(agent, row["id"], True, row["revision"], row["fingerprint"])
            async with asyncio.timeout(8):
                while (await i.deliveries.get(agent, row["id"]))["state"] != "sent":
                    await asyncio.sleep(0.01)
            assert len(gateway.calls) == 1
            spec = gateway.calls[0][1]
            assert spec["target"] == url and spec["mentions"] == (MENTIONS if kind == "comment" else [])
            with pytest.raises(BusinessError):
                await gateway.authorize(gateway.calls[0][0])

    asyncio.run(scenario())


@pytest.mark.parametrize("revoke", ["profile", "channel", "credential", "source"])
def test_revocation_rejects_approval_and_cannot_publish(tmp_path, revoke):
    async def scenario():
        async with opened(tmp_path) as (i, agent, config, profile, message, gateway):
            plan = await i.bilibili.prepare(agent, message["id"], config["id"], "comment", URL, MENTIONS, "plan")
            if revoke == "profile":
                await i.bilibili.save_profile(agent, config["id"], False, profile["forms"], profile["revision"])
            elif revoke == "channel":
                await i.configurations.save(agent, config["id"], "disabled", False, config["value"], config["revision"])
            elif revoke == "credential":
                await i._database.write(lambda u: u.credential_begin("fixture/bili", "delete", str(uuid.uuid4())))
            else:
                await i._database.write(lambda u: u.erase_message(agent, message["id"]))
            row = plan["deliveries"][0]
            with pytest.raises(BusinessError):
                await i.deliveries.decide(agent, row["id"], True, row["revision"], row["fingerprint"])
            assert not gateway.calls
            if revoke == "source":
                saved = await i.bilibili.get(agent, plan["id"])
                assert saved["text"] == "" and "Alice" not in json.dumps(saved)

    asyncio.run(scenario())


def test_concurrent_semantic_replay_and_immutable_metadata(tmp_path):
    async def scenario():
        async with opened(tmp_path) as (i, agent, config, profile, message, _):
            results = await asyncio.gather(
                *(
                    i.bilibili.prepare(agent, message["id"], config["id"], "comment", URL, MENTIONS, str(n))
                    for n in range(8)
                )
            )
            assert len({r["id"] for r in results}) == 1
            count = await i._database.read(
                lambda u: u.connection.execute(select(func.count()).select_from(s.bilibili_publications)).scalar_one()
            )
            assert count == 1
            with pytest.raises(BusinessError):
                await i._database.write(
                    lambda u: u.connection.execute(
                        update(s.bilibili_profile_revisions)
                        .where(s.bilibili_profile_revisions.c.id == profile["revision_id"])
                        .values(forms={})
                    )
                )
            with pytest.raises(BusinessError):
                await i.bilibili.save_profile(agent, config["id"], True, {"comment": FORM}, 0)

    asyncio.run(scenario())


def test_unknown_publication_is_not_replayed(tmp_path):
    async def scenario():
        async with opened(tmp_path, "unknown") as (i, agent, config, _, message, gateway):
            p = await i.bilibili.prepare(agent, message["id"], config["id"], "comment", URL, [], "unknown")
            row = p["deliveries"][0]
            await i.deliveries.decide(agent, row["id"], True, row["revision"], row["fingerprint"])
            async with asyncio.timeout(8):
                while (await i.deliveries.get(agent, row["id"]))["state"] != "unknown":
                    await asyncio.sleep(0.01)
            for _ in range(3):
                await i.deliveries.tick()
            assert len(gateway.calls) == 1
            repeated = await i.bilibili.prepare(
                agent, message["id"], config["id"], "comment", URL, [], "new-request-key"
            )
            assert repeated["id"] == p["id"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "url",
    [
        "http://www.bilibili.com/video/" + BV,
        "https://www.bilibili.com.evil.test/video/" + BV,
        URL + "?token=secret",
        URL + "#other",
    ],
)
def test_target_validation_rejects_ambiguous_or_credential_urls(url):
    with pytest.raises(BusinessError):
        target("comment", url)


def test_mention_offsets_are_unicode_ranges_not_literal_name_replacement():
    assert mentions_value(TEXT, MENTIONS) == MENTIONS
    with pytest.raises(BusinessError):
        mentions_value(TEXT, [{**MENTIONS[0], "start": MENTIONS[0]["start"] + 1}])


def test_one_display_name_cannot_resolve_to_different_uids():
    text = "@Alice @Alice"
    with pytest.raises(BusinessError):
        mentions_value(
            text,
            [
                {"start": 0, "end": 6, "uid": "111", "name": "Alice"},
                {"start": 7, "end": 13, "uid": "222", "name": "Alice"},
            ],
        )


def test_preparation_tool_returns_references_without_copying_the_message_body(tmp_path):
    from omni.application.bilibili import PREPARE_SPEC
    from omni.domain.contracts import InvocationContext, Outcome

    async def scenario():
        async with opened(tmp_path) as (i, agent, config, _, message, _):
            run = await i.application.get_run(agent, message["run_id"])
            result = await i.bilibili.invoke(
                PREPARE_SPEC.name,
                {
                    "config_id": config["id"],
                    "message_id": message["id"],
                    "kind": "comment",
                    "target": URL,
                    "mentions": MENTIONS,
                    "key": "tool",
                },
                operation_id=str(uuid.uuid4()),
                context=InvocationContext(agent, run["id"], run["session_id"], run["policy_epoch"]),
            )
            assert result.status == Outcome.OK
            assert TEXT not in result.text and "Alice" not in result.text and TEXT not in json.dumps(result.data)
            assert result.data["publication_id"] == json.loads(result.text)["id"]

    asyncio.run(scenario())
