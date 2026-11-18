"""Reusable bilibili_gateway fixtures, extracted without collecting test cases."""

import asyncio
import json

import httpx
import pytest

from omni.adapters.bilibili import BilibiliGateway
from omni.adapters.diagnostics import Redactor
from omni.domain.contracts import ActionResult, BusinessError, Effect, Outcome
from omni.domain.delivery import OutboundMessage, text_fingerprint
from omni_test_support.v2.bilibili import BV, FORM, MENTIONS, TEXT, URL

CONFIG = {"driver": "bilibili", "account": "456", "endpoint": "", "credential_ref": "fixture/cookies"}


class Secrets:
    async def get(self, reference):
        return json.dumps({"SESSDATA": "cookie-canary-value", "bili_jct": "csrf-canary-value", "DedeUserID": "456"})


def factory(*, account=456, malformed=False, malformed_comments=False):
    calls = []

    def handle(request):
        calls.append(request)
        if malformed:
            return httpx.Response(200, text="not-json", headers={"content-type": "application/json"})
        if request.url.path.endswith("/nav"):
            data = {"isLogin": True, "mid": account, "uname": "fixture"}
        elif request.url.path.endswith("/view"):
            data = {
                "bvid": BV,
                "aid": 123,
                "cid": 789,
                "title": "fixture",
                "owner": {"name": "author"},
                "desc": "description",
                "duration": 4,
            }
        else:
            data = {
                "replies": [{"rpid": 99, "mid": 222, "member": {"uname": "Alice"}, "content": {"message": "comment"}}]
            }
        if malformed_comments and request.url.path == "/x/v2/reply":
            data = {"replies": ["invalid record"]}
        return httpx.Response(200, json={"code": 0, "data": data})

    return lambda **options: httpx.AsyncClient(transport=httpx.MockTransport(handle), **options), calls


class Worker:
    def __init__(self, *, unknown=False):
        self.calls = []
        self.closed = False
        self.unknown = unknown

    async def invoke(self, name, args, *, before_dispatch, **kwargs):
        try:
            await before_dispatch()
        except BusinessError:
            return ActionResult(Outcome.DENIED, "Revoked before IPC", error_code="bilibili_dispatch_revoked")
        self.calls.append((name, args))
        if name != "bilibili_commit":
            return ActionResult(Outcome.OK, "fixture staged", data={"nonce": "fixture"})
        if self.unknown:
            return ActionResult(Outcome.UNKNOWN, "fixture disconnected", Effect.UNKNOWN)
        return ActionResult(
            Outcome.OK,
            "fixture verified",
            Effect.APPLIED,
            data={
                "record_id": "99",
                "account": "456",
                "target": URL,
                "body_hash": text_fingerprint(TEXT),
                "mentions": ["222"],
            },
        )

    async def close(self, timeout_s):
        self.closed = True
        return True


def message():
    return OutboundMessage("delivery", "attempt", "token", "a" * 64, "456", "bilibili:plan", TEXT, CONFIG)


async def authority(message):
    return {
        "kind": "comment",
        "target": URL,
        "target_key": BV,
        "form": FORM,
        "mentions": MENTIONS,
        "authority": "generation",
        "agent_id": "agent",
        "run_id": "run",
        "session_id": "session",
        "policy_revision": 1,
    }


__all__ = [
    "ActionResult",
    "BV",
    "BilibiliGateway",
    "BusinessError",
    "CONFIG",
    "Effect",
    "FORM",
    "MENTIONS",
    "OutboundMessage",
    "Outcome",
    "Redactor",
    "Secrets",
    "TEXT",
    "URL",
    "Worker",
    "asyncio",
    "authority",
    "factory",
    "httpx",
    "json",
    "message",
    "pytest",
    "text_fingerprint",
]
