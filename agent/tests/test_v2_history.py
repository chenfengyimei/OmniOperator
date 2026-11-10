"""History position allocation survives trimming, rollback, restart and offline recovery."""

import asyncio

import pytest
from sqlalchemy import delete, select, update
from test_v2_objects import Clock, opened

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.maintenance import backup, derived_indexes, restore
from omni.domain.contracts import BusinessError


def test_history_pages_bind_agents_runs_and_streams_with_bounded_continuation(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            other = await db.write(lambda u: u.create_agent("Other"))
            turns = [await db.write(lambda u, i=i: u.accept_turn(agent, None, str(i), "history", {})) for i in range(2)]
            first = await db.read(lambda u: u.history_page("events", agent, limit=1))
            assert first["has_more"] and len(first["items"]) == 1
            second = await db.read(lambda u: u.history_page("events", agent, cursor=first["next_cursor"], limit=3))
            assert not second["has_more"] and second["items"][0]["sequence"] > first["items"][0]["sequence"]
            empty = await db.read(lambda u: u.history_page("events", agent, cursor=second["next_cursor"]))
            assert not empty["items"] and empty["next_cursor"] == second["next_cursor"]
            filtered = await db.read(lambda u: u.history_page("events", agent, run_id=turns[0]["run_id"]))
            # Each accepted turn emits run.queued plus routing.selected.
            assert len(filtered["items"]) == 2 and all(
                item["run_id"] == turns[0]["run_id"] for item in filtered["items"]
            )
            for kind, owner, run_id in [
                ("audit", agent, ""),
                ("events", other["id"], ""),
                ("events", agent, turns[1]["run_id"]),
            ]:
                with pytest.raises(BusinessError) as failure:
                    await db.read(lambda u: u.history_page(kind, owner, cursor=filtered["next_cursor"], run_id=run_id))
                assert failure.value.code == "history_scope_mismatch"
            with pytest.raises(BusinessError) as failure:
                await db.read(lambda u: u.history_page("events", other["id"], run_id=turns[0]["run_id"]))
            assert failure.value.status == 404
            for limit in (0, 201, True):
                with pytest.raises(BusinessError) as failure:
                    await db.read(lambda u: u.history_page("audit", agent, limit=limit))
                assert failure.value.code == "invalid_history_query"

    asyncio.run(scenario())


def test_trim_revision_preserves_sparse_rows_and_allocation_never_rewinds(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            identities = [await db.write(lambda u, i=i: u.audit(agent, "fixture", None, {"i": i})) for i in range(3)]
            before = await db.read(lambda u: u.history_page("audit", agent))
            highest = before["stream"]["high_water"]

            def trim(u):
                u.connection.execute(delete(s.audit).where(s.audit.c.id.in_(identities[1:])))

            await db.write(trim)
            with pytest.raises(BusinessError) as failure:
                await db.read(lambda u: u.history_page("audit", agent, cursor=before["next_cursor"]))
            assert failure.value.code == "history_cursor_expired" and failure.value.status == 410
            fresh = await db.read(lambda u: u.history_page("audit", agent))
            assert fresh["history_trimmed"] and fresh["stream"]["deleted_count"] == 2
            assert identities[0] in {row["id"] for row in fresh["items"]}
            await db.write(lambda u: u.audit(agent, "fixture", None, {}))
            after = await db.read(lambda u: u.history_page("audit", agent, cursor=fresh["next_cursor"]))
            assert len(after["items"]) == 1 and after["items"][0]["sequence"] > highest
            for mutate in (
                lambda u: u.connection.execute(
                    update(s.history_sequences).where(s.history_sequences.c.kind == "audit").values(last_issued=0)
                ),
                lambda u: u.connection.execute(delete(s.history_sequences)),
                lambda u: u.connection.execute(
                    update(s.history_scopes).where(s.history_scopes.c.scope_id == agent).values(trim_revision=0)
                ),
            ):
                with pytest.raises(BusinessError) as failure:
                    await db.write(mutate)
                assert failure.value.code == "data_constraint"

    asyncio.run(scenario())


def test_event_empty_stream_and_rolled_back_allocations_preserve_run_sequence(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            turn = await db.write(lambda u: u.accept_turn(agent, None, "event", "event", {}))
            page = await db.read(lambda u: u.history_page("events", agent))
            old = page["stream"]["high_water"]
            await db.write(
                lambda u: u.connection.execute(delete(s.run_events).where(s.run_events.c.run_id == turn["run_id"]))
            )
            with pytest.raises(BusinessError) as failure:
                await db.read(lambda u: u.history_page("events", agent, cursor=page["next_cursor"]))
            assert failure.value.code == "history_cursor_expired"
            empty = await db.read(lambda u: u.history_page("events", agent))
            assert not empty["items"] and empty["stream"]["high_water"] == old

            def rollback(u):
                u.event(agent, turn["run_id"], "fixture", {})
                raise BusinessError("fixture_rollback", "Rollback")

            with pytest.raises(BusinessError, match="Rollback"):
                await db.write(rollback)
            assert (await db.read(lambda u: u.history_page("events", agent)))["stream"]["high_water"] == old
            await db.write(lambda u: u.event(agent, turn["run_id"], "fixture", {}))
            current = await db.read(lambda u: u.history_page("events", agent, cursor=empty["next_cursor"]))
            assert len(current["items"]) == 1 and current["items"][0]["sequence"] == old + 1
            assert current["items"][0]["seq"] == 3

    asyncio.run(scenario())


def test_offline_writers_recovery_epoch_and_restart_share_the_same_history(tmp_path):
    home, snapshot, recovered = tmp_path / "home", tmp_path / "backup", tmp_path / "recovered"

    async def original():
        async with opened(home, Clock()) as (paths, db, _, agent):
            await db.write(lambda u: u.accept_turn(agent, None, "active", "kept message", {}))
            return (
                paths,
                agent,
                await db.read(lambda u: u.history_page("audit", agent)),
                await db.read(lambda u: u.history_page("events", agent)),
            )

    paths, agent, audit, events = asyncio.run(original())
    derived_indexes(paths, rebuild=True)
    backup(paths, snapshot)
    restore(snapshot, recovered)

    async def compare():
        async with opened(home, Clock()) as (_, db, _, _):
            resumed = await db.read(lambda u: u.history_page("audit", agent, cursor=audit["next_cursor"]))
            assert any(row["kind"] == "indexes.rebuilt" for row in resumed["items"])
            assert all(row["sequence"] > audit["stream"]["high_water"] for row in resumed["items"])
        async with opened(recovered, Clock()) as (_, db, _, _):
            for kind, page in (("audit", audit), ("events", events)):
                with pytest.raises(BusinessError) as failure:
                    await db.read(lambda u: u.history_page(kind, agent, cursor=page["next_cursor"]))
                assert failure.value.code == "history_cursor_expired"
            new = await db.read(lambda u: u.history_page("events", agent))
            assert new["items"][-1]["sequence"] > events["stream"]["high_water"]
            assert new["items"][-1]["kind"] == "run.interrupted"
            assert (await db.integrity())["foreign_keys"] == 0

    asyncio.run(compare())


def test_instance_audit_empty_stream_keeps_sqlite_high_water(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, _):

            def append(u):
                owner = u.connection.execute(select(s.instances.c.owner_id)).scalar_one()
                u.connection.execute(
                    s.instance_audit.insert().values(
                        id=u.ids.new(),
                        created_at=u.clock.now(),
                        owner_id=owner,
                        operation_id=u.ids.new(),
                        kind="credential.requested",
                        outcome="requested",
                        data={},
                    )
                )

            await db.write(append)
            page = await db.read(lambda u: u.history_page("instance_audit"))
            assert page["stream"]["high_water"] > 0
            await db.write(lambda u: u.connection.execute(delete(s.instance_audit)))
            with pytest.raises(BusinessError) as failure:
                await db.read(lambda u: u.history_page("instance_audit", cursor=page["next_cursor"]))
            assert failure.value.code == "history_cursor_expired"
            empty = await db.read(lambda u: u.history_page("instance_audit"))
            assert not empty["items"] and empty["stream"]["high_water"] == page["stream"]["high_water"]
            await db.write(append)
            last = await db.read(lambda u: u.history_page("instance_audit", cursor=empty["next_cursor"]))
            assert len(last["items"]) == 1 and last["items"][0]["sequence"] > page["stream"]["high_water"]

    asyncio.run(scenario())


def test_exhausted_history_allocator_rolls_back_the_entire_turn(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            await db.write(
                lambda u: u.connection.execute(
                    update(s.history_sequences)
                    .where(s.history_sequences.c.kind == "events")
                    .values(last_issued=2**63 - 1)
                )
            )
            with pytest.raises(BusinessError) as failure:
                await db.write(lambda u: u.accept_turn(agent, None, "exhausted", "must roll back", {}))
            assert failure.value.code == "history_sequence_unavailable" and failure.value.status == 503
            assert not await db.read(lambda u: u.query("runs", agent))
            assert not await db.read(lambda u: u.query("sessions", agent))
            assert (await db.read(lambda u: u.history_page("events", agent)))["stream"]["high_water"] == 2**63 - 1

    asyncio.run(scenario())


def test_event_trim_is_isolated_by_agent_and_conservative_across_its_run_filters(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            other = await db.write(lambda u: u.create_agent("Other"))
            turns = [await db.write(lambda u, i=i: u.accept_turn(agent, None, str(i), "event", {})) for i in range(2)]
            foreign = await db.write(lambda u: u.accept_turn(other["id"], None, "other", "event", {}))
            first = await db.read(lambda u: u.history_page("events", agent, run_id=turns[0]["run_id"]))
            await db.write(
                lambda u: u.connection.execute(delete(s.run_events).where(s.run_events.c.run_id == foreign["run_id"]))
            )
            unchanged = await db.read(
                lambda u: u.history_page("events", agent, run_id=turns[0]["run_id"], cursor=first["next_cursor"])
            )
            assert not unchanged["items"] and not unchanged["history_trimmed"]
            await db.write(
                lambda u: u.connection.execute(delete(s.run_events).where(s.run_events.c.run_id == turns[1]["run_id"]))
            )
            with pytest.raises(BusinessError) as failure:
                await db.read(
                    lambda u: u.history_page("events", agent, run_id=turns[0]["run_id"], cursor=first["next_cursor"])
                )
            assert failure.value.code == "history_cursor_expired"
            fresh = await db.read(lambda u: u.history_page("events", agent, run_id=turns[0]["run_id"]))
            assert len(fresh["items"]) == 2 and fresh["stream"]["deleted_count"] == 2

    asyncio.run(scenario())
