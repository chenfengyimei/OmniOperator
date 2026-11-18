"""Pinned calendar rules, durable due identities, FIFO claims and crash recovery."""

from omni_test_support.v2.scheduling import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.scheduling import (
    Clock as Clock,
)
from omni_test_support.v2.scheduling import (
    Cron as Cron,
)
from omni_test_support.v2.scheduling import (
    Database as Database,
)
from omni_test_support.v2.scheduling import (
    PinnedTimezones as PinnedTimezones,
)
from omni_test_support.v2.scheduling import (
    SchedulingService as SchedulingService,
)
from omni_test_support.v2.scheduling import (
    UUIDs as UUIDs,
)
from omni_test_support.v2.scheduling import (
    asyncio as asyncio,
)
from omni_test_support.v2.scheduling import (
    dt as dt,
)
from omni_test_support.v2.scheduling import (
    due_selection as due_selection,
)
from omni_test_support.v2.scheduling import (
    instant as instant,
)
from omni_test_support.v2.scheduling import (
    interval as interval,
)
from omni_test_support.v2.scheduling import (
    pytest as pytest,
)
from omni_test_support.v2.scheduling import (
    s as s,
)
from omni_test_support.v2.scheduling import (
    schedule_value as schedule_value,
)
from omni_test_support.v2.scheduling import (
    select as select,
)
from omni_test_support.v2.scheduling import (
    setup as setup,
)
from omni_test_support.v2.scheduling import (
    timestamp as timestamp,
)
from omni_test_support.v2.scheduling import (
    update as update,
)


@pytest.mark.parametrize(
    "zone,rule,boundary,expected",
    [
        ("America/New_York", "30 2 * * *", "2026-03-08T05:00:00+00:00", "2026-03-09T06:30:00+00:00"),
        ("America/New_York", "30 1 * * *", "2026-11-01T04:00:00+00:00", "2026-11-01T05:30:00+00:00"),
        ("America/New_York", "30 1 * * *", "2026-11-01T05:30:00+00:00", "2026-11-02T06:30:00+00:00"),
        ("UTC", "0 0 29 2 *", "2096-03-01T00:00:00+00:00", "2104-02-29T00:00:00+00:00"),
        ("Asia/Shanghai", "0 9 * * 1-5", "2026-11-06T02:00:00+00:00", "2026-11-09T01:00:00+00:00"),
    ],
)
def test_calendar_dst_leap_and_weekdays(zone, rule, boundary, expected):
    assert Cron.parse(rule).find(instant(boundary), PinnedTimezones().resolve(zone)) == instant(expected)


@pytest.mark.parametrize("value", ["0 0 31 2 *", "61 * * * *", "*/0 * * * *", "@daily", "* * * * MON"])
def test_invalid_or_impossible_cron_is_bounded(value):
    with pytest.raises(BusinessError):
        Cron.parse(value).find(instant("2026-01-01T00:00:00+00:00"), dt.timezone.utc)


@pytest.mark.parametrize("zone", ["../UTC", "../../config", "", "Europe/DoesNotExist"])
def test_timezone_files_are_restricted(zone):
    with pytest.raises(BusinessError, match="timezone"):
        PinnedTimezones().resolve(zone)


def test_latest_dst_does_not_execute_second_fold():
    value = schedule_value({"prompt": "check", "expression": "30 1 * * *", "timezone": "America/New_York"})
    result = due_selection(
        value, "2026-10-31T05:30:00+00:00", "2026-11-01T06:45:00+00:00", PinnedTimezones().resolve(value["timezone"])
    )
    assert result["scheduled_at"] == "2026-11-01T05:30:00.000000+00:00"
    assert result["next_at"] == "2026-11-02T06:30:00.000000+00:00"


def test_due_deduplication_restart_and_latest_coalescing(tmp_path):
    async def scenario():
        db, agent, clock, service = await setup(tmp_path)
        try:
            plan = await service.save(agent, "periodic", True, interval())
            clock.advance(601)
            counts = await asyncio.gather(*(service.tick() for _ in range(8)))
            assert sum(counts) == 1
            rows = await service.occurrences(agent, plan["id"])
            assert len(rows) == 1 and rows[0]["state"] == "enqueued"
            assert rows[0]["scheduled_at"] == "2026-11-03T00:10:00.000000+00:00"
            assert rows[0]["coalesced_from"] == "2026-11-03T00:01:00.000000+00:00"
            assert len(await db.read(lambda u: u.query("runs", agent))) == 1
            await db.write(lambda u: u.recover())
            assert await service.tick() == 0
            claim = (await db.write(lambda u: u.claim_runs(4)))[0]
            await db.write(lambda u: u.transition(agent, claim["id"], "running"))
            await db.write(lambda u: u.finish(agent, claim["id"], "done"))
            clock.advance(60)
            assert await service.tick() == 1
            assert [r["sequence"] for r in await service.occurrences(agent, plan["id"])] == [1, 2]
        finally:
            await db.close()

    asyncio.run(scenario())


