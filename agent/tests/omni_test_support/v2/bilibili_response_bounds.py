"""Reusable bilibili_response_bounds fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import builtins
import gzip
import json

import httpx
import pytest

from omni.adapters import bilibili
from omni.adapters.bilibili import BilibiliGateway
from omni.adapters.diagnostics import Redactor
from omni.domain.contracts import BusinessError
from omni_test_support.v2.bilibili_gateway import CONFIG, Secrets

BV = "BV1xx411c7mD"

SOURCE = {"bvid": BV, "aid": "123", "cid": "789"}

SUBTITLE = "https://aisubtitle.hdslb.com/fixture.json"

NAV = {
    "isLogin": True,
    "mid": 456,
    "wbi_img": {"img_url": "/" + "a" * 32 + ".png", "sub_url": "/" + "b" * 32 + ".png"},
}

LIMITS = {"api": 524288, "subtitle": 2 * 1024 * 1024}


class Body(httpx.AsyncByteStream):
    def __init__(self, parts, *, gate=None, failure=None):
        self.parts, self.gate, self.failure = parts, gate, failure
        self.reads, self.closed = 0, False

    async def __aiter__(self):
        if self.gate is not None:
            entered, release = self.gate
            entered.set()
            await release.wait()
        for part in self.parts:
            self.reads += 1
            yield part
        if self.failure is not None:
            raise self.failure

    async def aclose(self):
        self.closed = True


def fixture(body, *, status=200, headers=None):
    calls, responses = [], []
    state = {"body": body}

    def handle(request):
        calls.append(request)
        if request.url.path in {"/bounded", "/fixture.json"}:
            response = httpx.Response(
                status, headers={"content-type": "application/json", **(headers or {})}, stream=state["body"]
            )
            responses.append(response)
            return response
        if request.url.path.endswith("/nav"):
            data = NAV
        elif request.url.path.endswith("/view"):
            data = {**SOURCE, "title": "Fixture", "duration": 120}
        elif request.url.path == "/x/player/wbi/v2":
            assert request.url.params["cid"] == SOURCE["cid"]
            data = {"subtitle": {"subtitles": [{"lan": "zh-CN", "subtitle_url": SUBTITLE}]}}
        else:
            raise AssertionError(f"Unexpected endpoint: {request.url.path}")
        return httpx.Response(200, json={"code": 0, "data": data})

    async def no_delivery(_):
        raise AssertionError("No publication")

    gateway = BilibiliGateway(
        Secrets(),
        Redactor(),
        no_delivery,
        client_factory=lambda **options: httpx.AsyncClient(transport=httpx.MockTransport(handle), **options),
    )
    return gateway, state, calls, responses


async def read(gateway, stage):
    async with gateway._client({}) as client:
        if stage == "api":
            return await gateway._get(client, "/bounded")
        return await gateway._subtitle(client, NAV, SOURCE)


def payload(stage, size=None):
    value = {"code": 0, "data": {"fixture": True}} if stage == "api" else {"body": [{"content": "合法字幕"}]}
    data = json.dumps(value, ensure_ascii=False).encode()
    return data if size is None else data + b" " * (size - len(data))


__all__ = [
    "BV",
    "BilibiliGateway",
    "Body",
    "BusinessError",
    "CONFIG",
    "LIMITS",
    "NAV",
    "Redactor",
    "SOURCE",
    "SUBTITLE",
    "Secrets",
    "asyncio",
    "bilibili",
    "builtins",
    "fixture",
    "gzip",
    "httpx",
    "json",
    "payload",
    "pytest",
    "read",
]
