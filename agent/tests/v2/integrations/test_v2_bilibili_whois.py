"""Public space whois: bounded uid target, WBI-signed acc/info, identity-matched plain-text profile."""

from __future__ import annotations

import asyncio
import typing

import httpx
import pytest
from omni_test_support.v2.bilibili_gateway import CONFIG, Secrets
from omni_test_support.v2.bilibili_response_bounds import NAV

from omni.adapters.bilibili import BilibiliGateway
from omni.adapters.diagnostics import Redactor
from omni.domain.bilibili import bounded_text, whois_profile
from omni.domain.contracts import BusinessError

PROFILE = {"mid": 123, "name": "某UP", "sex": "男", "sign": "签名<保留>文本", "level": 6}


def fixture(*, profile=None):
    state = {"profile": PROFILE if profile is None else profile}
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.path.endswith("/nav"):
            data = NAV
        elif request.url.path == "/x/space/wbi/acc/info":
            data = state["profile"]
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


def test_bounded_text_keeps_markup_and_bounds():
    assert bounded_text("a<b>c", 100) == "a<b>c"
    assert bounded_text(None, 100) == ""
    assert bounded_text("字" * 300, 200) == "字" * 200


@pytest.mark.parametrize("value", [42, {"a": 1}, ["x"], "\ud800"])
def test_bounded_text_rejects_non_string_or_bad_unicode(value):
    with pytest.raises(BusinessError) as caught:
        bounded_text(value, 100)
    assert caught.value.code == "bilibili_response_invalid" and caught.value.status == 502


def test_whois_profile_shapes_bounded_fields():
    assert whois_profile(PROFILE, "123") == {
        "mid": "123",
        "name": "某UP",
        "sex": "男",
        "sign": "签名<保留>文本",
        "level": 6,
    }


def test_whois_profile_identity_must_match():
    with pytest.raises(BusinessError) as caught:
        whois_profile({**PROFILE, "mid": 999}, "123")
    assert caught.value.code == "bilibili_source_changed"


def test_whois_profile_level_optional_and_text_defaults():
    assert whois_profile({"mid": 123}, "123") == {"mid": "123", "name": "", "sex": "", "sign": "", "level": None}


def test_whois_profile_malformed_text_fails():
    with pytest.raises(BusinessError) as caught:
        whois_profile({**PROFILE, "name": 42}, "123")
    assert caught.value.code == "bilibili_response_invalid" and caught.value.status == 502


def test_whois_gateway_signs_wbi_mid_and_parses_profile():
    async def scenario():
        gateway, _, calls = fixture()
        try:
            assert await gateway.read(CONFIG, "whois", "123") == {
                "mid": "123",
                "name": "某UP",
                "sex": "男",
                "sign": "签名<保留>文本",
                "level": 6,
            }
            assert [c.url.path for c in calls] == ["/x/web-interface/nav", "/x/space/wbi/acc/info"]
            query = calls[-1].url.params
            assert query["mid"] == "123" and query["w_rid"] and query["wts"]
            assert not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("target", ["", "abc", "0", "-1", "1" * 20, "12x"])
def test_whois_invalid_mid_rejected_before_network(target):
    async def scenario():
        gateway, _, calls = fixture()
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.read(CONFIG, "whois", target)
            assert caught.value.code == "bilibili_identity_invalid" and caught.value.status == 422
            assert not calls and not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_whois_identity_changed_rejected():
    async def scenario():
        gateway, _, _ = fixture(profile={**PROFILE, "mid": 999})
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.read(CONFIG, "whois", "123")
            assert caught.value.code == "bilibili_source_changed"
            assert not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_whois_malformed_response_fails():
    async def scenario():
        gateway, _, _ = fixture(profile={**PROFILE, "name": 42})
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.read(CONFIG, "whois", "123")
            assert caught.value.code == "bilibili_response_invalid" and caught.value.status == 502
            assert not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_whois_exposed_in_tool_spec_and_http_input():
    from omni.adapters.http import BilibiliReadInput
    from omni.application.bilibili import READ_SPEC

    assert "whois" in READ_SPEC.parameters["properties"]["operation"]["enum"]
    assert "whois" in typing.get_args(BilibiliReadInput.model_fields["operation"].annotation)
