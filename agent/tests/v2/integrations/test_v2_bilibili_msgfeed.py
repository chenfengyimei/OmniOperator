"""Owner reply/at inbox: bounded data.items discovery rows; fake HTTP only, target kind required, login required."""

from __future__ import annotations

import asyncio
import typing

import httpx
import pytest
from omni_test_support.v2.bilibili_gateway import CONFIG, Secrets
from omni_test_support.v2.bilibili_response_bounds import NAV

from omni.adapters.bilibili import BilibiliGateway
from omni.adapters.diagnostics import Redactor
from omni.domain.bilibili import FEED_MAX_ROWS, MSGFEED_LIKE_MAX_USERS, msgfeed_kind, msgfeed_like_rows, msgfeed_rows
from omni.domain.contracts import BusinessError

MSGFEED = {
    "items": [
        {
            "id": 9001,
            "reply_time": 1700000000,
            "user": {"nickname": "回复者", "mid": 555},
            "item": {
                "title": "<em>回复</em>提示",
                "source_content": "正文片段",
                "business_id": 1,
                "subject_id": 111,
                "root_id": 222,
                "source_id": 333,
                "uri": "https://b23.tv/x",
            },
        },
        {
            "id": 9002,
            "at_time": 1700000100,
            "user": {"nickname": "AT者", "mid": 556},
            "item": {"business_id": 17, "subject_id": 112, "uri": "https://b23.tv/y"},
        },
    ]
}

LIKE_FEED = {
    "latest": {
        "items": [
            {
                "id": 7001,
                "like_time": 1700000200,
                "counts": 3,
                "users": [{"nickname": "点赞者甲", "mid": 601}, {"nickname": "点赞者乙", "mid": 602}],
                "item": {
                    "title": "<em>视频</em>标题",
                    "source_content": "评论片段",
                    "business_id": 1,
                    "subject_id": 111,
                    "root_id": 222,
                    "source_id": 333,
                    "uri": "https://b23.tv/z",
                },
            }
        ]
    },
    "total": {
        "cursor": 123,
        "items": [
            {"id": 7001, "like_time": 1700000200, "counts": 3, "users": [{"nickname": "点赞者甲", "mid": 601}]},
            {
                "id": 7002,
                "like_time": 1700000300,
                "counts": 1,
                "users": [{"nickname": "点赞者丙", "mid": 603}],
                "item": {"business_id": 17, "subject_id": 112, "uri": "https://b23.tv/w"},
            },
        ],
    },
}


def fixture(*, feed=None):
    state = {"feed": MSGFEED if feed is None else feed}
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.path.endswith("/nav"):
            data = NAV
        elif request.url.path.startswith("/x/msgfeed/"):
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


def test_msgfeed_kind_accepts_reply_at_like():
    assert msgfeed_kind("reply") == "reply" and msgfeed_kind("at") == "at" and msgfeed_kind("like") == "like"


@pytest.mark.parametrize("value", ["", "video", "REPLY", None, 5, []])
def test_msgfeed_kind_rejects_other_targets(value):
    with pytest.raises(BusinessError) as caught:
        msgfeed_kind(value)
    assert caught.value.code == "bilibili_read_invalid" and caught.value.status == 422


def test_msgfeed_rows_parses_nested_and_time_fallback():
    out = msgfeed_rows(MSGFEED, 10)
    assert out["has_more"] is False
    assert out["items"][0] == {
        "id": 9001,
        "time": 1700000000,
        "nickname": "回复者",
        "mid": "555",
        "business_id": 1,
        "subject_id": "111",
        "root_id": "222",
        "source_id": "333",
        "uri": "https://b23.tv/x",
        "title": "回复提示",
        "source_content": "正文片段",
    }
    second = out["items"][1]
    assert second["time"] == 1700000100 and second["business_id"] == 17
    assert second["title"] == "" and second["source_content"] == ""
    assert second["root_id"] == "" and second["source_id"] == "" and second["subject_id"] == "112"


