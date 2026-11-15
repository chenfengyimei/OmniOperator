"""Synthetic media fixtures release native output containers on every Python exit."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from test_storyboard import _make_video


@pytest.fixture(params=["storyboard", "bilibili_frames", "bilibili_tools", "aac", "long_video"])
def native_output(tmp_path, monkeypatch, request):
    import av

    original_open = av.open
    original_frame = av.VideoFrame
    original_audio_frame = av.AudioFrame
    outputs = []
    primary = MemoryError("injected video fixture failure")
    cleanup = OSError("injected container close failure")
    fault = SimpleNamespace(stage=None, close=False)

    class Stream:
        def __init__(self, native):
            object.__setattr__(self, "native", native)

        def __setattr__(self, name, value):
            setattr(self.native, name, value)

        def encode(self, *args):
            packets = list(self.native.encode(*args))
            if fault.stage == ("encode" if args else "drain"):
                raise primary
            return packets

    class Output:
        def __init__(self, native):
            self.native, self.close_calls = native, 0

        def add_stream(self, *args, **kwargs):
            if fault.stage == "stream":
                raise primary
            return Stream(self.native.add_stream(*args, **kwargs))

        def mux(self, packet):
            self.native.mux(packet)
            if fault.stage == "mux":
                raise primary

        def close(self):
            self.close_calls += 1
            self.native.close()
            if fault.close:
                raise cleanup

    def owned_open(*args, **kwargs):
        if kwargs.get("mode") != "w":
            return original_open(*args, **kwargs)
        output = Output(original_open(*args, **kwargs))
        outputs.append(output)
        return output

    def frame(image):
        if fault.stage == "frame":
            raise primary
        return original_frame.from_image(image)

    def audio_frame(*args, **kwargs):
        if fault.stage == "frame":
            raise primary
        return original_audio_frame.from_ndarray(*args, **kwargs)

    case = request.param
    path = tmp_path / ("synthetic.m4a" if case == "aac" else "long.mp4" if case == "long_video" else "synthetic.mp4")

    def make():
        if case == "storyboard":
            return _make_video(path, [(1.0, (120, 60, 30))], fps=1, size=(32, 32))
        if case == "bilibili_frames":
            from test_bilibili_frames import _make_video as make_frames

            return make_frames(path, seconds=1, fps=1, size=(32, 32))
        if case == "bilibili_tools":
            from test_bilibili_tools import VideoAPI

            return VideoAPI().download_binary("https://fixture.test/owned", path)
        if case == "aac":
            from test_video_realworld import _make_aac_audio

            return _make_aac_audio(path, seconds=0.03)
        from test_video_realworld import test_scan_cut_scores_covers_whole_long_video

        return test_scan_cut_scores_covers_whole_long_video(tmp_path)

    monkeypatch.setattr(av, "open", owned_open)
    monkeypatch.setattr(av, "VideoFrame", SimpleNamespace(from_image=frame))
    monkeypatch.setattr(av, "AudioFrame", SimpleNamespace(from_ndarray=audio_frame))
    try:
        yield SimpleNamespace(
            path=path,
            outputs=outputs,
            fault=fault,
            primary=primary,
            cleanup=cleanup,
            open=original_open,
            make=make,
            case=case,
        )
    finally:
        # The regression itself must not leak an encoder when the old helper fails.
        for output in outputs:
            output.native.close()


@pytest.mark.parametrize("stage", ["stream", "frame", "encode", "mux", "drain"])
def test_synthetic_video_failure_closes_owned_container_once_and_keeps_original_error(native_output, stage):
    fixture = native_output
    fixture.fault.stage = stage
    with pytest.raises(MemoryError) as caught:
        fixture.make()
    assert caught.value is fixture.primary
    assert len(fixture.outputs) == 1 and fixture.outputs[0].close_calls == 1


def test_synthetic_video_success_closes_once_and_keeps_real_decodable_frame(native_output):
    fixture = native_output
    expected = None if fixture.case in {"bilibili_tools", "long_video"} else fixture.path
    assert fixture.make() == expected
    assert len(fixture.outputs) == 1 and fixture.outputs[0].close_calls == 1
    with fixture.open(str(fixture.path)) as container:
        if fixture.case == "aac":
            frames = list(container.decode(audio=0))
            assert frames and sum(frame.samples for frame in frames) > 0
        elif fixture.case == "long_video":
            frame = next(container.decode(video=0))
            assert (frame.width, frame.height) == (160, 120)
        else:
            frames = list(container.decode(video=0))
            assert len(frames) == (10 if fixture.case == "bilibili_tools" else 1)
            assert (frames[0].width, frames[0].height) == ((160, 120) if fixture.case == "bilibili_tools" else (32, 32))


@pytest.mark.parametrize("body_error", [False, True])
def test_container_close_failure_is_visible_and_retains_body_error_context(native_output, body_error):
    fixture = native_output
    fixture.fault.stage = "mux" if body_error else None
    fixture.fault.close = True
    with pytest.raises(OSError) as caught:
        fixture.make()
    assert caught.value is fixture.cleanup
    assert fixture.outputs[0].close_calls == 1
    assert caught.value.__context__ is (fixture.primary if body_error else None)