def test_revision_pins_pending_intent_queue_full_and_disable(tmp_path):
    async def scenario():
        db, agent, clock, service = await setup(tmp_path)
        try:
            for n in range(64):
                await db.write(lambda u, n=n: u.accept_turn(agent, None, str(n), "occupy", {}))
            plan = await service.save(agent, "periodic", True, interval("old prompt"))
            clock.advance(60)
            assert await service.tick() == 0
            due = (await service.occurrences(agent, plan["id"]))[0]
            assert due["state"] == "blocked" and due["error_code"] == "queue_full" and due["run_id"] is None
            changed = await service.save(agent, "new", True, interval("new prompt"), plan["id"], 1)
            first = (await db.read(lambda u: u.query("runs", agent)))[0]
            await db.write(lambda u: u.cancel(agent, first["id"]))
            assert await service.tick() == 1
            due = (await service.occurrences(agent, plan["id"]))[0]
            run = await db.read(lambda u: u.get_run(agent, due["run_id"]))
            message = await db.read(lambda u: u._one(s.messages, agent, run["input_message_id"]))
            assert message["content"] == "old prompt"
            await service.save(agent, "disabled", False, changed["value"], plan["id"], 2)
            assert (await db.read(lambda u: u.get_run(agent, due["run_id"])))["state"] == "queued"
            with pytest.raises(BusinessError, match="changed"):
                await service.save(agent, "stale", False, changed["value"], plan["id"], 2)
        finally:
            await db.close()

    asyncio.run(scenario())


def test_unknown_fee_blocks_next_due_review_does_not_release_hold(tmp_path):
    async def scenario():
        db, agent, clock, service = await setup(tmp_path)
        try:
            plan = await service.save(agent, "periodic", True, interval())
            clock.advance(60)
            assert await service.tick() == 1
            due = (await service.occurrences(agent, plan["id"]))[0]
            run = due["run_id"]
            await db.write(lambda u: u.transition(agent, run, "running"))
            call = await db.write(lambda u: u.reserve_call(agent, run, 50, 100, "fake"))
            await db.write(lambda u: u.recover())
            clock.advance(600)
            assert await service.tick() == 0
            blocker = (await service.get(agent, plan["id"]))["blocker"]
            assert blocker["reason"] == "unknown"
            with pytest.raises(BusinessError, match="changed"):
                await service.review(agent, plan["id"], blocker["id"], "stale")
            await service.review(agent, plan["id"], blocker["id"], blocker["fingerprint"])
            assert await service.tick() == 1
            calls = await db.read(lambda u: u.query("calls", agent))
            assert calls[0]["id"] == call and calls[0]["state"] == "unknown"
            held = await db.read(lambda u: u.connection.execute(select(s.budget_accounts.c.held_micro)).scalar_one())
            assert held == 50
        finally:
            await db.close()

    asyncio.run(scenario())


def test_atomic_claim_fifo_fencing_expiry_and_safe_restart(tmp_path):
    async def scenario():
        db, agent, clock, _ = await setup(tmp_path)
        try:
            first = await db.write(lambda u: u.accept_turn(agent, None, "1", "first", {}))
            second = await db.write(lambda u: u.accept_turn(agent, first["session_id"], "2", "second", {}))
            other = await db.write(lambda u: u.accept_turn(agent, None, "3", "other", {}))
            results = await asyncio.gather(*(db.write(lambda u: u.claim_runs(4)) for _ in range(5)))
            claims = [r for batch in results for r in batch]
            assert [r["id"] for r in claims] == [first["run_id"], other["run_id"]]
            await db.write(lambda u: u.recover())
            again = await db.write(lambda u: u.claim_runs(4))
            assert again[0]["lease_fence"] > claims[0]["lease_fence"]
            with pytest.raises(BusinessError, match="owned"):
                await db.write(
                    lambda u: u.start_claimed_run(
                        agent, first["run_id"], claims[0]["claim_token"], claims[0]["lease_fence"]
                    )
                )
            await db.write(lambda u: u.transition(agent, first["run_id"], "running"))
            clock.advance(61)
            await db.write(lambda u: u.renew_run_leases())
            with pytest.raises(BusinessError, match="ownership"):
                await db.write(lambda u: u.finish(agent, first["run_id"], "late success"))
            assert await db.write(lambda u: u.claim_runs(4)) == []
            await db.write(lambda u: u.recover())
            assert (await db.read(lambda u: u.get_run(agent, first["run_id"])))["state"] == "interrupted"
            assert (await db.write(lambda u: u.claim_runs(4)))[0]["id"] == second["run_id"]
        finally:
            await db.close()

    asyncio.run(scenario())


