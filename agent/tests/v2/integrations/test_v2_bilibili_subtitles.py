"""Official subtitle format changes fail explicitly instead of publishing coerced or partial text."""

from __future__ import annotations

import asyncio
import copy
import json

import httpx
import pytest
from omni_test_support.v2.bilibili_gateway import CONFIG, Secrets
from omni_test_support.v2.bilibili_response_bounds import BV, NAV, SOURCE, SUBTITLE, Body

from omni.adapters.bilibili import BilibiliGateway
from omni.adapters.diagnostics import Redactor
from omni.domain.contracts import BusinessError

TRACK = {"lan": "zh-CN", "subtitle_url": SUBTITLE}
PLAYER = {"subtitle": {"subtitles": [TRACK]}}
CONTENT = {"body": [{"content": "真实字幕"}, {"content": "第二行"}]}


def fixture(*, player=None, content=None):
    state = {
        "player": copy.deepcopy(PLAYER if player is None else player),
        "content": CONTENT if content is None else content,
    }
    calls, bodies = [], []

    def handle(request):
        calls.append(request)
        if request.url.host == "aisubtitle.hdslb.com":
            body = Body([json.dumps(state["content"], ensure_ascii=True).encode()])
            bodies.append(body)
            return httpx.Response(200, headers={"content-type": "application/json"}, stream=body)
        if request.url.path.endswith("/nav"):
            data = NAV
        elif request.url.path.endswith("/view"):
            data = {**SOURCE, "title": "Fixture", "duration": 120}
        elif request.url.path == "/x/player/wbi/v2":
            assert request.url.params["cid"] == SOURCE["cid"]
            data = state["player"]
        else:
            raise AssertionError("Unexpected endpoint")
        return httpx.Response(
            200,
            content=json.dumps({"code": 0, "data": data}, ensure_ascii=True).encode(),
            headers={"content-type": "application/json"},
        )

    async def no_delivery(_):
        raise AssertionError("No writes")

    gateway = BilibiliGateway(
        Secrets(),
        Redactor(),
        no_delivery,
        client_factory=lambda **options: httpx.AsyncClient(transport=httpx.MockTransport(handle), **options),
    )
    return gateway, state, calls, bodies


