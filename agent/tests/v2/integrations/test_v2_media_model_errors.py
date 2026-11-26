"""Offline ASR model errors traverse bounded current causes without changing business failures."""

from __future__ import annotations

import asyncio
import json
import sys
from textwrap import dedent

import pytest

from omni.adapters.workers import media as worker
from omni.domain.contracts import BusinessError
from omni.domain.media import asr_plan, parse_transcript


class LocalEntryNotFoundError(FileNotFoundError):
    pass


def wrap(error, depth, link="cause", outer=RuntimeError):
    for index in range(depth):
        current = outer("model loader failed") if index == depth - 1 else RuntimeError("inner loader failed")
        setattr(current, "__" + link + "__", error)
        error = current
    return error


@pytest.mark.parametrize("link", ["cause", "context"])
@pytest.mark.parametrize("depth,expected", [(0, True), (1, True), (2, True), (5, True), (6, False)])
def test_missing_model_class_is_found_only_within_six_total_exception_nodes(link, depth, expected):
    error = wrap(LocalEntryNotFoundError("fixture offline cache entry absent"), depth, link)
    assert worker._asr_model_missing(error) is expected


@pytest.mark.parametrize("link", ["cause", "context"])
@pytest.mark.parametrize("message", ["local_files_only", "not a local folder", "Cannot find the requested files"])
def test_nested_existing_message_hints_are_still_recognized(link, message):
    assert worker._asr_model_missing(wrap(OSError(message), 2, link))


@pytest.mark.parametrize("length", [1, 2])
def test_exception_cycles_visit_each_node_once(length):
    visits = []

    class Observed(RuntimeError):
        def __str__(self):
            visits.append(self)
            return "unrelated failure"

    nodes = [Observed() for _ in range(length)]
    for index, error in enumerate(nodes):
        error.__cause__ = nodes[(index + 1) % length]
    assert not worker._asr_model_missing(nodes[0])
    assert visits == nodes


def test_deep_negative_chain_reads_only_the_first_six_distinct_nodes():
    visits = []

    class Observed(RuntimeError):
        def __str__(self):
            visits.append(self)
            return "unrelated failure"

    nodes = [Observed() for _ in range(8)]
    for previous, current in zip(nodes, nodes[1:], strict=False):
        previous.__context__ = current
    assert not worker._asr_model_missing(nodes[0])
    assert visits == nodes[:6]


@pytest.mark.parametrize("falsey", [False, True])
def test_explicit_cause_takes_precedence_over_a_missing_model_context(falsey):
    class Cause(RuntimeError):
        def __bool__(self):
            return not falsey

    error = RuntimeError("top failure")
    error.__cause__, error.__context__ = Cause("unrelated cause"), LocalEntryNotFoundError("hidden cache failure")
    assert not worker._asr_model_missing(error)


def handle_error(monkeypatch, capsys, error, operation="transcribe"):
    def fail(*args):
        raise error

    monkeypatch.setattr(worker, {"transcribe": "_transcribe", "frames": "_frames", "probe": "_probe"}[operation], fail)
    identity = "12345678-1234-1234-1234-123456789abc"
    worker._handle(json.dumps({"version": 1, "id": identity, "operation": operation}))
    reply = json.loads(capsys.readouterr().out)
    assert reply["id"] == identity and reply["result"]["ok"] is False
    return reply["result"]["code"]


@pytest.mark.parametrize("outer", [RuntimeError, ValueError])
@pytest.mark.parametrize("link", ["cause", "context"])
def test_worker_handles_nested_loader_wrappers_as_model_missing(monkeypatch, capsys, outer, link):
    error = wrap(LocalEntryNotFoundError("fixture cache failure"), 2, link, outer)
    assert handle_error(monkeypatch, capsys, error) == "media_asr_model_missing"


@pytest.mark.parametrize(
    "message,expected",
    [
        ("no_audio_track", "media_audio_missing"),
        ("transcript_unusable", "media_transcript_unusable"),
        ("frame_timestamp_unusable", "media_frame_timestamp_unusable"),
    ],
)
def test_known_business_error_keeps_priority_over_a_model_missing_cause(monkeypatch, capsys, message, expected):
    error = ValueError(message)
    error.__cause__ = LocalEntryNotFoundError("old missing cache context")
    assert handle_error(monkeypatch, capsys, error) == expected


