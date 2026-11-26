"""ASR rejects the first unusable segment time without invented anchors or extra inference."""

from __future__ import annotations

import asyncio
import sys
from textwrap import dedent
from types import SimpleNamespace

import pytest
from omni_test_support.v2.media_worker import asr_worker as asr_worker

from omni.adapters.workers import media as worker
from omni.domain.contracts import BusinessError
from omni.domain.media import asr_plan, parse_transcript

INVALID = [
    "missing_start",
    "missing_end",
    "null_start",
    "null_end",
    "boolean_start",
    "boolean_end",
    "text_start",
    "empty_start",
    "bytes_end",
    "nan_start",
    "infinite_end",
    "negative_start",
    "too_long_end",
    "huge_end",
    "reversed",
    "out_of_order",
    "excess_overlap",
]


def bad_segment(case):
    values = {"start": 1, "end": 2, "text": "bad anchor"}
    if case.startswith("missing_"):
        values.pop(case.removeprefix("missing_"))
    else:
        changes = {
            "null_start": {"start": None},
            "null_end": {"end": None},
            "boolean_start": {"start": True},
            "boolean_end": {"end": True},
            "text_start": {"start": "1"},
            "empty_start": {"start": ""},
            "bytes_end": {"end": b"2"},
            "nan_start": {"start": float("nan")},
            "infinite_end": {"end": float("inf")},
            "negative_start": {"start": -0.001},
            "too_long_end": {"end": 7200.001},
            "huge_end": {"end": 10**400},
            "reversed": {"start": 2, "end": 1},
            "out_of_order": {"start": 0, "end": 0.5},
            "excess_overlap": {"start": 0.998, "end": 2},
        }
        values.update(changes[case])
    return SimpleNamespace(**values)


@pytest.mark.parametrize("vad_empty", [False, True])
@pytest.mark.parametrize("case", INVALID)
def test_asr_stops_on_first_bad_segment_closes_iterator_and_never_retries_or_writes(asr_worker, vad_empty, case):
    consumed, closed = [], []

    def segments(vad):
        try:
            if vad_empty and vad:
                yield SimpleNamespace(text=" \n ")
                return
            consumed.append("valid")
            yield SimpleNamespace(start=0, end=1, text="first")
            consumed.append("invalid")
            yield bad_segment(case)
            raise AssertionError("ASR read another segment after an unusable timestamp")
        finally:
            closed.append(vad)

    fixture = asr_worker(segments)
    with pytest.raises(ValueError, match="^transcript_unusable$"):
        worker._transcribe(fixture.request)
    assert consumed == ["valid", "invalid"]
    assert fixture.calls == closed == ([True, False] if vad_empty else [True])
    assert all(iterator.gi_frame is None for iterator in fixture.iterators)
    assert not fixture.output.exists() and not fixture.output.parent.exists()


@pytest.mark.parametrize("vad_empty", [False, True])
def test_asr_preserves_true_zero_exact_duration_tolerated_overlap_and_blank_vad_fallback(asr_worker, vad_empty):
    def segments(vad):
        yield SimpleNamespace(text=" \n ")
        if not vad_empty or not vad:
            yield SimpleNamespace(start=0, end=0, text="zero")
            yield SimpleNamespace(start=0, end=1, text="first")
            yield SimpleNamespace(start=0.999, end=7200, text="最后一段🙂")

    fixture = asr_worker(segments)
    result = worker._transcribe(fixture.request)
    parsed = parse_transcript(fixture.output.read_bytes())
    assert parsed["segments"] == [[0.0, 0.0, "zero"], [0.0, 1.0, "first"], [0.999, 7200.0, "最后一段🙂"]]
    assert result["duration_s"] == 7200 and fixture.calls == ([True, False] if vad_empty else [True])
    assert all(iterator.gi_frame is None for iterator in fixture.iterators)


