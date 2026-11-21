"""Shared media source-identity gateway fixture, extracted so tests do not import test modules."""

from __future__ import annotations

import httpx

from omni.adapters.bilibili import BilibiliGateway
from omni.adapters.diagnostics import Redactor
from omni_test_support.v2.bilibili_gateway import Secrets

BV = "BV1xx411c7mD"
SOURCE = {"bvid": BV, "aid": "123", "cid": "789"}
URL = "https://cdn.bilivideo.com/owned.m4s"


class Gateway(BilibiliGateway):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.payloads = []

    async def _subtitle(self, client, nav, video):
        self.payloads.append(("subtitle", dict(video)))
        return {"language": "zh", "text": "captured source subtitle"}

    async def _streams(self, client, video):
        self.payloads.append(("streams", dict(video)))
        return {"audio": {"url": URL}, "video": {"url": URL}}


def fixture():
    state = {"bvid": BV, "aid": 123, "cid": 789, "title": "Fixture", "duration": 120}
    calls, gate = [], {"events": None}

    async def handle(request):
        calls.append(request)
        if request.url.path.endswith("/nav"):
            data = {"isLogin": True, "mid": 456, "uname": "fixture"}
        elif request.url.path.endswith("/view"):
            if gate["events"] is not None:
                entered, release = gate["events"]
                entered.set()
                await release.wait()
            data = dict(state)
        elif request.url.host == "cdn.bilivideo.com":
            return httpx.Response(200, content=b"owned media bytes")
        else:
            raise AssertionError("Unexpected endpoint")
        return httpx.Response(200, json={"code": 0, "data": data})

    async def no_write(_):
        raise AssertionError("No publications")

    gateway = Gateway(
        Secrets(),
        Redactor(),
        no_write,
        client_factory=lambda **options: httpx.AsyncClient(transport=httpx.MockTransport(handle), **options),
    )
    return gateway, state, calls, gate