def test_msgfeed_rows_absent_items_is_empty():
    assert msgfeed_rows({}, 10) == {"items": [], "has_more": False}


def test_msgfeed_rows_limit_and_has_more():
    out = msgfeed_rows(MSGFEED, 1)
    assert len(out["items"]) == 1 and out["has_more"] is True


def test_msgfeed_rows_missing_nested_fields_default():
    out = msgfeed_rows({"items": [{"id": 1}]}, 10)
    assert out["items"] == [
        {
            "id": 1,
            "time": None,
            "nickname": "",
            "mid": "",
            "business_id": None,
            "subject_id": "",
            "root_id": "",
            "source_id": "",
            "uri": "",
            "title": "",
            "source_content": "",
        }
    ]


@pytest.mark.parametrize(
    "data",
    [
        {"items": "changed"},
        {"items": [None]},
        {"items": [{"id": "not-int"}]},
        {"items": [{"id": 1, "user": {"nickname": 42}}]},
        {"items": [MSGFEED["items"][0]] * (FEED_MAX_ROWS + 1)},
    ],
)
def test_msgfeed_rows_malformed_fails_explicitly(data):
    with pytest.raises(BusinessError) as caught:
        msgfeed_rows(data, 10)
    assert caught.value.code == "bilibili_response_invalid" and caught.value.status == 502


def test_msgfeed_gateway_reply_inbox():
    async def scenario():
        gateway, _, calls = fixture()
        try:
            out = await gateway.read(CONFIG, "msgfeed", "reply", 5)
            assert out["feed"] == "msgfeed" and out["kind"] == "reply"
            assert len(out["items"]) == 2 and out["items"][0]["nickname"] == "回复者"
            assert [c.url.path for c in calls] == ["/x/web-interface/nav", "/x/msgfeed/reply"]
            query = calls[-1].url.params
            assert query["platform"] == "web" and query["build"] == "0" and query["mobi_app"] == "web"
            assert not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_msgfeed_gateway_at_inbox():
    async def scenario():
        gateway, _, calls = fixture()
        try:
            out = await gateway.read(CONFIG, "msgfeed", "at", 5)
            assert out["kind"] == "at"
            assert calls[-1].url.path == "/x/msgfeed/at"
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_msgfeed_gateway_invalid_kind_rejected_before_network():
    async def scenario():
        gateway, _, calls = fixture()
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.read(CONFIG, "msgfeed", "video", 5)
            assert caught.value.code == "bilibili_read_invalid" and caught.value.status == 422
            assert calls == [] and not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_msgfeed_gateway_empty_items_is_normal():
    async def scenario():
        gateway, _, _ = fixture(feed={})
        try:
            out = await gateway.read(CONFIG, "msgfeed", "reply", 5)
            assert out["items"] == [] and out["has_more"] is False
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_msgfeed_gateway_malformed_response_fails():
    async def scenario():
        gateway, _, _ = fixture(feed={"items": "changed"})
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.read(CONFIG, "msgfeed", "reply", 5)
            assert caught.value.code == "bilibili_response_invalid" and caught.value.status == 502
            assert not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_msgfeed_exposed_in_tool_spec_and_http_input():
    from omni.adapters.http import BilibiliReadInput
    from omni.application.bilibili import READ_SPEC

    enum = READ_SPEC.parameters["properties"]["operation"]["enum"]
    assert "msgfeed" in enum
    args = typing.get_args(BilibiliReadInput.model_fields["operation"].annotation)
    assert "msgfeed" in args


