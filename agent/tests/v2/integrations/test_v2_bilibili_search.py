"""Public video search: bounded keyword, WBI-signed read, markup-stripped rows; fake HTTP only."""

from __future__ import annotations

import asyncio
import typing

import httpx
import pytest
from omni_test_support.v2.bilibili_gateway import CONFIG, Secrets
from omni_test_support.v2.bilibili_response_bounds import NAV

from omni.adapters.bilibili import BilibiliGateway
from omni.adapters.diagnostics import Redactor
from omni.domain.bilibili import SEARCH_MAX_RESULTS, search_keyword, search_rows, search_text
from omni.domain.contracts import BusinessError

SEARCH = {
    "result": [
        {
            "bvid": "BV1xx411c7mD",
            "title": '<em class="keyword">你好</em>世界',
            "author": "UP主",
            "play": 1000,
            "video_review": 50,
            "duration": "12:34",
            "pubdate": 1700000000,
            "description": "简介<em>关键词</em>文本",
        },
        {
            "bvid": "BV1yy411c7mE",
            "title": "第二视频",
            "author": "作者2",
            "play": 2000,
            "video_review": 60,
            "duration": "1:02:03",
            "pubdate": 1700000001,
            "description": "desc2",
        },
    ]
}


def fixture(*, search=None):
    state = {"search": SEARCH if search is None else search}
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.path.endswith("/nav"):
            data = NAV
        elif request.url.path == "/x/web-interface/wbi/search/type":
            data = state["search"]
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


def test_search_keyword_strips_and_bounds():
    assert search_keyword("  你好  ") == "你好"
    assert search_keyword("a" * 100) == "a" * 100


@pytest.mark.parametrize("value", [None, 42, "", "   ", "a" * 101, "bad\x00kw", "line\nbreak"])
def test_search_keyword_rejects_invalid(value):
    with pytest.raises(BusinessError) as caught:
        search_keyword(value)
    assert caught.value.code == "bilibili_read_invalid" and caught.value.status == 422


def test_search_text_strips_markup_and_bounds():
    assert search_text('<em class="keyword">你好</em>世界', 300) == "你好世界"
    assert search_text(None, 300) == ""
    assert search_text("字" * 400, 200) == "字" * 200


@pytest.mark.parametrize("value", [42, {"a": 1}, ["x"], "\ud800"])
def test_search_text_rejects_non_string_or_bad_unicode(value):
    with pytest.raises(BusinessError) as caught:
        search_text(value, 300)
    assert caught.value.code == "bilibili_response_invalid" and caught.value.status == 502


def test_search_rows_parses_bounded_fields():
    out = search_rows(SEARCH, 10)
    assert out["has_more"] is False
    assert out["items"] == [
        {
            "bvid": "BV1xx411c7mD",
            "title": "你好世界",
            "author": "UP主",
            "play": 1000,
            "danmaku": 50,
            "duration": "12:34",
            "pubdate": 1700000000,
            "description": "简介关键词文本",
        },
        {
            "bvid": "BV1yy411c7mE",
            "title": "第二视频",
            "author": "作者2",
            "play": 2000,
            "danmaku": 60,
            "duration": "1:02:03",
            "pubdate": 1700000001,
            "description": "desc2",
        },
    ]


def test_search_rows_absent_result_is_empty():
    assert search_rows({}, 10) == {"items": [], "has_more": False}
    assert search_rows({"numResults": 0}, 10) == {"items": [], "has_more": False}
    assert search_rows({"results": []}, 10) == {"items": [], "has_more": False}


def test_search_rows_limit_and_has_more():
    out = search_rows(SEARCH, 1)
    assert len(out["items"]) == 1 and out["has_more"] is True


@pytest.mark.parametrize(
    "data",
    [
        {"result": "changed"},
        {"result": [None]},
        {"result": [{"title": "no bvid"}]},
        {"result": [{"bvid": "not-a-bv"}]},
        {"result": [{"bvid": "BV1xx411c7mD", "title": 42}]},
        {"result": [{"bvid": "BV1xx411c7mD"}] * (SEARCH_MAX_RESULTS + 1)},
    ],
)
def test_search_rows_malformed_fails_explicitly(data):
    with pytest.raises(BusinessError) as caught:
        search_rows(data, 10)
    assert caught.value.code == "bilibili_response_invalid" and caught.value.status == 502


def test_search_gateway_signs_wbi_and_parses_rows():
    async def scenario():
        gateway, _, calls = fixture()
        try:
            out = await gateway.read(CONFIG, "search", "你好", 5)
            assert out["keyword"] == "你好"
            assert len(out["items"]) == 2 and out["items"][0]["title"] == "你好世界"
            assert [c.url.path for c in calls] == ["/x/web-interface/nav", "/x/web-interface/wbi/search/type"]
            query = calls[-1].url.params
            assert query["search_type"] == "video" and query["keyword"] == "你好" and query["page"] == "1"
            assert query["w_rid"] and query["wts"]
            assert not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_search_empty_result_is_normal():
    async def scenario():
        gateway, _, calls = fixture(search={})
        try:
            assert await gateway.read(CONFIG, "search", "无结果关键词", 5) == {
                "keyword": "无结果关键词",
                "items": [],
                "has_more": False,
            }
            assert len(calls) == 2
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("keyword", ["", "   ", "a" * 101, "bad\x00kw"])
def test_search_invalid_keyword_rejected_before_network(keyword):
    async def scenario():
        gateway, _, calls = fixture()
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.read(CONFIG, "search", keyword, 5)
            assert caught.value.code == "bilibili_read_invalid" and caught.value.status == 422
            assert not calls and not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_search_malformed_response_fails():
    async def scenario():
        gateway, _, _ = fixture(search={"result": "changed"})
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.read(CONFIG, "search", "你好", 5)
            assert caught.value.code == "bilibili_response_invalid" and caught.value.status == 502
            assert not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_search_respects_limit():
    async def scenario():
        gateway, _, _ = fixture()
        try:
            out = await gateway.read(CONFIG, "search", "你好", 1)
            assert len(out["items"]) == 1 and out["has_more"] is True
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_search_exposed_in_tool_spec_and_http_input():
    from omni.adapters.http import BilibiliReadInput
    from omni.application.bilibili import READ_SPEC

    assert "search" in READ_SPEC.parameters["properties"]["operation"]["enum"]
    assert "search" in typing.get_args(BilibiliReadInput.model_fields["operation"].annotation)