def test_scope_revision_immutability_and_timezone_drift(tmp_path):
    async def scenario():
        db, agent, clock, service = await setup(tmp_path)
        try:
            plan = await service.save(agent, "periodic", True, interval())
            other = (await db.write(lambda u: u.create_agent("other")))["id"]
            with pytest.raises(BusinessError, match="exist"):
                await service.get(other, plan["id"])
            with pytest.raises(BusinessError, match="constraint"):
                await db.write(lambda u: u.connection.execute(update(s.schedule_revisions).values(prompt="tampered")))
            service._zones = type("Changed", (), {"version": "new-version", "resolve": PinnedTimezones().resolve})()
            clock.advance(60)
            assert await service.tick() == 0
            assert (await service.get(agent, plan["id"]))["progress"]["error_code"] == "timezone_data_changed"
            assert not await service.occurrences(agent, plan["id"])
        finally:
            await db.close()

    asyncio.run(scenario())


def test_admission_savepoint_preserves_due_but_not_partial_turn(tmp_path, monkeypatch):
    from omni.adapters.persistence.repository import Repositories

    async def scenario():
        db, agent, clock, service = await setup(tmp_path)
        original = Repositories.accept_turn
        try:
            plan = await service.save(agent, "periodic", True, interval())
            clock.advance(60)

            def fail(self, *args):
                original(self, *args)
                raise BusinessError("admission_aborted", "Injected failure after turn creation")

            monkeypatch.setattr(Repositories, "accept_turn", fail)
            assert await service.tick() == 0
            due = (await service.occurrences(agent, plan["id"]))[0]
            assert due["state"] == "blocked" and due["error_code"] == "admission_aborted"
            assert await db.read(lambda u: u.query("runs", agent)) == []
            assert await db.read(lambda u: u.messages(agent, plan["value"]["session_id"])) == []
            monkeypatch.setattr(Repositories, "accept_turn", original)
            assert await service.tick() == 1
            assert (await service.occurrences(agent, plan["id"]))[0]["id"] == due["id"]
        finally:
            await db.close()

    asyncio.run(scenario())


def test_late_unknown_delivery_gates_claim_and_execution_checkpoint(tmp_path):
    async def scenario():
        db, agent, clock, service = await setup(tmp_path)
        try:
            plan = await service.save(agent, "periodic", True, interval())
            clock.advance(60)
            await service.tick()
            one = (await service.occurrences(agent, plan["id"]))[0]
            await db.write(lambda u: u.transition(agent, one["run_id"], "running"))
            from omni_test_support.v2.v2_delivery_helpers import descriptor

            route = await descriptor(db, agent)
            await db.write(lambda u: u.snapshot(agent, one["run_id"], [], []))
            await db.write(lambda u: u.finish(agent, one["run_id"], "done", route))
            old = (await db.read(lambda u: u.delivery_page(agent)))[0]
            clock.advance(60)
            assert await service.tick() == 0
            assert (await service.get(agent, plan["id"]))["blocker"]["reason"] == "delivery_pending"
            await db.write(lambda u: u.delivery_interrupt(agent, old["id"]))
            assert await service.tick() == 1
            late = await db.write(
                lambda u: u.delivery_create(
                    agent,
                    old["message_id"],
                    route["config_id"],
                    "late-target",
                    "late-key",
                    route["adapter_id"],
                    route["account_key"],
                    route["config_revision_id"],
                )
            )
            await db.write(lambda u: u.delivery_decide(agent, late["id"], True, late["revision"], late["fingerprint"]))
            attempt = await db.write(
                lambda u: u.delivery_claim(agent, late["id"], route["adapter_id"], route["account_key"])
            )
            await db.write(
                lambda u: u.delivery_start(
                    agent, attempt["id"], attempt["token"], route["adapter_id"], route["account_key"]
                )
            )
            await db.write(
                lambda u: u.delivery_settle(
                    agent,
                    attempt["id"],
                    attempt["token"],
                    {
                        "status": "unknown",
                        "fingerprint": late["fingerprint"],
                        "error_code": "fixture_unknown",
                        "receipt_id": "",
                        "retryable": False,
                    },
                )
            )
            assert await db.write(lambda u: u.claim_runs(4)) == []
            blocker = (await service.get(agent, plan["id"]))["blocker"]
            await service.review(agent, plan["id"], blocker["id"], blocker["fingerprint"])
            claim = (await db.write(lambda u: u.claim_runs(4)))[0]
            await db.write(lambda u: u.transition(agent, claim["id"], "running"))
            # A genuinely new unresolved intent after review must stop a later checkpoint.
            await db.write(lambda u: u.reserve_call(agent, one["run_id"], 5, 100, "late"))
            with pytest.raises(BusinessError, match="previous scheduled"):
                await db.read(lambda u: u.assert_context(agent, claim["id"]))
        finally:
            await db.close()

    asyncio.run(scenario())


