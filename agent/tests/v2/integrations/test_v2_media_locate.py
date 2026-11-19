"""media_locate: bounded transcript LCS locate keeps anchors, isolation, deletion barrier and read-only wiring."""

from __future__ import annotations

import asyncio

import pytest
from omni_test_support.v2.media import (
    BV,
    FakeGateway,
    FakeProcessor,
    NoModel,
    Secrets,
    _context,
    _media_instance,
    _seed,
    _settle,
    open_instance,
)

from omni.application.media import MediaService
from omni.domain.contracts import BusinessError, Outcome
from omni.domain.media import transcript_payload
from omni.domain.media_locate import (
    DEFAULT_LOCATE_RESULTS,
    EXACT_BONUS,
    MAX_LOCATE_RESULTS,
    MAX_QUERY_CHARS,
    MAX_SNIPPET_CHARS,
    MIN_SCORE,
    match_segments,
    normalize_limit,
    normalize_query,
    render_locate,
    score_segment,
)

ASR_ROWS = [[0.0, 2.5, "你好世界"], [2.5, 4.0, "第二句锚点"]]


# ---------- domain rules ----------


def test_normalize_query_strips_and_bounds():
    assert normalize_query("  你好  ") == "你好"
    for bad in ("", "   ", "x" * (MAX_QUERY_CHARS + 1), "a\nb", "a\tb", 5, None):
        with pytest.raises(BusinessError) as caught:
            normalize_query(bad)
        assert caught.value.code == "media_locate_invalid"


def test_normalize_limit_default_and_bounds():
    assert normalize_limit(None) == DEFAULT_LOCATE_RESULTS
    assert normalize_limit(1) == 1 and normalize_limit(MAX_LOCATE_RESULTS) == MAX_LOCATE_RESULTS
    for bad in (0, MAX_LOCATE_RESULTS + 1, "3", 2.5, True):
        with pytest.raises(BusinessError):
            normalize_limit(bad)


def test_score_exact_substring_gets_bonus_and_casefolds():
    assert score_segment("你好", "你好世界") == 1.0 + EXACT_BONUS
    assert score_segment("HELLO", "hello world") == 1.0 + EXACT_BONUS
    assert score_segment("", "anything") == 0.0


def test_score_fuzzy_lcs_coverage_and_floor():
    # LCS("世界锚点","你好世界") = 世界 = 2 of 4 query chars
    assert score_segment("世界锚点", "你好世界") == pytest.approx(0.5)
    assert score_segment("xyz", "你好世界") == 0.0
    assert 0.5 >= MIN_SCORE > 0.0


def test_match_orders_by_score_then_source_and_limits():
    rows = [[0.0, 1.0, "世界锚点"], [1.0, 2.0, "你好世界"], [2.0, 3.0, "无关"]]
    matches = match_segments("你好世界", rows, MAX_LOCATE_RESULTS)
    # exact substring row scores 1.25 and sorts first despite later source order
    assert matches[0]["segment_index"] == 1 and matches[0]["score"] == 1.25
    assert all(m["score"] >= MIN_SCORE for m in matches)
    assert len(match_segments("你好世界", rows, 1)) == 1


def test_match_preserves_anchors_and_truncates_snippet():
    long_text = "锚点" + "x" * (MAX_SNIPPET_CHARS + 50)
    matches = match_segments("锚点", [[3.0, 9.0, long_text]], 5)
    assert matches[0]["start"] == 3.0 and matches[0]["end"] == 9.0
    assert matches[0]["truncated"] is True and len(matches[0]["text"]) == MAX_SNIPPET_CHARS
    short = match_segments("锚点", [[3.0, 9.0, "锚点"]], 5)
    assert short[0]["truncated"] is False


def test_render_asr_anchors_untimed_subtitle_and_no_match():
    asr = render_locate("你好", "asr", match_segments("你好", ASR_ROWS, 5), 2)
    assert "[00:00-00:02]" in asr and "score=1.25" in asr
    subtitle = render_locate(
        "字幕", "subtitle", [{"start": 0.0, "end": 0.0, "score": 1.25, "text": "字幕", "truncated": False}], 1
    )
    assert "[untimed]" in subtitle
    empty = render_locate("zzz", "asr", [], 2)
    assert "matches=0" in empty and "No segment matched" in empty


# ---------- real instance: anchored ASR + subtitle + model invoke ----------


