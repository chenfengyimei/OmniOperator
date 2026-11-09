"""Isolated database, clock and trusted fake transport; never contacts an account."""

import datetime as dt
from contextlib import nullcontext

from omni.adapters.persistence.database import Database
from omni.adapters.primitives import UUIDs
from omni.application.delivery import DeliveryService
from omni.domain.delivery import DeliveryResult
from omni.domain.scheduling import instant, timestamp


class Clock:
    def __init__(self):
        self.value = "2026-11-04T00:00:00.000000+00:00"
        self.elapsed = 0.0

    def now(self):
        return self.value

    def monotonic(self):
        return self.elapsed

    def advance(self, seconds):
        self.value = timestamp(instant(self.value) + dt.timedelta(seconds=seconds))
        self.elapsed += seconds


class Observation:
    def context(self, **fields):
        return nullcontext()

    def span(self, name):
        return nullcontext()


class FakePort:
    identity = "fixture-qq/v1"

    def __init__(self):
        self.calls = []

    def account_key(self, configuration):
        return configuration["account"]

    async def send(self, message):
        self.calls.append(message)
        return DeliveryResult("sent", message.fingerprint, receipt_id="fixture-receipt")

    async def close(self, timeout_s):
        return True


async def setup(tmp_path, port=None):
    clock = Clock()
    db = Database(tmp_path / "omni.db", clock, UUIDs())
    await db.write(lambda u: u.initialize())
    agent = (await db.read(lambda u: u.query("agents", "")))[0]["id"]
    channel = await db.write(
        lambda u: u.configuration_create(
            agent,
            "channel",
            "Fixture",
            True,
            {"driver": "qq", "account": "account-one", "credential_ref": "fixture/key"},
        )
    )
    port = port or FakePort()
    service = DeliveryService(db, clock, Observation(), {"qq": port})
    return db, agent, channel, clock, port, service


async def output(db, agent, key="turn", text="outbound text"):
    turn = await db.write(lambda u: u.accept_turn(agent, None, key, "generate", {}))
    await db.write(lambda u: u.transition(agent, turn["run_id"], "running"))
    await db.write(
        lambda u: u.snapshot(agent, turn["run_id"], [u.get_run(agent, turn["run_id"])["input_message_id"]], [])
    )
    await db.write(lambda u: u.finish(agent, turn["run_id"], text))
    messages = await db.read(lambda u: u.messages(agent, turn["session_id"]))
    return turn, messages[-1]


async def descriptor(db, agent, target="isolated"):
    channel = await db.write(
        lambda u: u.configuration_create(
            agent,
            "channel",
            "Fixture",
            True,
            {"driver": "qq", "account": "account-one", "credential_ref": "fixture/key"},
        )
    )
    return {
        "config_id": channel["id"],
        "config_revision_id": channel["revision_id"],
        "adapter_id": FakePort.identity,
        "account_key": "account-one",
        "target": target,
    }
