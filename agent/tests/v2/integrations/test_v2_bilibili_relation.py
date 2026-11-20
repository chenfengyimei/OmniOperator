"""DM follow relationship: bounded uid target, plain GET x/relation (no WBI, never cached), discovery only."""

from __future__ import annotations

import asyncio
import typing

import httpx
import pytest
from omni_test_support.v2.bilibili_gateway import CONFIG, Secrets
from omni_test_support.v2.bilibili_response_bounds import NAV

from omni.adapters.bilibili import BilibiliGateway
from omni.adapters.diagnostics import Redactor
from omni.domain.bilibili import relation_view
from omni.domain.contracts import BusinessError

RELATION = {"attribute": 6, "be_relation": {"attribute": 2}}


def fixture(*, relation=None):
    state = {"relation": RELATION if relation is None else relation}
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.path.endswith("/nav"):
            data = NAV
        elif request.url.path == "/x/relation":
            data = state["relation"]
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


def test_relation_view_mutual_parses_both_directions():
    assert relation_view(RELATION, "123") == {
        "mid": "123",
        "attribute": 6,
        "be_attribute": 2,
        "follows": True,
        "followed_by": True,
        "mutual": True,
        "blocked": False,
    }


def test_relation_view_not_followed():
    assert relation_view({"attribute": 0}, "123") == {
        "mid": "123",
        "attribute": 0,
        "be_attribute": None,
        "follows": False,
        "followed_by": False,
        "mutual": False,
        "blocked": False,
    }


def test_relation_view_followed_one_way():
    out = relation_view({"attribute": 2}, "123")
    assert out["follows"] is True and out["followed_by"] is False and out["mutual"] is False
    assert out["be_attribute"] is None and out["blocked"] is False


def test_relation_view_followed_by_via_be_relation():
    out = relation_view({"attribute": 0, "be_relation": {"attribute": 2}}, "123")
    assert out["follows"] is False and out["followed_by"] is True and out["mutual"] is False
    assert out["be_attribute"] == 2


def test_relation_view_blocked_owner_side():
    out = relation_view({"attribute": 128}, "123")
    assert out["blocked"] is True and out["follows"] is False and out["mutual"] is False


def test_relation_view_blocked_reverse_side():
    out = relation_view({"attribute": 0, "be_relation": {"attribute": 128}}, "123")
    assert out["blocked"] is True and out["be_attribute"] == 128 and out["followed_by"] is False


def test_relation_view_be_relation_absent_or_malformed_is_unknown():
    absent = relation_view({"attribute": 2}, "123")
    assert absent["be_attribute"] is None and absent["followed_by"] is False
    malformed = relation_view({"attribute": 2, "be_relation": "changed"}, "123")
    assert malformed["be_attribute"] is None and malformed["followed_by"] is False
    non_int = relation_view({"attribute": 6, "be_relation": {"attribute": "2"}}, "123")
    assert non_int["be_attribute"] is None and non_int["mutual"] is True and non_int["followed_by"] is True


@pytest.mark.parametrize("data", [{"attribute": "6"}, {"attribute": None}, {}, {"attribute": 6.0}])
def test_relation_view_attribute_non_int_fails_explicitly(data):
    with pytest.raises(BusinessError) as caught:
        relation_view(data, "123")
    assert caught.value.code == "bilibili_response_invalid" and caught.value.status == 502


def test_relation_gateway_plain_get_fid_no_wbi():
    async def scenario():
        gateway, _, calls = fixture()
        try:
            out = await gateway.read(CONFIG, "relation", "123")
            assert out["mid"] == "123" and out["attribute"] == 6 and out["mutual"] is True
            assert [c.url.path for c in calls] == ["/x/web-interface/nav", "/x/relation"]
            query = calls[-1].url.params
            assert query["fid"] == "123"
            assert "w_rid" not in query and "wts" not in query
            assert not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("target", ["", "abc", "0", "-1", "1" * 20, "12x"])
def test_relation_invalid_mid_rejected_before_network(target):
    async def scenario():
        gateway, _, calls = fixture()
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.read(CONFIG, "relation", target)
            assert caught.value.code == "bilibili_identity_invalid" and caught.value.status == 422
            assert not calls and not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_relation_gateway_malformed_response_fails():
    async def scenario():
        gateway, _, _ = fixture(relation={"attribute": "6"})
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.read(CONFIG, "relation", "123")
            assert caught.value.code == "bilibili_response_invalid" and caught.value.status == 502
            assert not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_relation_exposed_in_tool_spec_and_http_input():
    from omni.adapters.http import BilibiliReadInput
    from omni.application.bilibili import READ_SPEC

    assert "relation" in READ_SPEC.parameters["properties"]["operation"]["enum"]
    assert "relation" in typing.get_args(BilibiliReadInput.model_fields["operation"].annotation)
