"""Frame sheets enforce allocation/encoding budgets and close owned image resources."""

from __future__ import annotations

from omni_test_support.v2.media_frames import (
    MAX_SHEET_BYTES as MAX_SHEET_BYTES,
)
from omni_test_support.v2.media_frames import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.media_frames import (
    ModuleType as ModuleType,
)
from omni_test_support.v2.media_frames import (
    Path as Path,
)
from omni_test_support.v2.media_frames import (
    SimpleNamespace as SimpleNamespace,
)
from omni_test_support.v2.media_frames import (
    asyncio as asyncio,
)
from omni_test_support.v2.media_frames import (
    frames as frames,
)
from omni_test_support.v2.media_frames import (
    hashlib as hashlib,
)
from omni_test_support.v2.media_frames import (
    json as json,
)
from omni_test_support.v2.media_frames import (
    pytest as pytest,
)
from omni_test_support.v2.media_frames import (
    sheet_result as sheet_result,
)
from omni_test_support.v2.media_frames import (
    sys as sys,
)
from omni_test_support.v2.media_frames import (
    worker as worker,
)


@pytest.mark.parametrize("metadata", [False, True])
@pytest.mark.parametrize("resize", [False, True])
def test_success_closes_sources_resizes_canvas_and_buffer_without_changing_output(frames, metadata, resize):
    state = frames.state
    state.metadata, state.source_size = metadata, (640, 360) if resize else (320, 180)
    result = worker._frames(frames.request)
    body = b"".join(state.chunks)
    assert frames.output.read_bytes() == body
    assert result == {
        "frame_count": 3,
        "timestamps": [0.25, 1.25, 2.25],
        "width": 640,
        "height": 400,
        "sha256": hashlib.sha256(body).hexdigest(),
        "bytes": len(body),
    }
    assert len(state.images) == (7 if resize else 4)
    assert all(image.closed == 1 for image in state.images)
    assert state.container_closed == 1 and all(stream.closed for stream in state.buffers)


@pytest.mark.parametrize(
    "phase", ["decode", "convert", "resize", "canvas", "paste", "text", "encode", "mkdir", "write"]
)
def test_failure_closes_every_already_allocated_resource_and_preserves_the_error(frames, phase):
    state = frames.state
    state.phase, state.metadata = phase, False
    with pytest.raises(RuntimeError) as caught:
        worker._frames(frames.request)
    assert caught.value is frames.fault
    assert state.images and all(image.closed == 1 for image in state.images)
    assert state.container_closed == 1 and all(stream.closed for stream in state.buffers)
    assert not frames.output.exists()


def test_cleanup_failure_still_closes_other_images_and_retains_primary_context(frames):
    state = frames.state
    state.phase, state.closes_fail = "paste", True
    with pytest.raises(OSError, match="image close failure") as caught:
        worker._frames(frames.request)
    assert all(image.closed == 1 for image in state.images) and state.container_closed == 1
    contexts, error = [], caught.value
    while error is not None:
        contexts.append(error)
        error = error.__context__
    assert frames.fault in contexts and not frames.output.exists()


@pytest.mark.parametrize(
    "size,count,width,converted",
    [
        ((0, 100), 1, 320, 0),
        ((320, -1), 1, 320, 0),
        ((320, 8173), 1, 320, 0),
        ((320, 5000), 3, 320, 2),
        ((1280, 7000), 2, 1280, 1),
    ],
)
def test_invalid_prospective_sheet_is_rejected_before_the_next_conversion(frames, size, count, width, converted):
    state = frames.state
    state.source_size = size
    frames.request.update(count=count, width=width)
    with pytest.raises(ValueError, match="^sheet_invalid$"):
        worker._frames(frames.request)
    assert state.conversions == converted and not any(image.kind == "canvas" for image in state.images)
    assert all(image.closed == 1 for image in state.images) and state.container_closed == 1
    assert not frames.output.exists() and not frames.output.parent.exists()


@pytest.mark.parametrize("size,count,expected", [((320, 8172), 1, (320, 8192)), ((1280, 6230), 2, (2560, 6250))])
def test_exact_sheet_side_and_pixel_limits_remain_accepted(frames, size, count, expected):
    frames.state.source_size = size
    frames.request.update(count=count, width=size[0])
    result = worker._frames(frames.request)
    assert (result["width"], result["height"]) == expected
    assert all(image.closed == 1 for image in frames.state.images)
    assert sheet_result(*[result[key] for key in ("frame_count", "width", "height", "timestamps", "sha256", "bytes")])


