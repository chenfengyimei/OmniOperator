"""Decoded ASR input has a sample budget, complete resampler tails and owned cleanup."""

from __future__ import annotations

from omni_test_support.v2.media_audio import (
    MAX_DURATION_S as MAX_DURATION_S,
)
from omni_test_support.v2.media_audio import (
    Path as Path,
)
from omni_test_support.v2.media_audio import (
    SimpleNamespace as SimpleNamespace,
)
from omni_test_support.v2.media_audio import (
    UnreadableOverBudgetFrame as UnreadableOverBudgetFrame,
)
from omni_test_support.v2.media_audio import (
    asyncio as asyncio,
)
from omni_test_support.v2.media_audio import (
    av as av,
)
from omni_test_support.v2.media_audio import (
    contextmanager as contextmanager,
)
from omni_test_support.v2.media_audio import (
    controlled_decode as controlled_decode,
)
from omni_test_support.v2.media_audio import (
    dedent as dedent,
)
from omni_test_support.v2.media_audio import (
    np as np,
)
from omni_test_support.v2.media_audio import (
    pcm_frame as pcm_frame,
)
from omni_test_support.v2.media_audio import (
    pytest as pytest,
)
from omni_test_support.v2.media_audio import (
    sys as sys,
)
from omni_test_support.v2.media_audio import (
    wave as wave,
)
from omni_test_support.v2.media_audio import (
    worker as worker,
)
from omni_test_support.v2.media_audio import (
    write_audio as write_audio,
)


def test_audio_sample_budget_matches_the_existing_media_duration_contract():
    assert getattr(worker, "MAX_AUDIO_SAMPLES", None) == MAX_DURATION_S * 16000


@pytest.mark.parametrize("rate", [8000, 16000, 44100, 48000])
@pytest.mark.parametrize("channels", [1, 2])
def test_real_decode_preserves_all_samples_at_the_exact_budget(tmp_path, monkeypatch, rate, channels):
    monkeypatch.setattr(worker, "MAX_AUDIO_SAMPLES", 16000, raising=False)
    audio = write_audio(tmp_path / "owned.wav", rate, channels)
    samples = worker._decode_audio_16k_mono(audio)
    assert samples.shape == (16000,) and samples.dtype == np.float32
    assert samples.flags.owndata and np.isfinite(samples).all()
    assert np.all(samples[-16:] > 0.1) and np.max(np.abs(samples)) <= 1.0


@pytest.mark.parametrize("rate", [8000, 16000, 44100, 48000])
def test_real_decode_rejects_the_first_sample_over_budget_including_the_tail(tmp_path, monkeypatch, rate):
    monkeypatch.setattr(worker, "MAX_AUDIO_SAMPLES", 15999, raising=False)
    audio = write_audio(tmp_path / "owned.wav", rate)
    with pytest.raises(ValueError, match="^audio_duration_out_of_range$"):
        worker._decode_audio_16k_mono(audio)


@pytest.mark.parametrize("stage", ["first", "later", "tail", "later_tail"])
def test_over_budget_stops_before_reading_the_plane_or_any_later_frame(controlled_decode, monkeypatch, stage):
    monkeypatch.setattr(worker, "MAX_AUDIO_SAMPLES", 4, raising=False)
    safe = pcm_frame([1, 2, 3, 4])
    over = UnreadableOverBudgetFrame(5 if stage == "first" else 1)
    sentinel = UnreadableOverBudgetFrame(0)
    batches = [[over, sentinel]] if stage == "first" else [[safe], [over, sentinel]] if stage == "later" else [[safe]]
    tail = (
        [over, sentinel] if stage == "tail" else [None, pcm_frame([]), over, sentinel] if stage == "later_tail" else []
    )
    fixture = (
        controlled_decode(batches + [[sentinel]], tail=tail)
        if stage in {"first", "later"}
        else controlled_decode(batches, tail=tail)
    )
    with pytest.raises(ValueError, match="^audio_duration_out_of_range$"):
        worker._decode_audio_16k_mono(Path("owned.wav"))
    assert fixture.events[-1] == ("close",)
    assert sum(event[0] == "decode" for event in fixture.events) == (2 if stage == "later" else 1)
    assert sum(event[0] == "flush" for event in fixture.events) == (stage in {"tail", "later_tail"})


@pytest.mark.parametrize("stage", ["decode", "resample", "flush"])
def test_audio_failure_keeps_the_original_exception_and_closes_the_container(controlled_decode, stage):
    fixture = controlled_decode([[pcm_frame([123])]], failure=stage)
    with pytest.raises(OSError) as caught:
        worker._decode_audio_16k_mono(Path("owned.wav"))
    assert caught.value is fixture.primary and fixture.events[-1] == ("close",)
    assert sum(event[0] == "flush" for event in fixture.events) == (stage == "flush")


