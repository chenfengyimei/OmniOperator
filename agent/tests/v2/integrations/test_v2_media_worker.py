"""ASR output budgets stop owned iterators before unbounded collection or publication."""

from __future__ import annotations

from omni_test_support.v2.media_worker import (
    MAX_SEGMENTS as MAX_SEGMENTS,
)
from omni_test_support.v2.media_worker import (
    MAX_TRANSCRIPT_CHARS as MAX_TRANSCRIPT_CHARS,
)
from omni_test_support.v2.media_worker import (
    ModuleType as ModuleType,
)
from omni_test_support.v2.media_worker import (
    SimpleNamespace as SimpleNamespace,
)
from omni_test_support.v2.media_worker import (
    asr_worker as asr_worker,
)
from omni_test_support.v2.media_worker import (
    asyncio as asyncio,
)
from omni_test_support.v2.media_worker import (
    dedent as dedent,
)
from omni_test_support.v2.media_worker import (
    parse_transcript as parse_transcript,
)
from omni_test_support.v2.media_worker import (
    pytest as pytest,
)
from omni_test_support.v2.media_worker import (
    sys as sys,
)
from omni_test_support.v2.media_worker import (
    worker as worker,
)


@pytest.mark.parametrize("vad_empty", [False, True])
@pytest.mark.parametrize("budget", ["segments", "text_total", "text_single"])
def test_asr_stops_at_first_over_budget_segment_without_fallback_or_partial_artifact(asr_worker, vad_empty, budget):
    consumed, closed = [], []
    offending = MAX_SEGMENTS + 1 if budget == "segments" else 2 if budget == "text_total" else 1

    def segments(vad):
        try:
            if vad_empty and vad:
                yield SimpleNamespace(text=" \n ")
                return
            for index in range(offending):
                consumed.append(index)
                text = (
                    "字" if budget == "segments" or index else "🙂" * (MAX_TRANSCRIPT_CHARS + (budget == "text_single"))
                )
                yield SimpleNamespace(start=float(index), end=float(index + 1), text=text)
            raise AssertionError("ASR consumed another segment after the output budget was exceeded")
        finally:
            closed.append(vad)

    fixture = asr_worker(segments)
    with pytest.raises(ValueError, match="^transcript_unusable$"):
        worker._transcribe(fixture.request)
    assert len(consumed) == offending
    assert fixture.calls == ([True, False] if vad_empty else [True])
    assert closed == fixture.calls and all(iterator.gi_frame is None for iterator in fixture.iterators)
    assert not fixture.output.exists() and not fixture.output.parent.exists()


