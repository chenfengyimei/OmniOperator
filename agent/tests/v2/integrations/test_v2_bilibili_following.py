"""Following video-dynamic feed: bounded data.items rows with nested modules + duration_text; fake HTTP only."""

from __future__ import annotations

import asyncio
import typing

import httpx
import pytest
from omni_test_support.v2.bilibili_gateway import CONFIG, Secrets
from omni_test_support.v2.bilibili_response_bounds import NAV

from omni.adapters.bilibili import BilibiliGateway
from omni.adapters.diagnostics import Redactor
from omni.domain.bilibili import FEED_MAX_ROWS, duration_seconds, following_rows
from omni.domain.contracts import BusinessError

FOLLOWING = {
    "items": [
        {
            "modules": {
                "module_author": {"name": "关注UP"},
                "module_dynamic": {
                    "major": {
                        "archive": {
                            "bvid": "BV1xx411c7mD",
                            "title": "<em>关注</em>视频",
                            "duration_text": "05:23",
                            "stat": {"play": 1000},
                        }
                    }
                },
            }
        },
        {
            "modules": {
                "module_author": {"name": "作者2"},
                "module_dynamic": {
                    "major": {
                        "archive": {
                            "bvid": "BV1yy411c7mE",
                            "title": "第二",
                            "duration_text": "1:02:03",
                            "stat": {"play": 2000},
                        }
                    }
                },
            }
        },
    ]
}


def fixture(*, feed=None):
    state = {"feed": FOLLOWING if feed is None else feed}
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.path.endswith("/nav"):
            data = NAV
        elif request.url.path == "/x/polymer/web-dynamic/v1/feed/all":
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


def test_following_rows_parses_nested_modules_and_duration():
    out = following_rows(FOLLOWING, 10)
    assert out["has_more"] is False
    assert out["items"] == [
        {"bvid": "BV1xx411c7mD", "title": "关注视频", "author": "关注UP", "play": 1000, "duration_s": 323},
        {"bvid": "BV1yy411c7mE", "title": "第二", "author": "作者2", "play": 2000, "duration_s": 3723},
    ]


def test_following_rows_absent_items_is_empty():
    assert following_rows({}, 10) == {"items": [], "has_more": False}


def test_following_rows_limit_and_has_more():
    out = following_rows(FOLLOWING, 1)
    assert len(out["items"]) == 1 and out["has_more"] is True


def test_following_rows_skips_non_video_dynamics():
    data = {
        "items": [
            {"modules": {"module_dynamic": {"major": {}}}},
            {"modules": {}},
            {},
            FOLLOWING["items"][0],
        ]
    }
    out = following_rows(data, 10)
    assert len(out["items"]) == 1 and out["items"][0]["bvid"] == "BV1xx411c7mD"


def test_following_rows_missing_nested_fields_default_none():
    data = {"items": [{"modules": {"module_dynamic": {"major": {"archive": {"bvid": "BV1xx411c7mD"}}}}}]}
    out = following_rows(data, 10)
    assert out["items"] == [{"bvid": "BV1xx411c7mD", "title": "", "author": "", "play": None, "duration_s": None}]


@pytest.mark.parametrize(
    "value,expected",
    [
        ("05:23", 323),
        ("1:02:03", 3723),
        ("00:00", 0),
        ("59:59", 3599),
        ("", None),
        ("abc", None),
        ("1:2:3:4", None),
        ("1:xx", None),
        (None, None),
        (42, None),
    ],
)
def test_duration_seconds_parses_mm_ss_and_hh_mm_ss(value, expected):
    assert duration_seconds(value) == expected


@pytest.mark.parametrize(
    "data",
    [
        {"items": "changed"},
        {"items": [None]},
        {"items": [{"modules": {"module_dynamic": {"major": {"archive": {"bvid": "not-a-bv"}}}}}]},
        {"items": [{"modules": {"module_dynamic": {"major": {"archive": {"bvid": "BV1xx411c7mD", "title": 42}}}}}]},
        {"items": [FOLLOWING["items"][0]] * (FEED_MAX_ROWS + 1)},
    ],
)
def test_following_rows_malformed_fails_explicitly(data):
    with pytest.raises(BusinessError) as caught:
        following_rows(data, 10)
    assert caught.value.code == "bilibili_response_invalid" and caught.value.status == 502


def test_following_gateway_reads_items():
    async def scenario():
        gateway, _, calls = fixture()
        try:
            out = await gateway.read(CONFIG, "following", "", 5)
            assert out["feed"] == "following"
            assert len(out["items"]) == 2 and out["items"][0]["title"] == "关注视频"
            assert [c.url.path for c in calls] == ["/x/web-interface/nav", "/x/polymer/web-dynamic/v1/feed/all"]
            query = calls[-1].url.params
            assert query["type"] == "video" and query["page"] == "1"
            assert not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_following_gateway_respects_limit():
    async def scenario():
        gateway, _, _ = fixture()
        try:
            out = await gateway.read(CONFIG, "following", "", 1)
            assert len(out["items"]) == 1 and out["has_more"] is True
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_following_gateway_empty_items_is_normal():
    async def scenario():
        gateway, _, _ = fixture(feed={})
        try:
            out = await gateway.read(CONFIG, "following", "", 5)
            assert out["items"] == [] and out["has_more"] is False
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_following_gateway_malformed_response_fails():
    async def scenario():
        gateway, _, _ = fixture(feed={"items": "changed"})
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.read(CONFIG, "following", "", 5)
            assert caught.value.code == "bilibili_response_invalid" and caught.value.status == 502
            assert not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_following_exposed_in_tool_spec_and_http_input():
    from omni.adapters.http import BilibiliReadInput
    from omni.application.bilibili import READ_SPEC

    enum = READ_SPEC.parameters["properties"]["operation"]["enum"]
    assert "following" in enum
    args = typing.get_args(BilibiliReadInput.model_fields["operation"].annotation)
    assert "following" in args