def test_decode_excludes_alignment_padding_and_normalizes_the_full_s16_range(controlled_decode):
    controlled_decode(
        [[pcm_frame([-32768, -1], padding=b"\xff" * 128)]],
        tail=[pcm_frame([0, 1, 32767], padding=b"\xff" * 128)],
    )
    samples = worker._decode_audio_16k_mono(Path("owned.wav"))
    np.testing.assert_array_equal(samples, np.asarray([-1, -1 / 32768, 0, 1 / 32768, 32767 / 32768], dtype=np.float32))
    assert samples.flags.owndata


@pytest.mark.parametrize("missing", [False, True])
def test_missing_and_empty_audio_remain_precise_and_close_the_container(controlled_decode, missing):
    fixture = controlled_decode([], missing=missing)
    with pytest.raises(ValueError, match="^no_audio_track$" if missing else "^empty_audio$"):
        worker._decode_audio_16k_mono(Path("owned.wav"))
    assert fixture.events[-1] == ("close",)
    assert sum(event[0] == "flush" for event in fixture.events) == (not missing)


@pytest.mark.parametrize("rate", [16000, 44100])
def test_real_processor_reports_audio_limit_without_asr_or_partial_output_and_reuses_worker(tmp_path, rate):
    from omni.adapters.media import MediaProcessor
    from omni.domain.contracts import BusinessError
    from omni.domain.media import asr_plan, parse_transcript

    bad_audio = write_audio(tmp_path / "too-long.wav", rate, seconds=2)
    good_audio = write_audio(tmp_path / "exact.wav", rate)
    script = tmp_path / "owned_audio_worker.py"
    script.write_text(
        dedent("""\
            import sys
            import types
            from omni.adapters.workers import media

            media.MAX_AUDIO_SAMPLES = 16000
            calls = 0

            class Model:
                def __init__(self, name, **options):
                    assert options["local_files_only"] is True

                def transcribe(self, samples, *, vad_filter, beam_size):
                    global calls
                    calls += 1
                    assert len(samples) == 16000 and str(samples.dtype) == "float32"
                    assert vad_filter and beam_size == 5
                    return iter([types.SimpleNamespace(start=0, end=1, text=f"calls={calls}")]), types.SimpleNamespace(language="zh")

            module = types.ModuleType("faster_whisper")
            module.WhisperModel = Model
            sys.modules["faster_whisper"] = module
            raise SystemExit(media.main())
            """),
        encoding="utf-8",
    )

    async def scenario():
        processor = MediaProcessor([sys.executable, str(script)])
        bad, good = tmp_path / "bad" / "partial.json", tmp_path / "good.json"
        plan = asr_plan("fixture-model", "cpu", "int8")
        try:
            with pytest.raises(BusinessError) as caught:
                await processor.transcribe(bad_audio, bad, plan)
            assert caught.value.code == "media_audio_duration_out_of_range"
            assert not bad.exists() and not bad.parent.exists()
            assert processor._owned is not None
            pid = processor._owned.process.pid
            result = await processor.transcribe(good_audio, good, plan)
            assert result["segments"] == 1 and result["duration_s"] == 1
            assert parse_transcript(good.read_bytes())["segments"] == [[0.0, 1.0, "calls=1"]]
            assert processor._owned.process.pid == pid and processor.status()["running"]
        finally:
            assert await processor.close(10)
        assert not processor.status()["running"] and not processor.status()["cleanup_pending"]

    asyncio.run(scenario())


def test_audio_limit_is_a_durable_failed_job_without_artifact_and_allows_the_next_job(tmp_path):
    from omni_test_support.v2.media import BV, FakeProcessor, _media_instance, _seed, _settle

    from omni.domain.contracts import BusinessError

    async def scenario():
        processor = FakeProcessor()
        instance = await _media_instance(tmp_path, processor=processor)
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            processor.failure = BusinessError("media_audio_duration_out_of_range", "audio_duration_out_of_range")
            submitted = await instance.media.submit(agent, str(source["id"]), "transcribe", {})
            failed = await _settle(instance, agent, submitted["id"], wait_for_cleanup=True)
            assert failed["state"] == "failed" and failed["error_code"] == "media_audio_duration_out_of_range"
            view = await instance.media.source(agent, str(source["id"]))
            assert not view["latest_transcript_id"]
            assert not (instance.paths.control / "media-staging" / str(submitted["id"])).exists()
            processor.failure = None
            recovered = await _settle(
                instance, agent, (await instance.media.submit(agent, str(source["id"]), "transcribe", {}))["id"]
            )
            assert recovered["state"] == "done"
            assert (await instance.media.job(agent, str(failed["id"])))["state"] == "failed"
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())
