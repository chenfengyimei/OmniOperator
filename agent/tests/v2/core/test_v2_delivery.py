"""Outbound authority, durable ownership, honest retries and unknown-result review."""

from omni_test_support.v2.delivery import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.delivery import (
    DeliveryResult as DeliveryResult,
)
from omni_test_support.v2.delivery import (
    FakePort as FakePort,
)
from omni_test_support.v2.delivery import (
    asyncio as asyncio,
)
from omni_test_support.v2.delivery import (
    drain as drain,
)
from omni_test_support.v2.delivery import (
    func as func,
)
from omni_test_support.v2.delivery import (
    output as output,
)
from omni_test_support.v2.delivery import (
    pytest as pytest,
)
from omni_test_support.v2.delivery import (
    s as s,
)
from omni_test_support.v2.delivery import (
    select as select,
)
from omni_test_support.v2.delivery import (
    setup as setup,
)


def test_approval_exact_target_and_provider_acceptance(tmp_path):
    async def scenario():
        db, agent, channel, _, port, service = await setup(tmp_path)
        try:
            turn, message = await output(db, agent)
            row = await service.create(agent, message["id"], channel["id"], "private:target", "intent-one")
            assert row["state"] == "awaiting_approval" and row["text"] == "outbound text"
            assert (await service.create(agent, message["id"], channel["id"], "private:target", "intent-one"))[
                "id"
            ] == row["id"]
            with pytest.raises(BusinessError, match="different outbound"):
                await service.create(agent, message["id"], channel["id"], "other-target", "intent-one")
            await service.tick()
            assert port.calls == []
            with pytest.raises(BusinessError):
                await service.decide(agent, row["id"], True, row["revision"], "wrong")
            row = await service.decide(agent, row["id"], True, row["revision"], row["fingerprint"])
            await service.tick()
            await drain(service)
            result = await service.get(agent, row["id"])
            assert result["state"] == "sent" and len(port.calls) == 1
            assert (
                port.calls[0].target == "private:target"
                and port.calls[0].configuration["credential_ref"] == "fixture/key"
            )
            attempts = await service.attempts(agent, row["id"])
            assert attempts[0]["state"] == "sent" and attempts[0]["results"][0]["receipt_id"] == "fixture-receipt"
            assert "token" not in attempts[0]
            assert (await db.read(lambda u: u.get_run(agent, turn["run_id"])))["state"] == "succeeded"
        finally:
            await service.close()
            await db.close()

    asyncio.run(scenario())


def test_approval_compare_and_swap_and_blocked_parent_reissue(tmp_path):
    async def scenario():
        db, agent, channel, clock, port, service = await setup(tmp_path)
        try:
            _, message = await output(db, agent)
            row = await service.create(agent, message["id"], channel["id"], "target", "key")
            results = await asyncio.gather(
                *(service.decide(agent, row["id"], True, row["revision"], row["fingerprint"]) for _ in range(2)),
                return_exceptions=True,
            )
            assert sum(isinstance(r, BusinessError) for r in results) == 1
            clock.advance(301)
            await service.tick()
            assert (await service.get(agent, row["id"]))["state"] == "blocked"
            new = await service.create(agent, message["id"], channel["id"], "target", "new-key", row["id"])
            assert (await service.get(agent, row["id"]))["state"] == "cancelled"
            await service.decide(agent, new["id"], True, new["revision"], new["fingerprint"])
            await service.tick()
            await drain(service)
            assert (await service.get(agent, new["id"]))["state"] == "sent" and len(port.calls) == 1
        finally:
            await service.close()
            await db.close()

    asyncio.run(scenario())