@pytest.mark.parametrize("over", [False, True])
def test_encoder_cannot_retain_bytes_beyond_eight_mib_or_write_an_oversized_file(frames, over):
    state = frames.state
    state.chunks = [b"x" * MAX_SHEET_BYTES, *([b"y"] if over else [])]
    if over:
        with pytest.raises(ValueError, match="^sheet_invalid$"):
            worker._frames(frames.request)
        assert not frames.output.exists()
    else:
        assert worker._frames(frames.request)["bytes"] == MAX_SHEET_BYTES
        assert frames.output.stat().st_size == MAX_SHEET_BYTES
    assert state.retained == MAX_SHEET_BYTES and all(stream.closed for stream in state.buffers)
    assert all(image.closed == 1 for image in state.images)


def test_worker_protocol_maps_generation_budget_to_precise_business_failure(frames, capsys):
    frames.state.source_size = (320, 8173)
    worker._handle(
        json.dumps(
            {"version": 1, "id": "12345678-1234-1234-1234-123456789abc", "operation": "frames", **frames.request}
        )
    )
    reply = json.loads(capsys.readouterr().out)
    assert reply["result"]["ok"] is False and reply["result"]["code"] == "media_sheet_invalid"
    assert not frames.output.exists()


def test_real_stdio_worker_rejects_tall_sheet_then_produces_valid_png_on_same_process(tmp_path):
    from PIL import Image
    from test_bilibili_frames import _make_video

    from omni.adapters.media import MediaProcessor

    tall = _make_video(tmp_path / "tall.mp4", seconds=1, fps=1, size=(32, 1024))
    valid = _make_video(tmp_path / "valid.mp4", seconds=1, fps=2, size=(64, 48))

    async def scenario():
        processor = MediaProcessor()
        output = tmp_path / "sheet.png"
        try:
            with pytest.raises(BusinessError) as caught:
                await processor.frames(tall, output, 1, 320)
            assert caught.value.code == "media_sheet_invalid" and not output.exists()
            owner = processor._owned
            assert owner is not None
            result = await processor.frames(valid, output, 2, 320)
            assert processor._owned is owner and result["frame_count"] == 2
            with Image.open(output) as sheet:
                sheet.load()
                assert sheet.format == "PNG" and sheet.size == (640, 260)
            assert sheet_result(
                *[result[key] for key in ("frame_count", "width", "height", "timestamps", "sha256", "bytes")]
            )
            assert result["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
        finally:
            assert await processor.close()

    asyncio.run(scenario())


def test_real_job_rejects_generation_budget_without_artifact_and_explicit_new_job_succeeds(tmp_path):
    from omni_test_support.v2.media import BV, FakeGateway, _media_instance, _seed, _settle
    from test_bilibili_frames import _make_video

    from omni.adapters.media import MediaProcessor

    tall = _make_video(tmp_path / "tall.mp4", seconds=1, fps=1, size=(32, 1024))
    valid = _make_video(tmp_path / "valid.mp4", seconds=1, fps=1, size=(64, 48))

    class Gateway(FakeGateway):
        video = tall

        async def download(self, configuration, url, destination, **kwargs):
            destination.write_bytes(self.video.read_bytes())
            return {"bytes": destination.stat().st_size}

    async def scenario():
        gateway = Gateway()
        instance = await _media_instance(tmp_path / "instance", gateway, MediaProcessor())
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            bad = await instance.media.submit(agent, str(source["id"]), "frames", {"count": 1, "width": 320})
            row = await _settle(instance, agent, bad["id"])
            assert row["state"] == "failed" and row["error_code"] == "media_sheet_invalid"
            assert not (await instance.media.source(agent, str(source["id"])))["latest_frames_id"]
            assert not (instance.media._staging / str(bad["id"])).exists()
            gateway.video = valid
            fresh = await instance.media.submit(agent, str(source["id"]), "frames", {"count": 1, "width": 320})
            assert (await _settle(instance, agent, fresh["id"]))["state"] == "done"
            assert (await instance.media.job(agent, str(bad["id"])))["state"] == "failed"
            assert not (instance.media._staging / str(fresh["id"])).exists()
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())
