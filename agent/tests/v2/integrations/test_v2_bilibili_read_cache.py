"""Bilibili owner read cache: stable-endpoint allowlist, credential generations, TTL/LRU bounds."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from omni_test_support.v2.bilibili import BV, opened
from sqlalchemy import update

from omni.adapters.persistence import schema as s
from omni.application.bilibili import READ_SPEC, BilibiliService
from omni.domain.bilibili import (
    READ_CACHE_MAX_ENTRY_BYTES,
    READ_CACHE_TTL_S,
    ReadCache,
    read_cache_key,
)
from omni.domain.contracts import BusinessError, InvocationContext, Outcome

NOT_CACHED = (
    "account",
    "comments",
    "replies",
    "search",
    "relation",
    "danmaku",
    "popular",
    "ranking",
    "following",
    "favorites",
    "tags",
    "history",
    "msgfeed",
)
OTHER_BV = "BV1yy411c7mE"
THIRD_BV = "BV1zz411c7mF"


def watched(gateway):
    calls = []
    original = gateway.read

    async def read(configuration, operation, target, limit=10):
        calls.append((operation, target))
        return await original(configuration, operation, target, limit)

    gateway.read = read
    return calls


async def authority(i, agent, config_id):
    return (await i._database.read(lambda u: u.bilibili_read_authorized(agent, config_id)))["authority"]


async def rotate_credential(i):
    await i._database.write(
        lambda u: u.connection.execute(
            update(s.credential_references).values(revision=s.credential_references.c.revision + 1)
        )
    )


def test_only_reference_stable_operations_are_cacheable():
    assert dict(READ_CACHE_TTL_S) == {"video": 86400.0, "whois": 86400.0}
    enum = READ_SPEC.parameters["properties"]["operation"]["enum"]
    assert set(enum) - set(READ_CACHE_TTL_S) == set(NOT_CACHED)


def test_cache_key_binds_owner_configuration_and_credential_generation():
    base = read_cache_key("agent", "config", "authority", "video", BV, 10)
    assert base == ("agent", "config", "authority", "video", BV, 10)
    variants = [
        read_cache_key("other-agent", "config", "authority", "video", BV, 10),
        read_cache_key("agent", "other-config", "authority", "video", BV, 10),
        read_cache_key("agent", "config", "other-authority", "video", BV, 10),
        read_cache_key("agent", "config", "authority", "whois", BV, 10),
        read_cache_key("agent", "config", "authority", "video", OTHER_BV, 10),
        read_cache_key("agent", "config", "authority", "video", BV, 20),
    ]
    assert len({base, *variants}) == 7


@pytest.mark.parametrize(
    "scope",
    [
        ("", "config", "authority", "video", BV, 10),
        ("agent", "", "authority", "video", BV, 10),
        ("agent", "config", "", "video", BV, 10),
        ("agent", "config", "authority", "", BV, 10),
        ("agent", "config", "authority", "video", None, 10),
        ("agent", "config", "authority", "video", BV, 0),
        ("agent", "config", "authority", "video", BV, True),
        (None, "config", "authority", "video", BV, 10),
    ],
)
def test_cache_key_rejects_incomplete_scopes(scope):
    with pytest.raises(ValueError):
        read_cache_key(*scope)


def test_snapshot_is_returned_as_an_independent_copy():
    cache = ReadCache()
    key = read_cache_key("agent", "config", "authority", "video", BV, 10)
    assert cache.put(key, {"title": "fixture", "n": 1}, 0.0, 10.0)
    first = cache.get(key, 0.0)
    assert first == {"title": "fixture", "n": 1} and first is not None
    first["title"] = "mutated"
    assert cache.get(key, 0.0) == {"title": "fixture", "n": 1}


def test_entry_expires_exactly_at_its_ttl():
    cache = ReadCache()
    key = read_cache_key("agent", "config", "authority", "video", BV, 10)
    assert cache.put(key, {"n": 1}, 5.0, 10.0)
    assert cache.get(key, 14.999) == {"n": 1}
    assert cache.get(key, 15.0) is None
    assert cache.entries == 0 and cache.bytes == 0


def test_lru_evicts_the_least_recently_used_entry():
    cache = ReadCache(max_entries=2)
    keys = [read_cache_key("agent", "config", "authority", "video", target, 10) for target in (BV, OTHER_BV, THIRD_BV)]
    for key in keys[:2]:
        assert cache.put(key, {"target": key[4]}, 0.0, 60.0)
    assert cache.get(keys[0], 0.0) is not None
    assert cache.put(keys[2], {"target": keys[2][4]}, 0.0, 60.0)
    assert cache.get(keys[1], 0.0) is None
    assert cache.get(keys[0], 0.0) is not None and cache.get(keys[2], 0.0) is not None


def test_byte_budget_evicts_until_the_snapshot_fits():
    cache = ReadCache(max_entries=10, max_bytes=200)
    keys = [read_cache_key("agent", "config", "authority", "video", f"target-{n}", 10) for n in range(3)]
    for key in keys:
        assert cache.put(key, {"payload": "x" * 80}, 0.0, 60.0)
    assert cache.bytes <= 200 and cache.entries == 2
    assert cache.get(keys[0], 0.0) is None


def test_oversized_or_non_json_values_are_never_stored():
    cache = ReadCache()
    key = read_cache_key("agent", "config", "authority", "video", BV, 10)
    assert not cache.put(key, {"payload": "x" * READ_CACHE_MAX_ENTRY_BYTES}, 0.0, 60.0)
    assert not cache.put(key, {"payload": {1, 2}}, 0.0, 60.0)
    assert cache.entries == 0 and cache.bytes == 0


def test_retired_generations_are_dropped_when_the_current_one_is_observed():
    cache = ReadCache()
    old = read_cache_key("agent", "config", "generation-1", "video", BV, 10)
    new = read_cache_key("agent", "config", "generation-2", "video", BV, 10)
    other_config = read_cache_key("agent", "config-2", "generation-1", "video", BV, 10)
    other_agent = read_cache_key("agent-2", "config", "generation-1", "video", BV, 10)
    for key in (old, other_config, other_agent):
        assert cache.put(key, {"scope": key[1]}, 0.0, 60.0)
    assert cache.discard_generations("agent", "config", "generation-2") == 1
    assert cache.get(old, 0.0) is None
    assert cache.get(other_config, 0.0) is not None and cache.get(other_agent, 0.0) is not None
    assert cache.put(new, {"scope": "new"}, 0.0, 60.0)
    assert cache.discard_generations("agent", "config", "generation-2") == 0


def test_discard_scope_drops_every_generation_of_one_configuration():
    cache = ReadCache()
    first = read_cache_key("agent", "config", "generation-1", "video", BV, 10)
    second = read_cache_key("agent", "config", "generation-2", "whois", BV, 10)
    other = read_cache_key("agent", "config-2", "generation-1", "video", BV, 10)
    for key in (first, second, other):
        assert cache.put(key, {"scope": key[1]}, 0.0, 60.0)
    assert cache.discard_scope("agent", "config") == 2
    assert cache.get(first, 0.0) is None and cache.get(second, 0.0) is None
    assert cache.get(other, 0.0) is not None


def test_non_object_snapshots_are_dropped_defensively():
    cache = ReadCache()
    key = read_cache_key("agent", "config", "authority", "video", BV, 10)
    cache._entries[key] = (10.0, 3, "[1]")
    cache._bytes = 3
    assert cache.get(key, 0.0) is None
    assert cache.entries == 0 and cache.bytes == 0


def test_clear_releases_every_snapshot():
    cache = ReadCache()
    assert cache.put(read_cache_key("agent", "config", "authority", "video", BV, 10), {"n": 1}, 0.0, 60.0)
    assert cache.entries == 1 and cache.bytes > 0
    cache.clear()
    assert cache.entries == 0 and cache.bytes == 0


@pytest.mark.parametrize("bounds", [(0, 10), (10, 0), (True, 10), (10, -1)])
def test_constructor_rejects_invalid_bounds(bounds):
    with pytest.raises(ValueError):
        ReadCache(*bounds)


@pytest.mark.parametrize("ttl", [0, -1.0, float("inf"), float("nan"), True])
def test_put_rejects_invalid_ttl(ttl):
    with pytest.raises(ValueError):
        ReadCache().put(read_cache_key("agent", "config", "authority", "video", BV, 10), {"n": 1}, 0.0, ttl)


def test_video_snapshot_serves_repeat_reads(tmp_path):
    async def scenario():
        async with opened(tmp_path) as (i, agent, config, _profile, _message, gateway):
            calls = watched(gateway)
            first = await i.bilibili.read(agent, config["id"], "video", BV)
            second = await i.bilibili.read(agent, config["id"], "video", BV)
            assert first == second and first["target"] == BV and first["operation"] == "video"
            assert len(calls) == 1
            assert i.bilibili.health()["read_cache_entries"] == 1
            assert i.bilibili.health()["read_cache_bytes"] > 0

    asyncio.run(scenario())


def test_whois_snapshot_serves_repeat_reads(tmp_path):
    async def scenario():
        async with opened(tmp_path) as (i, agent, config, _profile, _message, gateway):
            calls = watched(gateway)
            first = await i.bilibili.read(agent, config["id"], "whois", "222")
            second = await i.bilibili.read(agent, config["id"], "whois", "222")
            assert first == second and first["operation"] == "whois"
            assert len(calls) == 1
            assert i.bilibili.health()["read_cache_entries"] == 1

    asyncio.run(scenario())


def test_every_other_operation_reads_live(tmp_path):
    async def scenario():
        async with opened(tmp_path) as (i, agent, config, _profile, _message, gateway):
            calls = watched(gateway)
            for operation in NOT_CACHED:
                before = len(calls)
                for _ in range(2):
                    await i.bilibili.read(agent, config["id"], operation, "222")
                assert len(calls) - before == 2, operation
                assert i.bilibili.health()["read_cache_entries"] == 0, operation

    asyncio.run(scenario())


def test_rotated_credential_purges_older_snapshots(tmp_path):
    async def scenario():
        async with opened(tmp_path) as (i, agent, config, _profile, _message, gateway):
            calls = watched(gateway)
            assert (await i.bilibili.read(agent, config["id"], "video", BV))["target"] == BV
            before = await authority(i, agent, config["id"])
            await rotate_credential(i)
            after = await authority(i, agent, config["id"])
            assert before != after
            assert (await i.bilibili.read(agent, config["id"], "video", OTHER_BV))["target"] == OTHER_BV
            assert len(calls) == 2
            assert i.bilibili.health()["read_cache_entries"] == 1
            assert (await i.bilibili.read(agent, config["id"], "video", BV))["target"] == BV
            assert len(calls) == 3
            assert i.bilibili.health()["read_cache_entries"] == 2

    asyncio.run(scenario())


def test_cache_entries_are_scoped_to_one_configuration(tmp_path):
    async def scenario():
        async with opened(tmp_path) as (i, agent, config, _profile, _message, gateway):
            await i._database.write(lambda u: u.credential_register("fixture/bili"))
            other = await i.configurations.create(
                agent,
                "channel",
                "Bili second",
                True,
                {"driver": "bilibili", "account": "456", "credential_ref": "fixture/bili"},
            )
            calls = watched(gateway)
            await i.bilibili.read(agent, config["id"], "video", BV)
            await i.bilibili.read(agent, other["id"], "video", BV)
            assert len(calls) == 2
            assert i.bilibili.health()["read_cache_entries"] == 2
            await i.bilibili.read(agent, config["id"], "video", BV)
            assert len(calls) == 2

    asyncio.run(scenario())


def test_paused_account_never_serves_a_snapshot(tmp_path):
    async def scenario():
        async with opened(tmp_path) as (i, agent, config, _profile, _message, gateway):
            calls = watched(gateway)
            assert (await i.bilibili.read(agent, config["id"], "video", BV))["target"] == BV
            await i._database.write(lambda u: u.bilibili_risk_pause(agent, config["id"], 12015))
            with pytest.raises(BusinessError) as paused:
                await i.bilibili.read(agent, config["id"], "video", BV)
            assert paused.value.code == "bilibili_risk_paused"
            assert len(calls) == 1
            await i.bilibili.resume(agent, config["id"])
            assert (await i.bilibili.read(agent, config["id"], "video", BV))["target"] == BV
            assert len(calls) == 1

    asyncio.run(scenario())


def test_failed_reads_are_never_cached(tmp_path):
    async def scenario():
        async with opened(tmp_path) as (i, agent, config, _profile, _message, gateway):
            attempts = []
            original = gateway.read

            async def flaky(configuration, operation, target, limit=10):
                attempts.append(operation)
                if len(attempts) == 1:
                    raise BusinessError("bilibili_read_unavailable", "fixture outage", status=503)
                return await original(configuration, operation, target, limit)

            gateway.read = flaky
            with pytest.raises(BusinessError) as failed:
                await i.bilibili.read(agent, config["id"], "video", BV)
            assert failed.value.code == "bilibili_read_unavailable"
            assert i.bilibili.health()["read_cache_entries"] == 0
            assert (await i.bilibili.read(agent, config["id"], "video", BV))["target"] == BV
            assert len(attempts) == 2
            assert i.bilibili.health()["read_cache_entries"] == 1

    asyncio.run(scenario())


def test_owner_revocation_is_checked_before_a_cached_snapshot_is_served(tmp_path):
    async def scenario():
        async with opened(tmp_path) as (i, agent, config, _profile, _message, gateway):
            calls = watched(gateway)
            assert (await i.bilibili.read(agent, config["id"], "video", BV))["target"] == BV
            current = await i.configurations.get(agent, config["id"])
            await i.configurations.save(
                agent,
                config["id"],
                current["name"],
                False,
                current["value"],
                current["revision"],
            )
            with pytest.raises(BusinessError) as revoked:
                await i.bilibili.read(agent, config["id"], "video", BV)
            assert revoked.value.code == "bilibili_configuration_revoked"
            assert len(calls) == 1

    asyncio.run(scenario())


def test_ttl_expiry_rereads_after_the_injected_clock_passes(tmp_path):
    async def scenario():
        async with opened(tmp_path) as (i, agent, config, _profile, _message, gateway):
            calls = watched(gateway)
            now = [1000.0]
            service = BilibiliService(i._database, gateway, clock=lambda: now[0])
            assert (await service.read(agent, config["id"], "video", BV))["target"] == BV
            now[0] += 86_399.0
            assert (await service.read(agent, config["id"], "video", BV))["target"] == BV
            assert len(calls) == 1
            now[0] += 1.0
            assert (await service.read(agent, config["id"], "video", BV))["target"] == BV
            assert len(calls) == 2
            assert service.health()["read_cache_entries"] == 1
            service.stop_accepting()
            assert service.health()["read_cache_entries"] == 0

    asyncio.run(scenario())


def test_model_tool_invocations_reuse_the_snapshot(tmp_path):
    async def scenario():
        async with opened(tmp_path) as (i, agent, config, _profile, _message, gateway):
            calls = watched(gateway)
            context = InvocationContext(
                agent_id=agent, run_id=str(uuid.uuid4()), session_id=str(uuid.uuid4()), policy_revision=1
            )
            arguments = {"config_id": config["id"], "operation": "video", "target": BV, "limit": 10}
            first = await i.bilibili.invoke("bilibili_read", arguments, operation_id="op-1", context=context)
            second = await i.bilibili.invoke("bilibili_read", arguments, operation_id="op-2", context=context)
            assert first.status is Outcome.OK and second.status is Outcome.OK
            assert first.text == second.text
            assert len(calls) == 1

    asyncio.run(scenario())