def test_once_cancel_and_backward_clock_never_reuse_due_identity(tmp_path):
    async def scenario():
        db, agent, clock, service = await setup(tmp_path)
        try:
            value = {"kind": "once", "expression": "", "start_at": "2026-11-03T00:01:00+00:00", "prompt": "once"}
            plan = await service.save(agent, "once", True, value)
            clock.advance(60)
            assert await service.tick() == 1
            due = (await service.occurrences(agent, plan["id"]))[0]
            await service.cancel(agent, plan["id"], due["id"])
            assert (await db.read(lambda u: u.get_run(agent, due["run_id"])))["state"] == "cancelled"
            clock.advance(3600)
            assert await service.tick() == 0
            assert (await service.get(agent, plan["id"]))["progress"]["next_at"] is None
            clock.advance(-3601)
            changed = await service.save(agent, "interval", True, interval(), plan["id"], 1)
            assert changed["progress"]["next_at"] > due["scheduled_at"]
            assert len(await service.occurrences(agent, plan["id"])) == 1
            await service.archive(agent, plan["id"], 2)
            clock.advance(3600)
            assert await service.tick() == 0
        finally:
            await db.close()

    asyncio.run(scenario())


def test_supervised_scheduler_executes_through_model_runtime_and_closes(tmp_path):
    from omni_test_support.v2.v2_configuration_helpers import model_instance

    from omni.composition.instance import open_instance
    from omni.domain.contracts import ModelReply

    class Model:
        calls = 0

        async def complete(self, messages, tools, settings):
            self.calls += 1
            assert messages[-1]["content"] == "scheduled"
            return ModelReply("scheduled reply", cost_micro=0)

    async def scenario():
        clock, model = Clock(), Model()
        instance = await open_instance(tmp_path, model=model, capabilities=(), clock=clock)
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            await model_instance(instance, agent)
            plan = await instance.schedules.save(agent, "periodic", True, interval())
            clock.advance(60)
            await instance.schedules.tick()
            async with asyncio.timeout(10):
                while True:
                    due = await instance.schedules.occurrences(agent, plan["id"])
                    if due and due[0]["run_state"] == "succeeded":
                        break
                    await asyncio.sleep(0.01)
            assert model.calls == 1 and instance.health()["schedules"]["ready"]
            assert [m["content"] for m in await instance.application.messages(agent, plan["value"]["session_id"])] == [
                "scheduled",
                "scheduled reply",
            ]
        finally:
            assert (await instance.close())["complete"]
            assert not instance.schedules.health()["ready"]

    asyncio.run(scenario())


@pytest.mark.parametrize("value", [{"session_id": ""}, {"interval_seconds": True}, {"prompt": " "}])
def test_schedule_value_rejects_invalid_identity_and_non_strict_numbers(value):
    with pytest.raises(BusinessError):
        schedule_value({**interval(), **value})


def test_claim_with_external_intent_cannot_be_replayed_after_restart(tmp_path):
    async def scenario():
        db, agent, _, _ = await setup(tmp_path)
        try:
            receipt = await db.write(lambda u: u.accept_turn(agent, None, "claim", "hello", {}))
            await db.write(lambda u: u.claim_runs(1))
            await db.write(lambda u: u.reserve_call(agent, receipt["run_id"], 10, 100, "fake"))
            await db.write(lambda u: u.recover())
            run = await db.read(lambda u: u.get_run(agent, receipt["run_id"]))
            assert run["state"] == "interrupted" and run["error_code"] == "claim_has_external_intent"
            assert await db.write(lambda u: u.claim_runs(4)) == []
            assert (await db.read(lambda u: u.query("calls", agent)))[0]["state"] == "unknown"
        finally:
            await db.close()

    asyncio.run(scenario())


def test_archived_schedule_keeps_inflight_occurrence_cancellable(tmp_path):
    async def scenario():
        db, agent, clock, service = await setup(tmp_path)
        try:
            plan = await service.save(agent, "archive", True, interval())
            clock.advance(60)
            await service.tick()
            due = (await service.occurrences(agent, plan["id"]))[0]
            await service.archive(agent, plan["id"], 1)
            assert await service.page(agent) == []
            archived = await service.page(agent, include_archived=True)
            assert archived[0]["id"] == plan["id"] and archived[0]["deleted_at"]
            assert (await service.occurrences(agent, plan["id"]))[0]["run_state"] == "queued"
            await service.cancel(agent, plan["id"], due["id"])
            assert (await db.read(lambda u: u.get_run(agent, due["run_id"])))["state"] == "cancelled"
            clock.advance(600)
            assert await service.tick() == 0
        finally:
            await db.close()

    asyncio.run(scenario())
