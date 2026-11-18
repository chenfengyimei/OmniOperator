"""Reusable media_audio fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import sys
import wave
from contextlib import contextmanager
from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace

import av
import numpy as np
import pytest

from omni.adapters.workers import media as worker
from omni.domain.media import MAX_DURATION_S


def write_audio(path, rate, channels=1, seconds=1):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(np.full(rate * channels * seconds, 12000, dtype="<i2").tobytes())
    return path


@pytest.fixture
def controlled_decode(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "owned.wav").write_bytes(b"injected decoder input")
    events = []

    def configure(batches, *, tail=(), failure=None, missing=False):
        class Container:
            streams = SimpleNamespace(audio=[] if missing else [object()])

            def decode(self, **options):
                assert options == {"audio": 0}
                for index, batch in enumerate(batches):
                    events.append(("decode", index))
                    if failure == "decode":
                        raise primary
                    yield batch
                events.append(("exhausted",))

        @contextmanager
        def open_audio(path, *, mode, options):
            assert mode == "r" and options == {"protocol_whitelist": ""} and not path.closed
            events.append(("open",))
            try:
                yield Container()
            finally:
                events.append(("close",))

        class Resampler:
            def __init__(self, **options):
                assert options == {"format": "s16", "layout": "mono", "rate": 16000}

            def resample(self, frame):
                events.append(("flush" if frame is None else "resample",))
                if failure == ("flush" if frame is None else "resample"):
                    raise primary
                return list(tail) if frame is None else frame

        primary = OSError("injected owned audio failure")
        monkeypatch.setattr(av, "open", open_audio)
        monkeypatch.setattr(av, "AudioResampler", Resampler)
        return SimpleNamespace(events=events, primary=primary)

    return configure


def pcm_frame(values, *, padding=b""):
    return SimpleNamespace(samples=len(values), planes=[np.asarray(values, dtype="<i2").tobytes() + padding])


class UnreadableOverBudgetFrame:
    def __init__(self, samples):
        self.samples = samples

    @property
    def planes(self):
        raise AssertionError("the over-budget plane must not be copied or read")


__all__ = [
    "MAX_DURATION_S",
    "Path",
    "SimpleNamespace",
    "UnreadableOverBudgetFrame",
    "asyncio",
    "av",
    "contextmanager",
    "controlled_decode",
    "dedent",
    "np",
    "pcm_frame",
    "pytest",
    "sys",
    "wave",
    "worker",
    "write_audio",
]