def test_locate_anchored_asr_and_model_invoke(tmp_path):
    async def scenario():
        instance = await _media_instance(tmp_path)
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            job = await instance.media.submit(agent, str(source["id"]), "transcribe", {})
            assert (await _settle(instance, agent, job["id"]))["state"] == "done"
            src = await instance.media.source(agent, str(source["id"]))
            tid = str(src["latest_transcript_id"])
            result = await instance.media.locate(agent, str(source["id"]), tid, "你好", 5)
            assert result["kind"] == "asr" and result["total_segments"] == 2
            assert len(result["matches"]) == 1
            match = result["matches"][0]
            assert match["start"] == 0.0 and match["end"] == 2.5 and match["score"] == 1.25
            assert match["text"] == "你好世界" and "[00:00-00:02]" in result["text"]
            res = await instance.media.invoke(
                "media_locate",
                {"source_id": str(source["id"]), "query": "你好"},
                operation_id="run",
                context=_context(agent),
            )
            assert res.status == Outcome.OK and res.data is not None
            assert res.data["matches"][0]["score"] == 1.25
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_locate_latest_transcript_without_id(tmp_path):
    async def scenario():
        instance = await _media_instance(tmp_path)
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            job = await instance.media.submit(agent, str(source["id"]), "transcribe", {})
            assert (await _settle(instance, agent, job["id"]))["state"] == "done"
            result = await instance.media.locate(agent, str(source["id"]), None, "锚点", 5)
            assert len(result["matches"]) == 1 and result["matches"][0]["start"] == 2.5
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_locate_subtitle_is_untimed(tmp_path):
    async def scenario():
        instance = await _media_instance(tmp_path)
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            job = await instance.media.submit(agent, str(source["id"]), "subtitle", {})
            assert (await _settle(instance, agent, job["id"]))["state"] == "done"
            result = await instance.media.locate(agent, str(source["id"]), None, "字幕", 5)
            assert result["kind"] == "subtitle" and "[untimed]" in result["text"]
            assert len(result["matches"]) == 1 and result["matches"][0]["score"] == 1.25
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_media_locate_registered_read_only_in_production(tmp_path):
    async def scenario():
        instance = await open_instance(
            tmp_path,
            model=NoModel(),
            bilibili_backend=FakeGateway(),
            media_processor=FakeProcessor(),
            credential_backend=Secrets(),
        )
        try:
            tools = instance.application._tools
            assert "media_locate" in tools
            assert tools["media_locate"].spec.writes is False
            assert tools["media_locate"].spec.resource == "media"
        finally:
            await instance.close()

    asyncio.run(scenario())


# ---------- stub guards: pairing, isolation, closing, deletion barrier ----------


class StubDatabase:
    def __init__(self, exists=True):
        self.calls, self.exists = 0, exists

    async def read(self, operation):
        self.calls += 1
        return operation(self)

    async def write(self, operation):
        raise AssertionError("locate cannot write")

    def media_transcript_get(self, agent_id, transcript_id):
        if not self.exists or agent_id != "agent" or transcript_id != "transcript":
            raise BusinessError("media_transcript_not_found", "No such transcript", status=404)
        return {"id": "transcript", "source_id": "source", "object_id": "object", "kind": "asr", "language": "zh"}

    def media_transcript_latest(self, agent_id, source_id):
        if source_id != "source":
            raise BusinessError("media_transcript_not_found", "No transcript in this source", status=404)
        return self.media_transcript_get(agent_id, "transcript")


class StubObjects:
    def __init__(self, database=None):
        self.calls, self.database = 0, database

    async def get(self, agent_id, object_id):
        assert (agent_id, object_id) == ("agent", "object")
        self.calls += 1
        if self.database is not None:
            self.database.exists = False
        return {}, transcript_payload("asr", "zh", ASR_ROWS)


class StubProcessor:
    def status(self):
        return {"available": True}

    async def close(self, timeout_s=30):
        return True


def _stub(tmp_path, delete_during_read=False):
    database = StubDatabase()
    objects = StubObjects(database if delete_during_read else None)
    service = MediaService(database, objects, None, None, StubProcessor(), None, tmp_path)
    return service, database, objects


def test_locate_source_pairing_mismatch_rejects_before_object(tmp_path):
    async def scenario():
        service, _, objects = _stub(tmp_path)
        try:
            with pytest.raises(BusinessError) as caught:
                await service.locate("agent", "other-source", "transcript", "你好", 5)
            assert caught.value.code == "media_transcript_not_found" and objects.calls == 0
        finally:
            assert await service.close()

    asyncio.run(scenario())


def test_locate_other_agent_never_reads_object(tmp_path):
    async def scenario():
        service, _, objects = _stub(tmp_path)
        try:
            with pytest.raises(BusinessError) as caught:
                await service.locate("other-agent", "source", "transcript", "你好", 5)
            assert caught.value.code == "media_transcript_not_found" and objects.calls == 0
        finally:
            assert await service.close()

    asyncio.run(scenario())


def test_locate_closing_rejects_before_any_lookup(tmp_path):
    async def scenario():
        service, database, objects = _stub(tmp_path)
        service.stop_accepting()
        try:
            with pytest.raises(BusinessError) as caught:
                await service.locate("agent", "source", "transcript", "你好", 5)
            assert caught.value.code == "media_closing" and database.calls == objects.calls == 0
        finally:
            assert await service.close()

    asyncio.run(scenario())


def test_locate_deletion_during_object_read_rejects_loaded_body(tmp_path):
    async def scenario():
        service, _, objects = _stub(tmp_path, delete_during_read=True)
        try:
            with pytest.raises(BusinessError) as caught:
                await service.locate("agent", "source", "transcript", "你好", 5)
            assert caught.value.code == "media_transcript_not_found" and objects.calls == 1
        finally:
            assert await service.close()

    asyncio.run(scenario())