def test_persistent_global_queue_and_active_limits(tmp_path):
    async def scenario():
        db, agent, channel, _, _, service = await setup(tmp_path)
        try:
            _, message = await output(db, agent)
            for i in range(64):
                await service.create(agent, message["id"], channel["id"], f"target-{i}", f"key-{i}")
            with pytest.raises(BusinessError, match="queue is full"):
                await service.create(agent, message["id"], channel["id"], "overflow", "overflow")
            for row in await service.page(agent):
                await service.cancel(agent, row["id"])
            for i in range(5):
                config = await db.write(
                    lambda u: u.configuration_create(
                        agent, "channel", f"Account {i}", True, {**channel["value"], "account": f"account-{i}"}
                    )
                )
                row = await service.create(agent, message["id"], config["id"], "target", f"active-{i}")
                await service.decide(agent, row["id"], True, row["revision"], row["fingerprint"])
                claim = await db.write(lambda u: u.delivery_claim(agent, row["id"], FakePort.identity, f"account-{i}"))
                assert (claim is not None) == (i < 4)
            assert (
                await db.read(
                    lambda u: u.connection.execute(
                        select(func.count())
                        .select_from(s.dispatch_resources)
                        .where(s.dispatch_resources.c.state == "held")
                    ).scalar_one()
                )
                == 4
            )
        finally:
            await service.close()
            await db.close()

    asyncio.run(scenario())


def test_known_not_sent_has_bounded_backoff_and_immutable_attempt_history(tmp_path):
    class Port(FakePort):
        async def send(self, message):
            self.calls.append(message)
            return DeliveryResult("not_sent", message.fingerprint, error_code="fixture_busy", retryable=True)

    async def scenario():
        db, agent, channel, clock, port, service = await setup(tmp_path, Port())
        try:
            _, message = await output(db, agent)
            row = await service.create(agent, message["id"], channel["id"], "target", "retry")
            await service.decide(agent, row["id"], True, row["revision"], row["fingerprint"])
            for delay in (0, 2, 4):
                clock.advance(delay)
                await service.tick()
                await drain(service)
                await service.tick()
            assert len(port.calls) == 3 and (await service.get(agent, row["id"]))["state"] == "blocked"
            attempts = await service.attempts(agent, row["id"])
            assert [a["seq"] for a in attempts] == [1, 2, 3]
            assert all(a["state"] == "not_sent" for a in attempts)
            assert len({a.token for a in port.calls}) == 3
            clock.advance(100)
            await service.tick()
            assert len(port.calls) == 3
        finally:
            await service.close()
            await db.close()

    asyncio.run(scenario())


def test_unknown_cannot_retry_review_never_releases_model_fees(tmp_path):
    class Port(FakePort):
        async def send(self, message):
            self.calls.append(message)
            raise RuntimeError("fixture-private-canary")

    async def scenario():
        db, agent, channel, clock, port, service = await setup(tmp_path, Port())
        try:
            turn, message = await output(db, agent)
            await db.write(lambda u: u.reserve_call(agent, turn["run_id"], 50, 100, "fake"))
            row = await service.create(agent, message["id"], channel["id"], "target", "unknown")
            await service.decide(agent, row["id"], True, row["revision"], row["fingerprint"])
            await service.tick()
            await drain(service)
            unknown = await service.get(agent, row["id"])
            assert unknown["state"] == "unknown"
            clock.advance(31)
            await service.tick()
            assert len(port.calls) == 1
            reviewed = await service.review(
                agent,
                row["id"],
                unknown["revision"],
                unknown["review_fingerprint"],
                "allow_future",
                "Checked fake provider; original result still unknown",
            )
            assert reviewed["state"] == "unknown" and reviewed["review_valid"]
            assert (
                await db.read(lambda u: u.connection.execute(select(s.budget_accounts.c.held_micro)).scalar_one()) == 50
            )
            with pytest.raises(BusinessError, match="acknowledge"):
                await service.create(agent, message["id"], channel["id"], "target", "new", row["id"])
            new = await service.create(agent, message["id"], channel["id"], "target", "new", row["id"], True)
            assert new["id"] != row["id"] and new["state"] == "awaiting_approval"
        finally:
            await service.close()
            await db.close()

    asyncio.run(scenario())