def test_msgfeed_like_rows_parses_latest_total_dedup():
    out = msgfeed_like_rows(LIKE_FEED, 10)
    assert out["has_more"] is False and len(out["items"]) == 2
    assert out["items"][0] == {
        "id": 7001,
        "time": 1700000200,
        "counts": 3,
        "users": [{"mid": "601", "nickname": "点赞者甲"}, {"mid": "602", "nickname": "点赞者乙"}],
        "business_id": 1,
        "subject_id": "111",
        "root_id": "222",
        "source_id": "333",
        "uri": "https://b23.tv/z",
        "title": "视频标题",
        "source_content": "评论片段",
    }
    second = out["items"][1]
    assert second["id"] == 7002 and second["time"] == 1700000300 and second["counts"] == 1
    assert second["users"] == [{"mid": "603", "nickname": "点赞者丙"}]
    assert second["title"] == "" and second["business_id"] == 17 and second["subject_id"] == "112"


def test_msgfeed_like_rows_absent_is_empty():
    assert msgfeed_like_rows({}, 10) == {"items": [], "has_more": False}


def test_msgfeed_like_rows_flat_items_fallback():
    out = msgfeed_like_rows(
        {"items": [{"id": 1, "like_time": 5, "counts": 2, "users": [{"mid": 9, "nickname": "n"}]}]}, 10
    )
    assert out["items"][0]["id"] == 1 and out["items"][0]["time"] == 5 and out["items"][0]["counts"] == 2
    assert out["items"][0]["users"] == [{"mid": "9", "nickname": "n"}]


def test_msgfeed_like_rows_users_truncated():
    users = [{"mid": i, "nickname": f"u{i}"} for i in range(MSGFEED_LIKE_MAX_USERS + 5)]
    out = msgfeed_like_rows({"items": [{"id": 1, "users": users}]}, 10)
    assert len(out["items"][0]["users"]) == MSGFEED_LIKE_MAX_USERS


def test_msgfeed_like_rows_missing_fields_default():
    assert msgfeed_like_rows({"items": [{"id": 1}]}, 10)["items"] == [
        {
            "id": 1,
            "time": None,
            "counts": None,
            "users": [],
            "business_id": None,
            "subject_id": "",
            "root_id": "",
            "source_id": "",
            "uri": "",
            "title": "",
            "source_content": "",
        }
    ]


def test_msgfeed_like_rows_limit_and_has_more():
    out = msgfeed_like_rows(LIKE_FEED, 1)
    assert len(out["items"]) == 1 and out["has_more"] is True


@pytest.mark.parametrize(
    "data",
    [
        {"latest": {"items": "changed"}},
        {"total": {"items": [None]}},
        {"items": [{"id": "not-int"}]},
        {"items": [{"id": 1, "users": "changed"}]},
        {"items": [{"id": 1, "users": [None]}]},
        {"items": [{"id": 1, "users": [{"nickname": 42}]}]},
        {"latest": {"items": [{"id": 1}] * (FEED_MAX_ROWS + 1)}},
    ],
)
def test_msgfeed_like_rows_malformed_fails_explicitly(data):
    with pytest.raises(BusinessError) as caught:
        msgfeed_like_rows(data, 10)
    assert caught.value.code == "bilibili_response_invalid" and caught.value.status == 502


def test_msgfeed_gateway_like_inbox():
    async def scenario():
        gateway, _, calls = fixture(feed=LIKE_FEED)
        try:
            out = await gateway.read(CONFIG, "msgfeed", "like", 5)
            assert out["feed"] == "msgfeed" and out["kind"] == "like"
            assert len(out["items"]) == 2 and out["items"][0]["counts"] == 3
            assert out["items"][0]["users"][0]["nickname"] == "点赞者甲"
            assert [c.url.path for c in calls] == ["/x/web-interface/nav", "/x/msgfeed/like"]
            query = calls[-1].url.params
            assert query["platform"] == "web" and query["build"] == "0" and query["mobi_app"] == "web"
            assert not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_msgfeed_gateway_like_malformed_fails():
    async def scenario():
        gateway, _, _ = fixture(feed={"latest": {"items": "changed"}})
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.read(CONFIG, "msgfeed", "like", 5)
            assert caught.value.code == "bilibili_response_invalid" and caught.value.status == 502
            assert not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())
