"""ASR text is model text, never object coercion; invalid output stops before publication."""

from __future__ import annotations

import asyncio
import sys
from textwrap import dedent
from types import SimpleNamespace

import pytest
from test_v2_media_worker import asr_worker as asr_worker

from omni.adapters.media import MediaProcessor
from omni.adapters.workers import media as worker
from omni.domain.media import parse_transcript

BAD_TEXT = {
    "missing": None,
    "null": None,
    "boolean": False,
    "zero": 0,
    "integer": 7,
    "float": 1.5,
    "array": ["invented"],
    "object": {"invented": "text"},
    "bytes": b"invented",
    "bytearray": bytearray(b"invented"),
    "unicode": "\ud800",
    "stringify": None,
}


class NotText:
    def __str__(self):
        raise AssertionError("ASR must not stringify an object into transcript text")


@pytest.mark.parametrize("vad_empty", [False, True])
@pytest.mark.parametrize("case", BAD_TEXT)
def test_first_bad_text_stops_collection_closes_iterator_and_never_falls_back_or_writes(asr_worker, vad_empty, case):
    consumed, closed = [], []

    def segments(vad):
        try:
            if vad_empty and vad:
                yield SimpleNamespace(text=" \n ")
                return
            consumed.append("valid")
            yield SimpleNamespace(start=0, end=1, text="prefix")
            values = {"start": 1, "end": 2, "text": NotText() if case == "stringify" else BAD_TEXT[case]}
            if case == "missing":
                values.pop("text")
            consumed.append("invalid")
            yield SimpleNamespace(**values)
            raise AssertionError("ASR consumed text after the first unusable row")
        finally:
            closed.append(vad)

    fixture = asr_worker(segments)
    with pytest.raises(ValueError, match="^transcript_unusable$"):
        worker._transcribe(fixture.request)
    assert consumed == ["valid", "invalid"]
    assert fixture.calls == closed == ([True, False] if vad_empty else [True])
    assert all(iterator.gi_frame is None for iterator in fixture.iterators)
    assert not fixture.output.exists() and not fixture.output.parent.exists()


def set_language(monkeypatch, value, *, missing=False):
    model = sys.modules["faster_whisper"].WhisperModel
    transcribe = model.transcribe

    def changed(self, *args, **kwargs):
        iterator, info = transcribe(self, *args, **kwargs)
        if missing:
            del info.language
        else:
            info.language = value
        return iterator, info

    monkeypatch.setattr(model, "transcribe", changed)


@pytest.mark.parametrize("value", [False, 0, 7, b"zh", [], {}, "\ud800"])
def test_bad_language_cannot_be_coerced_or_fail_late_while_writing(asr_worker, monkeypatch, value):
    def segments(vad):
        yield SimpleNamespace(start=0, end=1, text="valid text")

    fixture = asr_worker(segments)
    set_language(monkeypatch, value)
    with pytest.raises(ValueError, match="^transcript_unusable$"):
        worker._transcribe(fixture.request)
    assert fixture.calls == [True] and all(iterator.gi_frame is None for iterator in fixture.iterators)
    assert not fixture.output.exists() and not fixture.output.parent.exists()


@pytest.mark.parametrize("case", ["missing", "null", "empty", "normal", "boundary", "truncated"])
def test_valid_unicode_trimming_unknown_language_and_language_window_remain(asr_worker, monkeypatch, case):
    def segments(vad):
        yield SimpleNamespace(text=" \n ")
        yield SimpleNamespace(start=0, end=1, text="  合法🙂  ")

    value = {
        "missing": None,
        "null": None,
        "empty": "",
        "normal": "zh",
        "boundary": "中" * 16,
        "truncated": "x" * 16 + "\ud800",
    }[case]
    fixture = asr_worker(segments)
    set_language(monkeypatch, value, missing=case == "missing")
    result = worker._transcribe(fixture.request)
    expected = "" if value is None else value[:16]
    parsed = parse_transcript(fixture.output.read_bytes())
    assert parsed["language"] == result["language"] == expected
    assert parsed["segments"] == [[0.0, 1.0, "合法🙂"]]
    assert fixture.calls == [True] and all(iterator.gi_frame is None for iterator in fixture.iterators)