@pytest.mark.parametrize(
    "player",
    [
        {"subtitle": False},
        {"subtitle": [TRACK]},
        {"subtitle": "changed"},
        {"subtitle": {"subtitles": {}}},
        {"subtitle": {"subtitles": "changed"}},
        {"subtitle": {"subtitles": False}},
        {"subtitle": {"subtitles": [None, TRACK]}},
        {"subtitle": {"subtitles": [TRACK, "broken unselected record"]}},
        {"subtitle": {"subtitles": [{**TRACK, "lan": None}]}},
        {"subtitle": {"subtitles": [{**TRACK, "lan": 123}]}},
        {"subtitle": {"subtitles": [{"lan": "zh-CN"}]}},
        {"subtitle": {"subtitles": [{**TRACK, "subtitle_url": ""}]}},
    ],
)
def test_malformed_player_cannot_become_no_subtitle_or_dispatch_content(player):
    async def scenario():
        gateway, _, calls, bodies = fixture(player=player)
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.read_source(CONFIG, "subtitle", SOURCE)
            assert caught.value.code == "bilibili_response_invalid" and caught.value.status == 502
            assert not bodies and all(request.url.host == "api.bilibili.com" for request in calls)
            assert not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "lines",
    [
        [{"content": "valid prefix"}, None],
        [{"content": "valid prefix"}, "changed"],
        [{"content": "valid prefix"}, {}],
        [{"content": "valid prefix"}, {"content": None}],
        [{"content": "valid prefix"}, {"content": 42}],
        [{"content": "valid prefix"}, {"content": True}],
        [{"content": "valid prefix"}, {"content": ["invented"]}],
        [{"content": "valid prefix"}, {"content": {"invented": "text"}}],
        [{"content": "valid prefix"}, {"content": "\ud800"}],
    ],
)
def test_bad_body_rows_cannot_be_dropped_coerced_or_return_invalid_unicode(lines):
    async def scenario():
        gateway, _, _, bodies = fixture(content={"body": lines})
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.read(CONFIG, "subtitle", BV)
            assert caught.value.code == "bilibili_response_invalid" and caught.value.status == 502
            assert len(bodies) == 1 and bodies[0].closed and not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "player",
    [{}, {"subtitle": None}, {"subtitle": {}}, {"subtitle": {"subtitles": None}}, {"subtitle": {"subtitles": []}}],
)
def test_absent_official_subtitles_remain_an_empty_result_without_content_request(player):
    async def scenario():
        gateway, _, calls, bodies = fixture(player=player)
        try:
            assert await gateway.read_source(CONFIG, "subtitle", SOURCE) == {"language": "", "text": ""}
            assert len(calls) == 3 and not bodies
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["chinese_first", "fallback", "blank_lines", "text_boundary", "language_boundary"])
def test_valid_subtitle_selection_and_text_preserve_existing_payload(case):
    async def scenario():
        gateway, state, calls, bodies = fixture()
        language, text = "zh-CN", "真实字幕\n第二行"
        if case == "chinese_first":
            state["player"] = {"subtitle": {"subtitles": [{"lan": "en", "subtitle_url": SUBTITLE + "?en"}, TRACK]}}
        elif case == "fallback":
            state["player"] = {"subtitle": {"subtitles": [{"subtitle_url": SUBTITLE}]}}
            language = ""
        elif case == "blank_lines":
            state["content"] = {"body": [{"content": " "}, {"content": "真实字幕"}, {"content": ""}]}
            text = "真实字幕"
        elif case == "text_boundary":
            state["content"] = {"body": [{"content": "字" * 60000}]}
            text = "字" * 60000
        elif case == "language_boundary":
            state["player"] = {"subtitle": {"subtitles": [{**TRACK, "lan": "zh-" + "a" * 20}]}}
            language = "zh-" + "a" * 13
        try:
            assert await gateway.read_source(CONFIG, "subtitle", SOURCE) == {"language": language, "text": text}
            assert len(calls) == 4 and bodies[0].closed and not gateway._reads
            assert not calls[-1].url.query
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["blank", "text_overflow", "language_unicode", "too_many_tracks"])
def test_empty_overlong_and_unserializable_results_keep_explicit_format_error(case):
    async def scenario():
        gateway, state, _, _ = fixture()
        if case in {"blank", "text_overflow"}:
            state["content"] = {"body": [{"content": " \n " if case == "blank" else "字" * 60001}]}
        elif case == "language_unicode":
            state["player"] = {"subtitle": {"subtitles": [{**TRACK, "lan": "zh-\ud800"}]}}
        else:
            state["player"] = {"subtitle": {"subtitles": [TRACK] * 33}}
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.read_source(CONFIG, "subtitle", SOURCE)
            assert caught.value.code == "bilibili_response_invalid"
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["bad_player", "bad_row", "bad_unicode"])
def test_real_media_job_never_publishes_malformed_or_partial_subtitle(tmp_path, monkeypatch, case):
    from omni_test_support.v2.media import _media_instance, _seed, _settle

    async def scenario():
        gateway, state, _, bodies = fixture()
        instance = await _media_instance(tmp_path, gateway=gateway)
        published, publish = [], instance.media._objects.publish

        async def observe(*args, **kwargs):
            published.append(args)
            return await publish(*args, **kwargs)

        monkeypatch.setattr(instance.media._objects, "publish", observe)
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            if case == "bad_player":
                state["player"] = {"subtitle": {"subtitles": [{**TRACK, "lan": None}]}}
            else:
                value = {"object": "invented text"} if case == "bad_row" else "\ud800"
                state["content"] = {"body": [{"content": "valid prefix"}, {"content": value}]}
            job = await instance.media.submit(agent, str(source["id"]), "subtitle", {})
            failed = await _settle(instance, agent, job["id"])
            assert failed["state"] == "failed" and failed["error_code"] == "bilibili_response_invalid"
            assert not published and not instance.media._processor.calls
            assert all(body.closed for body in bodies)
            assert not (instance.media._staging / str(job["id"])).exists()
            assert not (await instance.media.source(agent, str(source["id"])))["latest_transcript_id"]
            state.update(player=copy.deepcopy(PLAYER), content=copy.deepcopy(CONTENT))
            fresh = await instance.media.submit(agent, str(source["id"]), "subtitle", {})
            assert (await _settle(instance, agent, fresh["id"]))["state"] == "done"
            assert (await instance.media.job(agent, str(job["id"])))["state"] == "failed"
            assert len(published) == 1
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())