def test_missing_component_keeps_priority_over_model_missing_context(monkeypatch, capsys):
    error = ImportError("fixture component unavailable")
    error.__context__ = LocalEntryNotFoundError("old missing cache context")
    assert handle_error(monkeypatch, capsys, error) == "media_component_missing"


@pytest.mark.parametrize("outer", [ValueError, RuntimeError])
def test_unrelated_nested_errors_remain_generic_processing_failure(monkeypatch, capsys, outer):
    assert (
        handle_error(monkeypatch, capsys, wrap(OSError("unrelated input failure"), 2, outer=outer))
        == "media_processing_error"
    )


@pytest.mark.parametrize("operation", ["frames", "probe"])
def test_non_asr_value_error_does_not_gain_model_missing_classification(monkeypatch, capsys, operation):
    error = wrap(LocalEntryNotFoundError("irrelevant cache cause"), 2, outer=ValueError)
    assert handle_error(monkeypatch, capsys, error, operation) == "media_processing_error"


def model_processor(tmp_path, outer):
    from omni.adapters.media import MediaProcessor

    script = tmp_path / "nested_model_error_worker.py"
    script.write_text(
        dedent("""\
        import sys
        import types
        from omni.adapters.workers import media

        requests = 0
        class LocalEntryNotFoundError(FileNotFoundError):
            pass

        class Model:
            def __init__(self, name, **options):
                global requests
                assert options['local_files_only'] is True
                requests += 1
                if requests == 1:
                    missing = LocalEntryNotFoundError('fixture cache unavailable')
                    inner = RuntimeError('inner loader failed')
                    inner.__cause__ = missing
                    error = (ValueError if sys.argv[1] == 'value' else RuntimeError)('model loader failed')
                    error.__cause__ = inner
                    raise error
            def transcribe(self, samples, **kwargs):
                return iter([types.SimpleNamespace(start=0, end=1, text='available fixture')]), types.SimpleNamespace(language='zh')

        module = types.ModuleType('faster_whisper')
        module.WhisperModel = Model
        sys.modules['faster_whisper'] = module
        media._decode_audio_16k_mono = lambda path: object()
        raise SystemExit(media.main())
        """),
        encoding="utf-8",
    )
    return MediaProcessor([sys.executable, str(script), outer])


@pytest.mark.parametrize("outer", ["value", "runtime"])
def test_real_stdio_preserves_model_missing_guidance_and_reuses_worker_for_new_request(tmp_path, outer):
    async def scenario():
        processor = model_processor(tmp_path, outer)
        bad, good = tmp_path / "bad" / "transcript.json", tmp_path / "good.json"
        plan = asr_plan("fixture-model", "cpu", "int8")
        try:
            with pytest.raises(BusinessError) as caught:
                await processor.transcribe(tmp_path / "owned.wav", bad, plan)
            assert caught.value.code == "media_asr_model_missing" and "offline cache" in caught.value.message
            assert not bad.exists() and not bad.parent.exists()
            owner = processor._owned
            assert owner is not None
            assert (await processor.transcribe(tmp_path / "owned.wav", good, plan))["segments"] == 1
            assert processor._owned is owner
            assert parse_transcript(good.read_bytes())["segments"] == [[0.0, 1.0, "available fixture"]]
        finally:
            assert await processor.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("outer", ["value", "runtime"])
def test_real_job_records_model_missing_and_does_not_publish_until_explicit_new_work(tmp_path, outer):
    from omni_test_support.v2.media import BV, _media_instance, _seed, _settle

    async def scenario():
        instance = await _media_instance(tmp_path / "instance", processor=model_processor(tmp_path, outer))
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            bad = await instance.media.submit(agent, str(source["id"]), "transcribe", {})
            row = await _settle(instance, agent, bad["id"], wait_for_cleanup=True)
            assert row["state"] == "failed" and row["error_code"] == "media_asr_model_missing"
            assert not (await instance.media.source(agent, str(source["id"])))["latest_transcript_id"]
            assert not (instance.media._staging / str(bad["id"])).exists()
            owner = instance.media._processor._owned
            fresh = await instance.media.submit(agent, str(source["id"]), "transcribe", {})
            assert (await _settle(instance, agent, fresh["id"]))["state"] == "done"
            assert instance.media._processor._owned is owner
            assert (await instance.media.job(agent, str(bad["id"])))["error_code"] == "media_asr_model_missing"
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())
