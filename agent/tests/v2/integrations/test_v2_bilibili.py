"""V2 Bilibili publication consistency and authority; fake provider only, real isolated SQLite."""

from omni_test_support.v2.bilibili import (
    ADAPTER as ADAPTER,
)
from omni_test_support.v2.bilibili import (
    BV as BV,
)
from omni_test_support.v2.bilibili import (
    FORM as FORM,
)
from omni_test_support.v2.bilibili import (
    MENTIONS as MENTIONS,
)
from omni_test_support.v2.bilibili import (
    TEXT as TEXT,
)
from omni_test_support.v2.bilibili import (
    URL as URL,
)
from omni_test_support.v2.bilibili import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.bilibili import (
    DeliveryResult as DeliveryResult,
)
from omni_test_support.v2.bilibili import (
    Gateway as Gateway,
)
from omni_test_support.v2.bilibili import (
    Model as Model,
)
from omni_test_support.v2.bilibili import (
    ModelReply as ModelReply,
)
from omni_test_support.v2.bilibili import (
    Secrets as Secrets,
)
from omni_test_support.v2.bilibili import (
    asynccontextmanager as asynccontextmanager,
)
from omni_test_support.v2.bilibili import (
    asyncio as asyncio,
)
from omni_test_support.v2.bilibili import (
    connection as connection,
)
from omni_test_support.v2.bilibili import (
    func as func,
)
from omni_test_support.v2.bilibili import (
    json as json,
)
from omni_test_support.v2.bilibili import (
    mentions_value as mentions_value,
)
from omni_test_support.v2.bilibili import (
    open_instance as open_instance,
)
from omni_test_support.v2.bilibili import (
    opened as opened,
)
from omni_test_support.v2.bilibili import (
    pytest as pytest,
)
from omni_test_support.v2.bilibili import (
    s as s,
)
from omni_test_support.v2.bilibili import (
    select as select,
)
from omni_test_support.v2.bilibili import (
    target as target,
)
from omni_test_support.v2.bilibili import (
    terminal as terminal,
)
from omni_test_support.v2.bilibili import (
    update as update,
)
from omni_test_support.v2.bilibili import (
    uuid as uuid,
)


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
