"""Favorite folder contents: bounded video rows from a folder id, fixed endpoint, login required."""

from __future__ import annotations

import asyncio
import typing

import httpx
import pytest
from omni_test_support.v2.bilibili_gateway import CONFIG, Secrets
from omni_test_support.v2.bilibili_response_bounds import NAV

from omni.adapters.bilibili import BilibiliGateway
from omni.adapters.diagnostics import Redactor
from omni.domain.bilibili import fav_folder_id, fav_resource_rows
from omni.domain.contracts import BusinessError

INFO = {"id": 778, "title": "默认收藏夹", "media_count": 2}
MEDIAS = [
    {
        "id": 1001,
        "type": 2,
        "title": "视频<一>",
        "bvid": "BV1xx411c7mD",
        "duration": 61,
        "fav_time": 1700000000,
        "upper": {"name": "UP主"},
    },
    {"id": 1002, "type": 12, "title": "音频", "duration": 30},
    {
        "id": 1003,
        "type": 2,
        "title": "视频二",
        "bvid": "BV1yy411c7mE",
        "duration": 62,
        "fav_time": 1700000001,
        "upper": {"name": "UP2"},
    },
]
ROWS = [
    {
        "bvid": "BV1xx411c7mD",
        "title": "视频",
        "author": "UP主",
        "duration_s": 61,
        "fav_time": 1700000000,
    },
    {"bvid": "BV1yy411c7mE", "title": "视频二", "author": "UP2", "duration_s": 62, "fav_time": 1700000001},
]


def fixture(*, data=None, code=0):
    state = {"data": {"medias": MEDIAS, "info": INFO, "has_more": False} if data is None else data, "code": code}
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.path.endswith("/nav"):
            return httpx.Response(200, json={"code": 0, "data": NAV}, headers={"content-type": "application/json"})
        if request.url.path == "/x/v3/fav/resource/list":
            return httpx.Response(
                200,
                json={"code": state["code"], "message": "fixture", "data": state["data"]},
                headers={"content-type": "application/json"},
            )
        raise AssertionError(f"Unexpected endpoint: {request.url}")

    async def no_delivery(_):
        raise AssertionError("No writes")

    gateway = BilibiliGateway(
        Secrets(),
        Redactor(),
        no_delivery,
        client_factory=lambda **options: httpx.AsyncClient(transport=httpx.MockTransport(handle), **options),
    )
    return gateway, state, calls


def test_fav_resource_rows_shapes_video_rows_and_skips_non_video_media():
    assert fav_resource_rows({"medias": MEDIAS, "info": INFO, "has_more": False}, 10) == {
        "title": "默认收藏夹",
        "media_count": 2,
        "items": ROWS,
        "has_more": False,
    }


def test_fav_resource_rows_absent_medias_is_an_empty_folder():
    assert fav_resource_rows({}, 10) == {"title": "", "media_count": None, "items": [], "has_more": False}


def test_fav_resource_rows_skips_deleted_entries_without_a_bvid():
    rows = [{"bvid": ""}, {"bvid": None}, {"title": "已失效视频"}]
    assert fav_resource_rows({"medias": rows}, 10)["items"] == []


def test_fav_resource_rows_rejects_broken_bvid_identity():
    for bvid in (42, "BVbad", "av123", ["BV1xx411c7mD"]):
        with pytest.raises(BusinessError) as caught:
            fav_resource_rows({"medias": [{"bvid": bvid}]}, 10)
        assert caught.value.code == "bilibili_response_invalid" and caught.value.status == 502


@pytest.mark.parametrize("medias", [{}, "invalid", 42, [1]])
def test_fav_resource_rows_rejects_broken_shapes(medias):
    with pytest.raises(BusinessError) as caught:
        fav_resource_rows({"medias": medias}, 10)
    assert caught.value.code == "bilibili_response_invalid"


