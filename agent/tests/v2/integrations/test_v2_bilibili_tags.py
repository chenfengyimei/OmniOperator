"""Public video tags: bounded data-array rows via the list-shaped reader; fake HTTP only, target BV required."""

from __future__ import annotations

import asyncio
import typing

import httpx
import pytest
from omni_test_support.v2.bilibili_gateway import CONFIG, Secrets
from omni_test_support.v2.bilibili_response_bounds import NAV

from omni.adapters.bilibili import BilibiliGateway
from omni.adapters.diagnostics import Redactor
from omni.domain.bilibili import FEED_MAX_ROWS, tag_rows
from omni.domain.contracts import BusinessError

BV = "BV1xx411c7mD"
TAGS = [
    {"tag_id": 101, "tag_name": "<em>学习</em>"},
    {"tag_id": 102, "tag_name": "教程"},
]


def fixture(*, feed=None):
    state = {"feed": TAGS if feed is None else feed}
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.path.endswith("/nav"):
            data = NAV
        elif request.url.path == "/x/tag/archive/tags":
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


def test_tag_rows_parses_and_strips_name():
    out = tag_rows(TAGS, 10)
    assert out["has_more"] is False
    assert out["items"] == [{"tag_id": 101, "tag_name": "学习"}, {"tag_id": 102, "tag_name": "教程"}]


def test_tag_rows_absent_or_empty_is_normal():
    assert tag_rows(None, 10) == {"items": [], "has_more": False}
    assert tag_rows([], 10) == {"items": [], "has_more": False}


def test_tag_rows_limit_and_has_more():
    out = tag_rows(TAGS, 1)
    assert len(out["items"]) == 1 and out["has_more"] is True


def test_tag_rows_has_more_false_when_all_fit():
    assert tag_rows(TAGS, 2)["has_more"] is False
    assert tag_rows(TAGS, 5)["has_more"] is False


@pytest.mark.parametrize(
    "data",
    [
        "changed",
        [None],
        [{"tag_id": "not-int", "tag_name": "x"}],
        [{"tag_name": "no id"}],
        [{"tag_id": 101, "tag_name": 42}],
        [TAGS[0]] * (FEED_MAX_ROWS + 1),
    ],
)
def test_tag_rows_malformed_fails_explicitly(data):
    with pytest.raises(BusinessError) as caught:
        tag_rows(data, 10)
    assert caught.value.code == "bilibili_response_invalid" and caught.value.status == 502


def test_tags_gateway_reads_array():
    async def scenario():
        gateway, _, calls = fixture()
        try:
            out = await gateway.read(CONFIG, "tags", BV, 5)
            assert out["bvid"] == BV
            assert len(out["items"]) == 2 and out["items"][0]["tag_name"] == "学习"
            assert [c.url.path for c in calls] == ["/x/web-interface/nav", "/x/tag/archive/tags"]
            assert calls[-1].url.params["bvid"] == BV
            assert not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_tags_gateway_respects_limit():
    async def scenario():
        gateway, _, _ = fixture()
        try:
            out = await gateway.read(CONFIG, "tags", BV, 1)
            assert len(out["items"]) == 1 and out["has_more"] is True
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_tags_gateway_empty_array_is_normal():
    async def scenario():
        gateway, _, _ = fixture(feed=[])
        try:
            out = await gateway.read(CONFIG, "tags", BV, 5)
            assert out["items"] == [] and out["has_more"] is False
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_tags_gateway_malformed_rows_fail():
    async def scenario():
        gateway, _, _ = fixture(feed=[{"tag_name": "no id"}])
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.read(CONFIG, "tags", BV, 5)
            assert caught.value.code == "bilibili_response_invalid" and caught.value.status == 502
            assert not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_tags_gateway_dict_data_rejected_by_list_reader():
    async def scenario():
        gateway, _, _ = fixture(feed={"list": []})
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.read(CONFIG, "tags", BV, 5)
            assert caught.value.code == "bilibili_read_rejected" and caught.value.status == 502
            assert not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_tags_gateway_requires_bv_target():
    async def scenario():
        gateway, _, calls = fixture()
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.read(CONFIG, "tags", "not-a-bv", 5)
            assert caught.value.code == "bilibili_read_invalid" and caught.value.status == 422
            assert calls == [] and not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_tags_exposed_in_tool_spec_and_http_input():
    from omni.adapters.http import BilibiliReadInput
    from omni.application.bilibili import READ_SPEC

    enum = READ_SPEC.parameters["properties"]["operation"]["enum"]
    assert "tags" in enum
    args = typing.get_args(BilibiliReadInput.model_fields["operation"].annotation)
    assert "tags" in args