def test_iterator_close_failure_keeps_unusable_timestamp_as_context():
    cleanup = OSError("injected ASR iterator close failure")

    class Stream:
        consumed = closed = 0

        def __iter__(self):
            return self

        def __next__(self):
            self.consumed += 1
            assert self.consumed == 1, "Read past the missing timestamp"
            return SimpleNamespace(text="missing times")

        def close(self):
            self.closed += 1
            raise cleanup

    stream = Stream()
    with pytest.raises(OSError) as caught:
        worker._transcript_rows(stream)
    assert caught.value is cleanup and stream.closed == stream.consumed == 1
    assert isinstance(cleanup.__context__, ValueError) and str(cleanup.__context__) == "transcript_unusable"


def processor_with_missing_first_times(tmp_path, vad_empty):
    from omni.adapters.media import MediaProcessor

    script = tmp_path / "missing_asr_times_worker.py"
    script.write_text(
        dedent("""\
        import sys
        import types
        from omni.adapters.workers import media

        requests = closed = 0
        fallback = sys.argv[1] == 'fallback'

        def segments(bad, vad):
            global closed
            try:
                if bad and fallback and vad:
                    yield types.SimpleNamespace(text=' ')
                    return
                if bad:
                    yield types.SimpleNamespace(text='missing both timestamps')
                else:
                    yield types.SimpleNamespace(start=0, end=1, text=f'closed={closed}')
            finally:
                closed += 1

        class Model:
            def __init__(self, name, **options):
                global requests
                assert options['local_files_only'] is True
                requests += 1
                self.bad = requests == 1
            def transcribe(self, samples, *, vad_filter, beam_size):
                return segments(self.bad, vad_filter), types.SimpleNamespace(language='zh')

        module = types.ModuleType('faster_whisper')
        module.WhisperModel = Model
        sys.modules['faster_whisper'] = module
        media._decode_audio_16k_mono = lambda path: object()
        raise SystemExit(media.main())
        """),
        encoding="utf-8",
    )
    return MediaProcessor([sys.executable, str(script), "fallback" if vad_empty else "first"])


@pytest.mark.parametrize("vad_empty", [False, True])
def test_real_stdio_missing_times_cannot_become_zero_anchors_and_same_worker_remains_usable(tmp_path, vad_empty):
    async def scenario():
        processor = processor_with_missing_first_times(tmp_path, vad_empty)
        bad, good = tmp_path / "bad" / "transcript.json", tmp_path / "good" / "transcript.json"
        plan = asr_plan("fixture-model", "cpu", "int8")
        try:
            with pytest.raises(BusinessError) as caught:
                await processor.transcribe(tmp_path / "owned.wav", bad, plan)
            assert caught.value.code == "media_transcript_unusable" and not bad.exists() and not bad.parent.exists()
            owner = processor._owned
            assert owner is not None
            result = await processor.transcribe(tmp_path / "owned.wav", good, plan)
            assert processor._owned is owner and result["segments"] == 1
            assert parse_transcript(good.read_bytes())["segments"] == [[0.0, 1.0, f"closed={2 if vad_empty else 1}"]]
        finally:
            assert await processor.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("vad_empty", [False, True])
def test_real_job_missing_times_fails_without_transcript_and_new_explicit_job_succeeds(tmp_path, vad_empty):
    from omni_test_support.v2.media import BV, _media_instance, _seed, _settle

    async def scenario():
        instance = await _media_instance(
            tmp_path / "instance", processor=processor_with_missing_first_times(tmp_path, vad_empty)
        )
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            bad = await instance.media.submit(agent, str(source["id"]), "transcribe", {})
            row = await _settle(instance, agent, bad["id"], wait_for_cleanup=True)
            assert row["state"] == "failed" and row["error_code"] == "media_transcript_unusable"
            assert not (await instance.media.source(agent, str(source["id"])))["latest_transcript_id"]
            assert not (instance.media._staging / str(bad["id"])).exists()
            fresh = await instance.media.submit(agent, str(source["id"]), "transcribe", {})
            assert (await _settle(instance, agent, fresh["id"], wait_for_cleanup=True))["state"] == "done"
            assert (await instance.media.job(agent, str(bad["id"])))["state"] == "failed"
            latest = (await instance.media.source(agent, str(source["id"])))["latest_transcript_id"]
            result = await instance.media.transcript(agent, str(latest))
            assert result["segments"] == [[0.0, 1.0, f"closed={2 if vad_empty else 1}"]]
            assert not (instance.media._staging / str(fresh["id"])).exists()
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())
