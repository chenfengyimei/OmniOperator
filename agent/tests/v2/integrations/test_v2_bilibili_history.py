"""Owner watch history: bounded data.list rows with nested history oid/cid/business; fake HTTP only, login required."""

from __future__ import annotations

import asyncio
import typing

import httpx
import pytest
from omni_test_support.v2.bilibili_gateway import CONFIG, Secrets
from omni_test_support.v2.bilibili_response_bounds import NAV

from omni.adapters.bilibili import BilibiliGateway
from omni.adapters.diagnostics import Redactor
from omni.domain.bilibili import FEED_MAX_ROWS, history_rows
from omni.domain.contracts import BusinessError

HISTORY = {
    "list": [
        {
            "title": "<em>看过的</em>视频",
            "author_name": "UP主",
            "author_mid": 789,
            "progress": 120,
            "history": {"oid": 111, "cid": 222, "business": "archive", "bvid": "BV1xx411c7mD"},
        },
        {
            "title": "看完的",
            "author_name": "UP2",
            "author_mid": 790,
            "progress": -1,
            "history": {"oid": 112, "cid": 223, "business": "archive"},
        },
    ]
}


def fixture(*, feed=None):
    state = {"feed": HISTORY if feed is None else feed}
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.path.endswith("/nav"):
            data = NAV
        elif request.url.path == "/x/web-interface/history/cursor":
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


def test_history_rows_parses_nested_history():
    out = history_rows(HISTORY, 10)
    assert out["has_more"] is False
    assert out["items"][0] == {
        "title": "看过的视频",
        "author_name": "UP主",
        "author_mid": "789",
        "progress": 120,
        "business": "archive",
        "aid": 111,
        "cid": 222,
        "bvid": "BV1xx411c7mD",
    }
    assert out["items"][1]["progress"] == -1 and out["items"][1]["bvid"] is None
    assert out["items"][1]["aid"] == 112 and out["items"][1]["cid"] == 223


def test_history_rows_absent_list_is_empty():
    assert history_rows({}, 10) == {"items": [], "has_more": False}


def test_history_rows_limit_and_has_more():
    out = history_rows(HISTORY, 1)
    assert len(out["items"]) == 1 and out["has_more"] is True


def test_history_rows_missing_nested_fields_default_none():
    out = history_rows({"list": [{"title": "x"}]}, 10)
    assert out["items"] == [
        {
            "title": "x",
            "author_name": "",
            "author_mid": "",
            "progress": None,
            "business": None,
            "aid": None,
            "cid": None,
            "bvid": None,
        }
    ]


def test_history_rows_invalid_bvid_is_none_not_raised():
    out = history_rows({"list": [{"title": "x", "history": {"bvid": "not-a-bv", "oid": 1}}]}, 10)
    assert out["items"][0]["bvid"] is None and out["items"][0]["aid"] == 1


@pytest.mark.parametrize(
    "data",
    [
        {"list": "changed"},
        {"list": [None]},
        {"list": [{"title": 42}]},
        {"list": [HISTORY["list"][0]] * (FEED_MAX_ROWS + 1)},
    ],
)
def test_history_rows_malformed_fails_explicitly(data):
    with pytest.raises(BusinessError) as caught:
        history_rows(data, 10)
    assert caught.value.code == "bilibili_response_invalid" and caught.value.status == 502


def test_history_gateway_reads_list():
    async def scenario():
        gateway, _, calls = fixture()
        try:
            out = await gateway.read(CONFIG, "history", "", 5)
            assert out["feed"] == "history"
            assert len(out["items"]) == 2 and out["items"][0]["title"] == "看过的视频"
            assert [c.url.path for c in calls] == ["/x/web-interface/nav", "/x/web-interface/history/cursor"]
            query = calls[-1].url.params
            assert query["type"] == "archive" and query["ps"] == "20"
            assert not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_history_gateway_respects_limit():
    async def scenario():
        gateway, _, _ = fixture()
        try:
            out = await gateway.read(CONFIG, "history", "", 1)
            assert len(out["items"]) == 1 and out["has_more"] is True
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_history_gateway_empty_list_is_normal():
    async def scenario():
        gateway, _, _ = fixture(feed={})
        try:
            out = await gateway.read(CONFIG, "history", "", 5)
            assert out["items"] == [] and out["has_more"] is False
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_history_gateway_malformed_response_fails():
    async def scenario():
        gateway, _, _ = fixture(feed={"list": "changed"})
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.read(CONFIG, "history", "", 5)
            assert caught.value.code == "bilibili_response_invalid" and caught.value.status == 502
            assert not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_history_exposed_in_tool_spec_and_http_input():
    from omni.adapters.http import BilibiliReadInput
    from omni.application.bilibili import READ_SPEC

    enum = READ_SPEC.parameters["properties"]["operation"]["enum"]
    assert "history" in enum
    args = typing.get_args(BilibiliReadInput.model_fields["operation"].annotation)
    assert "history" in args
