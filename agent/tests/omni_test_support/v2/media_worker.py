"""Reusable media_worker fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import sys
from textwrap import dedent
from types import ModuleType, SimpleNamespace

import pytest

from omni.adapters.workers import media as worker
from omni.domain.media import MAX_SEGMENTS, MAX_TRANSCRIPT_CHARS, parse_transcript


@pytest.fixture
def asr_worker(tmp_path, monkeypatch):
    audio = object()
    calls, iterators, constructors = [], [], []
    output = tmp_path / "artifacts" / "transcript.json"
    request = {
        "audio": str(tmp_path / "owned.wav"),
        "output": str(output),
        "model": "fixture-model",
        "device": "cpu",
        "compute_type": "int8",
    }

    def configure(segments):
        class Model:
            def __init__(self, name, **options):
                constructors.append((name, options))

            def transcribe(self, samples, *, vad_filter, beam_size):
                assert samples is audio and beam_size == 5
                calls.append(vad_filter)
                iterator = segments(vad_filter)
                iterators.append(iterator)
                return iterator, SimpleNamespace(language="zh")

        module = ModuleType("faster_whisper")
        module.WhisperModel = Model
        monkeypatch.setitem(sys.modules, "faster_whisper", module)
        monkeypatch.setattr(worker, "_decode_audio_16k_mono", lambda path: audio)
        return SimpleNamespace(request=request, output=output, calls=calls, iterators=iterators)

    try:
        yield configure
    finally:
        for iterator in iterators:
            iterator.close()
        if constructors:
            assert constructors == [
                (
                    "fixture-model",
                    {"device": "cpu", "compute_type": "int8", "download_root": None, "local_files_only": True},
                )
            ]


__all__ = [
    "MAX_SEGMENTS",
    "MAX_TRANSCRIPT_CHARS",
    "ModuleType",
    "SimpleNamespace",
    "asr_worker",
    "asyncio",
    "dedent",
    "parse_transcript",
    "pytest",
    "sys",
    "worker",
]
