"""Reusable bilibili fixtures, extracted without collecting test cases."""

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
        from omni_test_support.v2.v2_configuration_helpers import model_instance

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


__all__ = [
    "ADAPTER",
    "BV",
    "BusinessError",
    "DeliveryResult",
    "FORM",
    "Gateway",
    "MENTIONS",
    "Model",
    "ModelReply",
    "Secrets",
    "TEXT",
    "URL",
    "asynccontextmanager",
    "asyncio",
    "connection",
    "func",
    "json",
    "mentions_value",
    "open_instance",
    "opened",
    "pytest",
    "s",
    "select",
    "target",
    "terminal",
    "update",
    "uuid",
]
