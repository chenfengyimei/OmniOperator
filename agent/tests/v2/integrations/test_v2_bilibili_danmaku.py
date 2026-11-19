"""Official danmaku sample: fixed comment host, bounded deflate decode, regex XML parse; fake HTTP only."""

from __future__ import annotations

import asyncio
import typing
import zlib

import httpx
import pytest
from omni_test_support.v2.bilibili_gateway import CONFIG, Secrets
from omni_test_support.v2.bilibili_response_bounds import BV, NAV

from omni.adapters.bilibili import BilibiliGateway, _danmaku_xml
from omni.adapters.diagnostics import Redactor
from omni.domain.bilibili import DANMAKU_MAX_ROWS, DANMAKU_TEXT_CHARS, danmaku_url, parse_danmaku
from omni.domain.contracts import BusinessError

XML = (
    "<i>"
    '<d p="1.5,1,25,16777215,0,0,0,0">你好&amp;世界</d>'
    '<d p="2.25,1,25,16777215,0,0,0,0">第二弹</d>'
    '<d p="bad,1,25,0,0,0,0,0">跳过</d>'
    '<d p="-1,1,25,0,0,0,0,0">负时间</d>'
    "</i>"
)


def deflate(raw: bytes, wbits: int = -15) -> bytes:
    co = zlib.compressobj(wbits=wbits)
    return co.compress(raw) + co.flush()


def fixture(*, body=None):
    state = {"body": XML.encode("utf-8") if body is None else body}
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.host == "comment.bilibili.com":
            assert request.url.path == "/789.xml"
            return httpx.Response(200, content=state["body"])
        if request.url.path.endswith("/nav"):
            data = NAV
        elif request.url.path.endswith("/view"):
            data = {"bvid": BV, "aid": 123, "cid": 789, "title": "Fixture", "duration": 120}
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


def test_danmaku_url_fixed_host_and_uid():
    assert danmaku_url("789") == "https://comment.bilibili.com/789.xml"
    assert danmaku_url(789) == "https://comment.bilibili.com/789.xml"


@pytest.mark.parametrize("cid", ["", "abc", "0", "-1", "1" * 20])
def test_danmaku_url_rejects_invalid_cid(cid):
    with pytest.raises(BusinessError) as caught:
        danmaku_url(cid)
    assert caught.value.code == "bilibili_identity_invalid"


def test_parse_danmaku_extracts_time_and_unescapes():
    assert parse_danmaku(XML) == [{"t": 1.5, "text": "你好&世界"}, {"t": 2.25, "text": "第二弹"}]


def test_parse_danmaku_empty_and_no_rows():
    assert parse_danmaku("") == []
    assert parse_danmaku("<i></i>") == []
    assert parse_danmaku("not xml") == []


def test_parse_danmaku_bounds_rows_and_text():
    many = "".join(f'<d p="{i},1,25,0,0,0,0,0">{"字" * 200}</d>' for i in range(DANMAKU_MAX_ROWS + 50))
    rows = parse_danmaku(many)
    assert len(rows) == DANMAKU_MAX_ROWS
    assert all(len(r["text"]) == DANMAKU_TEXT_CHARS for r in rows)


def test_parse_danmaku_skips_non_finite_and_negative():
    xml = (
        '<d p="nan,1,25,0,0,0,0,0">x</d><d p="inf,1,25,0,0,0,0,0">y</d>'
        '<d p="-5,1,25,0,0,0,0,0">z</d><d p="3.0,1,25,0,0,0,0,0">ok</d>'
    )
    assert parse_danmaku(xml) == [{"t": 3.0, "text": "ok"}]


def test_danmaku_xml_plain_utf8():
    assert _danmaku_xml(XML.encode("utf-8")) == XML


def test_danmaku_xml_raw_deflate():
    assert _danmaku_xml(deflate(XML.encode("utf-8"))) == XML


def test_danmaku_xml_zlib_and_gzip():
    assert _danmaku_xml(zlib.compress(XML.encode("utf-8"))) == XML
    assert _danmaku_xml(deflate(XML.encode("utf-8"), 31)) == XML


def test_danmaku_xml_bomb_rejected():
    huge = "<i>" + '<d p="0,1,25,0,0,0,0,0">x</d>' * 300000 + "</i>"
    with pytest.raises(BusinessError) as caught:
        _danmaku_xml(deflate(huge.encode("utf-8")))
    assert caught.value.code == "bilibili_response_invalid" and caught.value.status == 502


def test_danmaku_xml_undecodable_rejected():
    with pytest.raises(BusinessError) as caught:
        _danmaku_xml(b"\x00\x01\x02not-xml-or-deflate")
    assert caught.value.code == "bilibili_response_invalid" and caught.value.status == 502


def test_danmaku_gateway_reads_sample_via_fixed_host():
    async def scenario():
        gateway, _, calls = fixture()
        try:
            out = await gateway.read(CONFIG, "danmaku", BV)
            assert out["bvid"] == BV and out["cid"] == "789" and out["sampled"] is True
            assert out["items"] == [{"t": 1.5, "text": "你好&世界"}, {"t": 2.25, "text": "第二弹"}]
            assert out["count"] == 2
            assert [c.url.host for c in calls] == ["api.bilibili.com", "api.bilibili.com", "comment.bilibili.com"]
            assert not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_danmaku_gateway_decodes_deflate_body():
    async def scenario():
        gateway, _, _ = fixture(body=deflate(XML.encode("utf-8")))
        try:
            out = await gateway.read(CONFIG, "danmaku", BV)
            assert out["count"] == 2 and out["items"][0]["text"] == "你好&世界"
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_danmaku_gateway_empty_xml_is_normal():
    async def scenario():
        gateway, _, _ = fixture(body=b"<i></i>")
        try:
            out = await gateway.read(CONFIG, "danmaku", BV)
            assert out["items"] == [] and out["count"] == 0 and out["sampled"] is True
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_danmaku_gateway_invalid_bv_rejected_before_network():
    async def scenario():
        gateway, _, calls = fixture()
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.read(CONFIG, "danmaku", "not-a-bv")
            assert caught.value.code == "bilibili_read_invalid" and caught.value.status == 422
            assert not calls and not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_danmaku_exposed_in_tool_spec_and_http_input():
    from omni.adapters.http import BilibiliReadInput
    from omni.application.bilibili import READ_SPEC

    assert "danmaku" in READ_SPEC.parameters["properties"]["operation"]["enum"]
    assert "danmaku" in typing.get_args(BilibiliReadInput.model_fields["operation"].annotation)
