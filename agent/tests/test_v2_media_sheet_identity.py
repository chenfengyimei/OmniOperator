"""Frame artifacts bind worker dimensions and PNG format to independently verified image metadata."""

from __future__ import annotations

import asyncio
import hashlib
import io

import pytest
from test_v2_media_output import Objects, Output

from omni.application.media import MediaService
from omni.domain.contracts import BusinessError


def fixture(tmp_path):
    content = b"isolated image port fixture"
    prepared = {"state": "registered_not_attached", "width": 320, "height": 180, "media_type": "image/png"}

    class Images:
        def __init__(self):
            self.calls = []

        async def prepare(self, agent_id, object_id):
            self.calls.append((agent_id, object_id))
            return dict(prepared)

    objects, images = Objects(), Images()
    service = MediaService(None, objects, images, None, None, None, tmp_path)
    result = {
        "frame_count": 1,
        "width": 320,
        "height": 180,
        "timestamps": [1.0],
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }
    return service, objects, images, prepared, result, Output(content)


@pytest.mark.parametrize("case", ["width", "height", "format", "missing_width", "missing_height", "missing_format"])
def test_sheet_metadata_mismatch_cannot_become_a_media_artifact(tmp_path, case):
    async def scenario():
        service, objects, images, prepared, result, output = fixture(tmp_path)
        if case in {"width", "height"}:
            prepared[case] += 1
        elif case == "format":
            prepared["media_type"] = "image/jpeg"
        else:
            prepared.pop({"missing_width": "width", "missing_height": "height", "missing_format": "media_type"}[case])
        with pytest.raises(BusinessError) as caught:
            await service._register_sheet("agent", output, result)
        assert caught.value.code == "media_sheet_invalid" and caught.value.status == 503
        # Image verification follows object publication; these objects retain normal GC semantics.
        assert len(objects.calls) == 1 and images.calls == [("agent", "object")]
        assert output.reads == [result["bytes"] + 1] and output.closed == [True]

    asyncio.run(scenario())


def test_matching_metadata_keeps_exact_artifact_fields_and_authorization_order(tmp_path):
    async def scenario():
        service, objects, images, _, result, output = fixture(tmp_path)
        stages = []

        async def authorized():
            stages.append((len(objects.calls), len(images.calls)))

        row = await service._register_sheet("agent", output, result, before_publish=authorized)
        assert row == {**result, "object_id": "object", "image_state": "registered_not_attached"}
        assert stages == [(0, 0), (1, 0)]
        assert output.closed == [True]

    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["width", "height", "format"])
def test_real_image_verification_blocks_false_frame_metadata_and_new_job_can_succeed(tmp_path, monkeypatch, case):
    from PIL import Image
    from test_v2_media import BV, FakeProcessor, _media_instance, _seed, _settle

    class Processor(FakeProcessor):
        wrong = True

        async def frames(self, video, sheet_output, count, width):
            result = await super().frames(video, sheet_output, count, width)
            if self.wrong and case in {"width", "height"}:
                result[case] += 1
            elif self.wrong:
                with io.BytesIO() as buffer, Image.new("RGB", (result["width"], result["height"]), "white") as image:
                    image.save(buffer, format="JPEG")
                    content = buffer.getvalue()
                sheet_output.write_bytes(content)
                result.update(bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
            return result

    async def scenario():
        processor = Processor()
        instance = await _media_instance(tmp_path, processor=processor)
        inspected, prepare = [], instance.media._images.prepare

        async def observe(*args, **kwargs):
            row = await prepare(*args, **kwargs)
            inspected.append(row)
            return row

        monkeypatch.setattr(instance.media._images, "prepare", observe)
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            bad = await instance.media.submit(agent, str(source["id"]), "frames", {})
            failed = await _settle(instance, agent, bad["id"])
            await asyncio.gather(*tuple(instance.media._tasks))
            assert failed["state"] == "failed" and failed["error_code"] == "media_sheet_invalid"
            assert not (await instance.media.source(agent, str(source["id"])))["latest_frames_id"]
            assert not (instance.media._staging / str(bad["id"])).exists()
            assert len(inspected) == 1 and inspected[0]["width"] == 640 and inspected[0]["height"] == 360
            assert inspected[0]["media_type"] == ("image/jpeg" if case == "format" else "image/png")
            processor.wrong = False
            fresh = await instance.media.submit(agent, str(source["id"]), "frames", {})
            assert (await _settle(instance, agent, fresh["id"]))["state"] == "done"
            assert (await instance.media.job(agent, str(bad["id"])))["state"] == "failed"
            frame_id = (await instance.media.source(agent, str(source["id"])))["latest_frames_id"]
            frame = await instance.media.frames(agent, str(frame_id))
            assert frame["width"] == inspected[-1]["width"] == 640
            assert frame["height"] == inspected[-1]["height"] == 360
            assert inspected[-1]["media_type"] == "image/png"
            assert len((await instance.media.jobs(agent, str(source["id"])))["items"]) == 2
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())
