"""Owner favorite folders: bounded data.list rows from list-all; fake HTTP only, login required, no writes."""

from __future__ import annotations

import asyncio
import typing

import httpx
import pytest
from omni_test_support.v2.bilibili_gateway import CONFIG, Secrets
from omni_test_support.v2.bilibili_response_bounds import NAV

from omni.adapters.bilibili import BilibiliGateway
from omni.adapters.diagnostics import Redactor
from omni.domain.bilibili import FEED_MAX_ROWS, fav_folder_rows
from omni.domain.contracts import BusinessError

FAVORITES = {
    "list": [
        {"id": 1001, "title": "<em>默认</em>收藏夹", "media_count": 12},
        {"id": 1002, "title": "学习", "media_count": 0},
    ]
}


def fixture(*, feed=None):
    state = {"feed": FAVORITES if feed is None else feed}
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.path.endswith("/nav"):
            data = NAV
        elif request.url.path == "/x/v3/fav/folder/created/list-all":
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


def test_fav_folder_rows_parses_and_strips_title():
    out = fav_folder_rows(FAVORITES, 10)
    assert out["has_more"] is False
    assert out["items"] == [
        {"id": 1001, "title": "默认收藏夹", "media_count": 12},
        {"id": 1002, "title": "学习", "media_count": 0},
    ]


def test_fav_folder_rows_absent_list_is_empty():
    assert fav_folder_rows({}, 10) == {"items": [], "has_more": False}


def test_fav_folder_rows_limit_and_has_more():
    out = fav_folder_rows(FAVORITES, 1)
    assert len(out["items"]) == 1 and out["has_more"] is True


def test_fav_folder_rows_has_more_false_when_all_fit():
    assert fav_folder_rows(FAVORITES, 2)["has_more"] is False
    assert fav_folder_rows(FAVORITES, 5)["has_more"] is False


def test_fav_folder_rows_missing_media_count_defaults_none():
    out = fav_folder_rows({"list": [{"id": 1001, "title": "x"}]}, 10)
    assert out["items"] == [{"id": 1001, "title": "x", "media_count": None}]


@pytest.mark.parametrize(
    "data",
    [
        {"list": "changed"},
        {"list": [None]},
        {"list": [{"id": "not-int", "title": "x"}]},
        {"list": [{"title": "no id"}]},
        {"list": [{"id": 1001, "title": 42}]},
        {"list": [FAVORITES["list"][0]] * (FEED_MAX_ROWS + 1)},
    ],
)
def test_fav_folder_rows_malformed_fails_explicitly(data):
    with pytest.raises(BusinessError) as caught:
        fav_folder_rows(data, 10)
    assert caught.value.code == "bilibili_response_invalid" and caught.value.status == 502


def test_favorites_gateway_reads_folders():
    async def scenario():
        gateway, _, calls = fixture()
        try:
            out = await gateway.read(CONFIG, "favorites", "", 5)
            assert out["feed"] == "favorites"
            assert len(out["items"]) == 2 and out["items"][0]["title"] == "默认收藏夹"
            assert [c.url.path for c in calls] == ["/x/web-interface/nav", "/x/v3/fav/folder/created/list-all"]
            assert calls[-1].url.params["up_mid"] == "456"
            assert not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_favorites_gateway_respects_limit():
    async def scenario():
        gateway, _, _ = fixture()
        try:
            out = await gateway.read(CONFIG, "favorites", "", 1)
            assert len(out["items"]) == 1 and out["has_more"] is True
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_favorites_gateway_empty_list_is_normal():
    async def scenario():
        gateway, _, _ = fixture(feed={})
        try:
            out = await gateway.read(CONFIG, "favorites", "", 5)
            assert out["items"] == [] and out["has_more"] is False
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_favorites_gateway_malformed_response_fails():
    async def scenario():
        gateway, _, _ = fixture(feed={"list": "changed"})
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.read(CONFIG, "favorites", "", 5)
            assert caught.value.code == "bilibili_response_invalid" and caught.value.status == 502
            assert not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_favorites_exposed_in_tool_spec_and_http_input():
    from omni.adapters.http import BilibiliReadInput
    from omni.application.bilibili import READ_SPEC

    enum = READ_SPEC.parameters["properties"]["operation"]["enum"]
    assert "favorites" in enum
    args = typing.get_args(BilibiliReadInput.model_fields["operation"].annotation)
    assert "favorites" in args
