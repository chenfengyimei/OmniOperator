"""Popular/ranking video feeds: bounded data.list rows with nested stat/owner; fake HTTP only."""

from __future__ import annotations

import asyncio
import typing

import httpx
import pytest
from omni_test_support.v2.bilibili_gateway import CONFIG, Secrets
from omni_test_support.v2.bilibili_response_bounds import NAV

from omni.adapters.bilibili import BilibiliGateway
from omni.adapters.diagnostics import Redactor
from omni.domain.bilibili import FEED_MAX_ROWS, feed_rows
from omni.domain.contracts import BusinessError

FEED = {
    "list": [
        {
            "bvid": "BV1xx411c7mD",
            "title": "<em>热门</em>视频",
            "duration": 120,
            "stat": {"view": 1000, "danmaku": 50},
            "owner": {"name": "UP主"},
        },
        {
            "bvid": "BV1yy411c7mE",
            "title": "第二",
            "duration": 60,
            "stat": {"view": 2000, "danmaku": 60},
            "owner": {"name": "作者2"},
        },
    ]
}


def fixture(*, feed=None):
    state = {"feed": FEED if feed is None else feed}
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.path.endswith("/nav"):
            data = NAV
        elif request.url.path in {"/x/web-interface/popular", "/x/web-interface/ranking/v2"}:
            data = state["feed"]
        else:
            raise AssertionError(f"Unexpected endpoint: {request.url.path}")
        return httpx.Response(200, json={"code": 0, "data": data}, headers={"content-type": "application/json"})

    async def no_delivery(_):
        raise AssertionError("No writes")

    gateway = BilibiliGateway(
        Secrets(),
        Redactor(),
        no_delivery,
        client_factory=lambda **options: httpx.AsyncClient(transport=httpx.MockTransport(handle), **options),
    )
    return gateway, state, calls


def test_feed_rows_parses_nested_stat_owner():
    out = feed_rows(FEED, 10)
    assert out["has_more"] is False
    assert out["items"] == [
        {
            "bvid": "BV1xx411c7mD",
            "title": "热门视频",
            "author": "UP主",
            "play": 1000,
            "danmaku": 50,
            "duration_s": 120,
        },
        {"bvid": "BV1yy411c7mE", "title": "第二", "author": "作者2", "play": 2000, "danmaku": 60, "duration_s": 60},
    ]


def test_feed_rows_absent_list_is_empty():
    assert feed_rows({}, 10) == {"items": [], "has_more": False}


def test_feed_rows_limit_and_has_more():
    out = feed_rows(FEED, 1)
    assert len(out["items"]) == 1 and out["has_more"] is True


def test_feed_rows_missing_nested_fields_default_none():
    out = feed_rows({"list": [{"bvid": "BV1xx411c7mD"}]}, 10)
    assert out["items"] == [
        {"bvid": "BV1xx411c7mD", "title": "", "author": "", "play": None, "danmaku": None, "duration_s": None}
    ]


@pytest.mark.parametrize(
    "data",
    [
        {"list": "changed"},
        {"list": [None]},
        {"list": [{"title": "no bvid"}]},
        {"list": [{"bvid": "not-a-bv"}]},
        {"list": [{"bvid": "BV1xx411c7mD", "title": 42}]},
        {"list": [{"bvid": "BV1xx411c7mD"}] * (FEED_MAX_ROWS + 1)},
    ],
)
def test_feed_rows_malformed_fails_explicitly(data):
    with pytest.raises(BusinessError) as caught:
        feed_rows(data, 10)
    assert caught.value.code == "bilibili_response_invalid" and caught.value.status == 502


@pytest.mark.parametrize(
    "operation,path,feed",
    [("popular", "/x/web-interface/popular", "popular"), ("ranking", "/x/web-interface/ranking/v2", "ranking")],
)
def test_feed_gateway_reads_list(operation, path, feed):
    async def scenario():
        gateway, _, calls = fixture()
        try:
            out = await gateway.read(CONFIG, operation, "", 5)
            assert out["feed"] == feed
            assert len(out["items"]) == 2 and out["items"][0]["title"] == "热门视频"
            assert [c.url.path for c in calls] == ["/x/web-interface/nav", path]
            query = calls[-1].url.params
            if operation == "popular":
                assert query["pn"] == "1" and query["ps"] == "20"
            else:
                assert query["rid"] == "0" and query["type"] == "all"
            assert not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_feed_gateway_respects_limit():
    async def scenario():
        gateway, _, _ = fixture()
        try:
            out = await gateway.read(CONFIG, "popular", "", 1)
            assert len(out["items"]) == 1 and out["has_more"] is True
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_feed_gateway_empty_list_is_normal():
    async def scenario():
        gateway, _, _ = fixture(feed={})
        try:
            out = await gateway.read(CONFIG, "popular", "", 5)
            assert out["items"] == [] and out["has_more"] is False
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_feed_gateway_malformed_response_fails():
    async def scenario():
        gateway, _, _ = fixture(feed={"list": "changed"})
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.read(CONFIG, "ranking", "", 5)
            assert caught.value.code == "bilibili_response_invalid" and caught.value.status == 502
            assert not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_feed_exposed_in_tool_spec_and_http_input():
    from omni.adapters.http import BilibiliReadInput
    from omni.application.bilibili import READ_SPEC

    enum = READ_SPEC.parameters["properties"]["operation"]["enum"]
    assert "popular" in enum and "ranking" in enum
    args = typing.get_args(BilibiliReadInput.model_fields["operation"].annotation)
    assert "popular" in args and "ranking" in args