def test_close_failure_retains_invalid_text_as_exception_context():
    cleanup = OSError("fixture iterator close failure")

    class Stream:
        consumed = closed = 0

        def __iter__(self):
            return self

        def __next__(self):
            self.consumed += 1
            assert self.consumed == 1
            return SimpleNamespace(start=0, end=1, text=NotText())

        def close(self):
            self.closed += 1
            raise cleanup

    stream = Stream()
    with pytest.raises(OSError) as caught:
        worker._transcript_rows(stream)
    assert caught.value is cleanup and stream.closed == stream.consumed == 1
    assert isinstance(cleanup.__context__, ValueError) and str(cleanup.__context__) == "transcript_unusable"


def processor(tmp_path, case):
    script = tmp_path / "asr_text_worker.py"
    script.write_text(
        dedent("""\
            import sys, types
            from omni.adapters.workers import media
            requests = closed = 0
            case = sys.argv[1]
            def segments(bad):
                global closed
                try:
                    if bad:
                        yield types.SimpleNamespace(start=0,end=1,text='prefix')
                        if case != 'language':
                            yield types.SimpleNamespace(start=1,end=2,text=123 if case == 'number' else '\\ud800')
                    else:
                        yield types.SimpleNamespace(start=0,end=1,text=f'closed={closed}')
                finally:
                    closed += 1
            class Model:
                def __init__(self, name, **options):
                    global requests
                    assert options['local_files_only'] is True
                    requests += 1
                    self.bad = requests == 1
                def transcribe(self, samples, **kwargs):
                    return segments(self.bad), types.SimpleNamespace(language=False if self.bad and case == 'language' else 'zh')
            module = types.ModuleType('faster_whisper')
            module.WhisperModel = Model
            sys.modules['faster_whisper'] = module
            media._decode_audio_16k_mono = lambda path: object()
            raise SystemExit(media.main())
        """),
        encoding="utf-8",
    )
    return MediaProcessor([sys.executable, str(script), case])


@pytest.mark.parametrize("case", ["number", "unicode", "language"])
def test_real_stdio_database_rejects_unusable_text_and_explicit_new_job_reuses_healthy_worker(
    tmp_path, monkeypatch, case
):
    from test_v2_media import BV, _media_instance, _seed, _settle

    async def scenario():
        instance = await _media_instance(tmp_path / "instance", processor=processor(tmp_path, case))
        published, publish = [], instance.media._objects.publish

        async def observe(*args, **kwargs):
            published.append(args)
            return await publish(*args, **kwargs)

        monkeypatch.setattr(instance.media._objects, "publish", observe)
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            bad = await instance.media.submit(agent, str(source["id"]), "transcribe", {})
            row = await _settle(instance, agent, bad["id"])
            await asyncio.gather(*tuple(instance.media._tasks))
            assert row["state"] == "failed" and row["error_code"] == "media_transcript_unusable"
            assert not published and not (await instance.media.source(agent, str(source["id"])))["latest_transcript_id"]
            assert not (instance.media._staging / str(bad["id"])).exists()
            owner = instance.media._processor._owned
            assert owner is not None and not owner.reclaimed
            fresh = await instance.media.submit(agent, str(source["id"]), "transcribe", {})
            assert (await _settle(instance, agent, fresh["id"]))["state"] == "done"
            assert instance.media._processor._owned is owner and len(published) == 1
            latest = (await instance.media.source(agent, str(source["id"])))["latest_transcript_id"]
            parsed = await instance.media.transcript(agent, str(latest))
            assert parsed["segments"] == [[0.0, 1.0, "closed=1"]]
            assert (await instance.media.job(agent, str(bad["id"])))["error_code"] == "media_transcript_unusable"
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())