@pytest.mark.parametrize("vad_empty", [False, True])
@pytest.mark.parametrize("budget", ["segments", "text_total"])
def test_asr_accepts_exact_budgets_and_keeps_ordered_unicode_timestamps(asr_worker, vad_empty, budget):
    count = MAX_SEGMENTS if budget == "segments" else 2
    text = "字🙂" if budget == "segments" else "字🙂" * (MAX_TRANSCRIPT_CHARS // 4)
    closed = []

    def segments(vad):
        try:
            yield SimpleNamespace(text=" \n ")
            if not vad_empty or not vad:
                for index in range(count):
                    yield SimpleNamespace(start=float(index), end=float(index + 1), text=f"  {text}  ")
        finally:
            closed.append(vad)

    fixture = asr_worker(segments)
    result = worker._transcribe(fixture.request)
    body = parse_transcript(fixture.output.read_bytes())
    assert body == {
        "version": 1,
        "kind": "asr",
        "language": "zh",
        "segments": [[float(i), float(i + 1), text] for i in range(count)],
    }
    assert result == {
        "segments": count,
        "language": "zh",
        "duration_s": float(count),
        "bytes": fixture.output.stat().st_size,
    }
    assert fixture.calls == ([True, False] if vad_empty else [True])
    assert closed == fixture.calls and all(iterator.gi_frame is None for iterator in fixture.iterators)


def test_asr_empty_vad_and_fallback_remain_unusable_without_publishing(asr_worker):
    closed = []

    def segments(vad):
        try:
            yield SimpleNamespace(text=" \n ")
        finally:
            closed.append(vad)

    fixture = asr_worker(segments)
    with pytest.raises(ValueError, match="^transcript_unusable$"):
        worker._transcribe(fixture.request)
    assert fixture.calls == closed == [True, False]
    assert not fixture.output.exists() and not fixture.output.parent.exists()


@pytest.mark.parametrize("vad_empty", [False, True])
@pytest.mark.parametrize("failure", ["inference", "timestamp"])
def test_asr_failure_closes_owned_generator_and_keeps_original_error(asr_worker, vad_empty, failure):
    primary = ValueError("injected ASR segment failure")
    closed = []

    class BadTime:
        def __float__(self):
            raise primary

    def segments(vad):
        try:
            if vad_empty and vad:
                yield SimpleNamespace(text=" ")
                return
            yield SimpleNamespace(start=0, end=1, text="first")
            if failure == "inference":
                raise primary
            yield SimpleNamespace(start=BadTime(), end=2, text="second")
            raise AssertionError("ASR read past a failed timestamp conversion")
        finally:
            closed.append(vad)

    fixture = asr_worker(segments)
    with pytest.raises(ValueError) as caught:
        worker._transcribe(fixture.request)
    assert caught.value is primary
    assert fixture.calls == closed == ([True, False] if vad_empty else [True])
    assert all(iterator.gi_frame is None for iterator in fixture.iterators)
    assert not fixture.output.exists() and not fixture.output.parent.exists()


@pytest.mark.parametrize("over_budget", [False, True])
def test_asr_iterator_close_failure_is_visible_with_original_budget_error_context(over_budget):
    cleanup = OSError("injected ASR generator close failure")

    class Stream:
        close_calls = 0

        def __init__(self):
            self.rows = iter([SimpleNamespace(start=0, end=1, text="x" * (MAX_TRANSCRIPT_CHARS + over_budget))])

        def __iter__(self):
            return self

        def __next__(self):
            return next(self.rows)

        def close(self):
            self.close_calls += 1
            raise cleanup

    stream = Stream()
    with pytest.raises(OSError) as caught:
        worker._transcript_rows(stream)
    assert caught.value is cleanup and stream.close_calls == 1
    if over_budget:
        assert isinstance(cleanup.__context__, ValueError) and str(cleanup.__context__) == "transcript_unusable"
    else:
        assert cleanup.__context__ is None


@pytest.mark.parametrize("vad_empty", [False, True])
def test_media_processor_receives_budget_error_and_reuses_clean_worker_for_next_valid_request(tmp_path, vad_empty):
    from omni.adapters.media import MediaProcessor
    from omni.domain.contracts import BusinessError
    from omni.domain.media import asr_plan

    script = tmp_path / "owned_asr_worker.py"
    script.write_text(
        dedent("""\
            import sys
            import types
            from omni.adapters.workers import media

            fallback = sys.argv[1] == "fallback"
            requests = closed = 0

            def segments(bad, vad):
                global closed
                try:
                    if not bad:
                        yield types.SimpleNamespace(start=0, end=1, text=f"closed={closed}")
                        return
                    if fallback and vad:
                        yield types.SimpleNamespace(text=" ")
                        return
                    yield types.SimpleNamespace(start=0, end=1, text="x" * media.MAX_TRANSCRIPT_CHARS)
                    yield types.SimpleNamespace(start=1, end=2, text="x")
                    raise AssertionError("read past ASR output budget")
                finally:
                    closed += 1

            class Model:
                def __init__(self, name, **options):
                    global requests
                    assert options["local_files_only"] is True
                    requests += 1
                    self.bad = requests == 1

                def transcribe(self, samples, *, vad_filter, beam_size):
                    return segments(self.bad, vad_filter), types.SimpleNamespace(language="zh")

            module = types.ModuleType("faster_whisper")
            module.WhisperModel = Model
            sys.modules["faster_whisper"] = module
            media._decode_audio_16k_mono = lambda path: object()
            raise SystemExit(media.main())
            """),
        encoding="utf-8",
    )

    async def scenario():
        processor = MediaProcessor([sys.executable, str(script), "fallback" if vad_empty else "first"])
        bad, good = tmp_path / "bad.json", tmp_path / "good.json"
        plan = asr_plan("fixture-model", "cpu", "int8")
        try:
            with pytest.raises(BusinessError) as caught:
                await processor.transcribe(tmp_path / "owned.wav", bad, plan)
            assert caught.value.code == "media_transcript_unusable" and not bad.exists()
            assert processor._owned is not None
            pid = processor._owned.process.pid
            result = await processor.transcribe(tmp_path / "owned.wav", good, plan)
            assert result["segments"] == 1 and result["duration_s"] == 1
            assert parse_transcript(good.read_bytes())["segments"] == [[0.0, 1.0, f"closed={2 if vad_empty else 1}"]]
            assert processor._owned.process.pid == pid and processor.status()["running"]
        finally:
            assert await processor.close(10)
        assert not processor.status()["running"] and not processor.status()["cleanup_pending"]

    asyncio.run(scenario())
