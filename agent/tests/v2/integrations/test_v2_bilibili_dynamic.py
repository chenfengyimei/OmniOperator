"""Owner space dynamics: bounded uid target, fixed VC host, strict desc identity, best-effort card text."""

from __future__ import annotations

import asyncio
import json
import typing

import httpx
import pytest
from omni_test_support.v2.bilibili_gateway import CONFIG, Secrets
from omni_test_support.v2.bilibili_response_bounds import NAV

from omni.adapters.bilibili import BilibiliGateway
from omni.adapters.diagnostics import Redactor
from omni.domain.bilibili import DYNAMIC_HISTORY, dynamic_rows
from omni.domain.contracts import BusinessError

CARD = json.dumps({"item": {"description": "新的动态 <b>正文</b>"}}, ensure_ascii=False)
CARDS = [
    {"desc": {"dynamic_id_str": "588320531406678918", "timestamp": 1700000000, "type": 4}, "card": CARD},
    {
        "desc": {"dynamic_id_str": "588320531406678919", "timestamp": 1700000001, "type": 1},
        "card": json.dumps({"item": {"content": "content fallback"}}),
    },
]


def fixture(*, data=None, code=0):
    state = {"data": {"cards": CARDS, "has_more": 0} if data is None else data, "code": code}
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.path.endswith("/nav"):
            return httpx.Response(200, json={"code": 0, "data": NAV}, headers={"content-type": "application/json"})
        if str(request.url).startswith(DYNAMIC_HISTORY):
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


def test_dynamic_rows_shapes_strict_identity_and_bounded_text():
    rows = dynamic_rows({"cards": CARDS, "has_more": 0}, 10)
    assert rows == {
        "items": [
            {"dynamic_id": "588320531406678918", "timestamp": 1700000000, "type": 4, "text": "新的动态 正文"},
            {"dynamic_id": "588320531406678919", "timestamp": 1700000001, "type": 1, "text": "content fallback"},
        ],
        "has_more": False,
    }


def test_dynamic_rows_absent_cards_is_an_empty_page():
    assert dynamic_rows({}, 10) == {"items": [], "has_more": False}


def test_dynamic_rows_optional_timestamp_and_type_stay_unknown():
    rows = dynamic_rows({"cards": [{"desc": {"dynamic_id_str": "1"}, "card": ""}]}, 10)
    assert rows["items"] == [{"dynamic_id": "1", "timestamp": None, "type": None, "text": ""}]


def test_dynamic_rows_keeps_identity_even_when_the_card_is_unusable():
    for card in ("not-json", 42, None, json.dumps({"item": {"description": 42}}), json.dumps({"item": "x"})):
        rows = dynamic_rows({"cards": [{"desc": {"dynamic_id_str": "9"}, "card": card}]}, 10)
        assert rows["items"] == [{"dynamic_id": "9", "timestamp": None, "type": None, "text": ""}]


def test_dynamic_rows_skips_oversized_cards_without_failing():
    card = json.dumps({"item": {"description": "x" * (70 * 1024)}})
    rows = dynamic_rows({"cards": [{"desc": {"dynamic_id_str": "9"}, "card": card}]}, 10)
    assert rows["items"][0]["text"] == ""


def test_dynamic_rows_text_is_bounded_to_200_characters():
    card = json.dumps({"item": {"description": "字" * 500}})
    rows = dynamic_rows({"cards": [{"desc": {"dynamic_id_str": "9"}, "card": card}]}, 10)
    assert rows["items"][0]["text"] == "字" * 200


def test_dynamic_rows_rejects_non_list_cards():
    with pytest.raises(BusinessError) as caught:
        dynamic_rows({"cards": {}}, 10)
    assert caught.value.code == "bilibili_response_invalid" and caught.value.status == 502


def test_dynamic_rows_rejects_too_many_cards():
    with pytest.raises(BusinessError) as caught:
        dynamic_rows({"cards": [{"desc": {"dynamic_id_str": "1"}} for _ in range(51)]}, 10)
    assert caught.value.code == "bilibili_response_invalid"


@pytest.mark.parametrize(
    "row",
    [
        "invalid",
        {"card": ""},
        {"desc": "invalid"},
        {"desc": {"dynamic_id_str": 5}},
        {"desc": {"dynamic_id_str": "BV1xx411c7mD"}},
        {"desc": {"dynamic_id_str": ""}},
        {"desc": {"dynamic_id_str": "1" * 33}},
    ],
)
def test_dynamic_rows_rejects_broken_identity(row):
    with pytest.raises(BusinessError) as caught:
        dynamic_rows({"cards": [row]}, 10)
    assert caught.value.code == "bilibili_response_invalid" and caught.value.status == 502


def test_dynamic_rows_limit_and_has_more():
    cards = [{"desc": {"dynamic_id_str": str(n)}} for n in range(1, 4)]
    assert dynamic_rows({"cards": cards}, 2)["has_more"] is True
    assert len(dynamic_rows({"cards": cards}, 2)["items"]) == 2
    assert dynamic_rows({"cards": cards, "has_more": 1}, 2)["has_more"] is True
    assert dynamic_rows({"cards": cards, "has_more": 0}, 2)["has_more"] is False
    assert dynamic_rows({"cards": cards, "has_more": True}, 10)["has_more"] is True


def test_dynamic_list_gateway_uses_the_fixed_vc_host_and_parses_cards():
    async def scenario():
        gateway, _, calls = fixture()
        try:
            result = await gateway.read(CONFIG, "dynamic_list", "123", 5)
            assert result == {"mid": "123", "items": dynamic_rows({"cards": CARDS}, 5)["items"], "has_more": False}
            assert [c.url.path for c in calls] == ["/x/web-interface/nav", "/dynamic_svr/v1/dynamic_svr/space_history"]
            history = calls[-1]
            assert history.url.host == "api.vc.bilibili.com"
            assert history.url.params["host_uid"] == "123"
            assert history.url.params["offset_dynamic_id"] == "0"
            assert history.url.params["need_top"] == "0"
            assert "SESSDATA=" in history.headers["cookie"]
            assert not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("target", ["", "abc", "0", "-1", "1" * 20, "12x", "BV1xx411c7mD"])
def test_dynamic_list_invalid_mid_rejected_before_network(target):
    async def scenario():
        gateway, _, calls = fixture()
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.read(CONFIG, "dynamic_list", target)
            assert caught.value.code == "bilibili_identity_invalid" and caught.value.status == 422
            assert not calls and not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_dynamic_list_risk_code_maps_to_the_typed_pause_signal():
    async def scenario():
        gateway, _, _ = fixture(code=12015)
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.read(CONFIG, "dynamic_list", "123")
            assert caught.value.code == "bilibili_risk_code" and caught.value.detail == 12015
            assert not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_dynamic_list_malformed_cards_fail_explicitly():
    async def scenario():
        gateway, _, _ = fixture(data={"cards": "invalid"})
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.read(CONFIG, "dynamic_list", "123")
            assert caught.value.code == "bilibili_response_invalid" and caught.value.status == 502
            assert not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_dynamic_list_exposed_in_tool_spec_and_http_input():
    from omni.adapters.http import BilibiliReadInput
    from omni.application.bilibili import READ_SPEC

    assert "dynamic_list" in READ_SPEC.parameters["properties"]["operation"]["enum"]
    assert "dynamic_list" in typing.get_args(BilibiliReadInput.model_fields["operation"].annotation)