def test_fav_resource_rows_rejects_too_many_rows():
    with pytest.raises(BusinessError) as caught:
        fav_resource_rows({"medias": [{"bvid": "BV1xx411c7mD"} for _ in range(51)]}, 10)
    assert caught.value.code == "bilibili_response_invalid"


def test_fav_resource_rows_optional_fields_stay_unknown():
    rows = [{"bvid": "BV1xx411c7mD", "title": None, "duration": "61", "fav_time": 1.5}]
    assert fav_resource_rows({"medias": rows}, 10)["items"] == [
        {"bvid": "BV1xx411c7mD", "title": "", "author": "", "duration_s": None, "fav_time": None}
    ]


def test_fav_resource_rows_limit_and_has_more():
    assert len(fav_resource_rows({"medias": MEDIAS}, 2)["items"]) == 2
    assert fav_resource_rows({"medias": MEDIAS}, 2)["has_more"] is True
    assert len(fav_resource_rows({"medias": MEDIAS}, 5)["items"]) == 2
    assert fav_resource_rows({"medias": MEDIAS}, 5)["has_more"] is False
    assert fav_resource_rows({"medias": MEDIAS, "has_more": 1}, 5)["has_more"] is True
    assert fav_resource_rows({"medias": MEDIAS, "has_more": 0}, 5)["has_more"] is False
    assert fav_resource_rows({"medias": MEDIAS, "has_more": True}, 5)["has_more"] is True


def test_fav_folder_id_normalizes_and_rejects_bad_identities():
    assert fav_folder_id(778) == "778" and fav_folder_id("12") == "12"
    for value in ("", "0", "-1", "1" * 20, 2**63, "12x", "BV1xx411c7mD", True):
        with pytest.raises(BusinessError) as caught:
            fav_folder_id(value)
        assert caught.value.code == "bilibili_identity_invalid" and caught.value.status == 422


def test_fav_resources_gateway_uses_the_fixed_endpoint_and_parses_rows():
    async def scenario():
        gateway, _, calls = fixture()
        try:
            result = await gateway.read(CONFIG, "fav_resources", "778", 5)
            assert result == {
                "folder_id": "778",
                "title": "默认收藏夹",
                "media_count": 2,
                "items": ROWS,
                "has_more": False,
            }
            assert [c.url.path for c in calls] == ["/x/web-interface/nav", "/x/v3/fav/resource/list"]
            query = calls[-1].url.params
            assert query["media_id"] == "778"
            assert query["pn"] == "1" and query["ps"] == "20"
            assert query["order"] == "mtime" and query["platform"] == "web"
            assert "SESSDATA=" in calls[-1].headers["cookie"]
            assert not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("target", ["", "abc", "0", "-1", "1" * 20, "12x", "BV1xx411c7mD"])
def test_fav_resources_invalid_folder_rejected_before_network(target):
    async def scenario():
        gateway, _, calls = fixture()
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.read(CONFIG, "fav_resources", target)
            assert caught.value.code == "bilibili_identity_invalid" and caught.value.status == 422
            assert not calls and not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_fav_resources_risk_code_maps_to_the_typed_pause_signal():
    async def scenario():
        gateway, _, _ = fixture(code=12015)
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.read(CONFIG, "fav_resources", "778")
            assert caught.value.code == "bilibili_risk_code" and caught.value.detail == 12015
            assert not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_fav_resources_malformed_medias_fail_explicitly():
    async def scenario():
        gateway, _, _ = fixture(data={"medias": "invalid"})
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.read(CONFIG, "fav_resources", "778")
            assert caught.value.code == "bilibili_response_invalid" and caught.value.status == 502
            assert not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_fav_resources_exposed_in_tool_spec_and_http_input():
    from omni.adapters.http import BilibiliReadInput
    from omni.application.bilibili import READ_SPEC

    assert "fav_resources" in READ_SPEC.parameters["properties"]["operation"]["enum"]
    assert "fav_resources" in typing.get_args(BilibiliReadInput.model_fields["operation"].annotation)
