"""Uploaded object sources use the real media job graph; no account, download, model or device."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from omni_test_support.v2.media import FakeGateway, FakeProcessor, NoModel, Secrets, _settle

from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError


@asynccontextmanager
async def instance(path, gateway=None, processor=None):
    i = await open_instance(
        path,
        model=NoModel(),
        bilibili_backend=gateway or FakeGateway(),
        media_processor=processor or FakeProcessor(),
        credential_backend=Secrets(),
    )
    try:
        yield i
    finally:
        assert (await i.close())["complete"]


@pytest.mark.parametrize(
    "mime,operation", [("audio/wav", "transcribe"), ("video/mp4", "frames"), ("video/webm", "transcribe")]
)
def test_uploaded_source_runs_without_channel_and_holds_object_until_source_deleted(tmp_path, mime, operation):
    async def scenario():
        gateway, processor = FakeGateway(), FakeProcessor()
        original_transcribe, original_frames = processor.transcribe, processor.frames

        async def transcribe(audio, *args):
            assert audio.read_bytes() == b"isolated-upload-bytes"
            return await original_transcribe(audio, *args)

        async def frames(video, *args):
            assert video.read_bytes() == b"isolated-upload-bytes"
            return await original_frames(video, *args)

        processor.transcribe, processor.frames = transcribe, frames
        async with instance(tmp_path, gateway, processor) as i:
            agent = (await i.application.query("agents", ""))[0]["id"]
            obj = await i.objects.publish(agent, b"isolated-upload-bytes", mime)
            source = await i.media.register_upload(agent, obj["id"], "本地媒体")
            assert source["kind"] == ("object_audio" if mime.startswith("audio/") else "object_video")
            assert (
                source["object_id"] == obj["id"]
                and source["config_id"] is source["bvid"] is source["duration_s"] is None
            )
            assert (await i.objects.describe(agent, obj["id"]))["references"] == 1
            assert (await i.media.register_upload(agent, obj["id"], "本地媒体"))["id"] == source["id"]
            with pytest.raises(BusinessError):
                await i.media.register_upload(agent, obj["id"], "不同标题")
            job = await i.media.submit(agent, source["id"], operation, {})
            result = await _settle(i, agent, job["id"])
            assert result["state"] == "done", result
            assert not gateway.reads and not gateway.downloads and len(processor.calls) == 1
            if operation == "transcribe":
                window = await i.media.transcript_window(agent, source["id"], None, 0, 12000)
                assert "你好世界" in window["text"] and "00:00" in window["text"]
            else:
                assert (await i.media.source(agent, source["id"]))["latest_frames_id"]
            await i.media.delete(agent, source["id"])
            assert (await i.objects.describe(agent, obj["id"]))["references"] == 0
            assert not (await i.database.integrity())["foreign_keys"]

    asyncio.run(scenario())


def test_upload_validation_scope_and_operation_refusal_leave_no_job_or_reference(tmp_path):
    import uuid

    async def scenario():
        async with instance(tmp_path) as i:
            agent = (await i.application.query("agents", ""))[0]["id"]
            other = await i.application.create_agent("Other")
            for payload, mime, title in (
                (b"", "audio/wav", "empty"),
                (b"x", "text/plain", "wrong"),
                (b"x", "audio/wav", "\nprivate"),
                (b"x", "audio/wav", " "),
            ):
                obj = await i.objects.publish(agent, payload, mime)
                with pytest.raises(BusinessError):
                    await i.media.register_upload(agent, obj["id"], title)
                assert (await i.objects.describe(agent, obj["id"]))["references"] == 0
            obj = await i.objects.publish(agent, b"valid-body", "audio/wav")
            with pytest.raises(BusinessError):
                await i.media.register_upload(other["id"], obj["id"], "Wrong agent")
            with pytest.raises(BusinessError):
                await i.media.register_upload(agent, str(uuid.uuid4()), "Missing")
            source = await i.media.register_upload(agent, obj["id"], "Audio")
            for operation in ("subtitle", "frames"):
                with pytest.raises(BusinessError):
                    await i.media.submit(agent, source["id"], operation, {})
            assert not (await i.media.jobs(agent, source["id"]))["items"]

    asyncio.run(scenario())


def test_upload_http_registration_and_transcript_without_channel_is_owner_scoped(tmp_path):
    import time

    from fastapi.testclient import TestClient

    from omni.composition.http import http_app

    gateway = FakeGateway()
    with TestClient(
        http_app(
            tmp_path,
            model=NoModel(),
            bilibili_backend=gateway,
            media_processor=FakeProcessor(),
            credential_backend=Secrets(),
        )
    ) as client:
        assert (
            client.post("/api/v1/agents/00000000-0000-0000-0000-000000000000/media/uploads", json={}).status_code == 401
        )
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text(encoding="utf-8")
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        base = f"/api/v1/agents/{agent}"
        obj = client.post(
            base + "/objects?pinned=false", content=b"http-local-audio", headers={"Content-Type": "audio/wav"}
        ).json()
        response = client.post(base + "/media/uploads", json={"object_id": obj["id"], "title": "HTTP upload"})
        assert response.status_code == 201 and response.headers["cache-control"] == "no-store"
        source = response.json()
        assert (
            client.post(
                base + "/media/uploads", json={"object_id": obj["id"], "title": "HTTP upload", "path": "/tmp/private"}
            ).status_code
            == 422
        )
        job = client.post(base + f"/media/sources/{source['id']}/jobs", json={"operation": "transcribe"}).json()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            result = client.get(base + f"/media/jobs/{job['id']}").json()
            if result["state"] not in ("queued", "running"):
                break
            time.sleep(0.02)
        assert result["state"] == "done", result
        latest = client.get(base + f"/media/sources/{source['id']}").json()["latest_transcript_id"]
        transcript = client.get(base + f"/media/transcripts/{latest}/window").json()
        assert "你好世界" in transcript["text"] and not gateway.reads and not gateway.downloads


def test_cancelled_registration_waiter_keeps_actual_object_read_and_commit_owned(tmp_path):
    async def scenario():
        async with instance(tmp_path) as i:
            agent = (await i.application.query("agents", ""))[0]["id"]
            obj = await i.objects.publish(agent, b"cancelled-waiter-upload", "audio/wav")
            entered, release = asyncio.Event(), asyncio.Event()
            original = i.objects.get
            reads = []

            async def read(*args):
                reads.append(args)
                entered.set()
                await release.wait()
                return await original(*args)

            i.objects.get = read
            caller = asyncio.create_task(i.media.register_upload(agent, obj["id"], "Owned upload"))
            try:
                await entered.wait()
                owned = tuple(i.media._tasks)
                caller.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await caller
                assert not await i.media.close(0.01) and not owned[0].done()
                assert not i.database.metrics["closed"]
                release.set()
                await asyncio.gather(*owned)
                assert len(reads) == 1 and (await i.objects.describe(agent, obj["id"]))["references"] == 1
                assert await i.media.close() and i.media.health()["pending"] == 0
                with pytest.raises(BusinessError):
                    await i.media.register_upload(agent, obj["id"], "No new work")
                assert len(reads) == 1
            finally:
                release.set()
                await asyncio.gather(caller, return_exceptions=True)

    asyncio.run(scenario())


def test_uploaded_object_reference_and_transcript_survive_verified_backup_restore(tmp_path):
    from omni.adapters.persistence.maintenance import backup, restore
    from omni.adapters.platform.runtime.paths import InstancePaths

    home = tmp_path / "source"
    snapshot = tmp_path / "snapshot"
    restored = tmp_path / "restored"

    async def prepare():
        async with instance(home) as i:
            agent = (await i.application.query("agents", ""))[0]["id"]
            obj = await i.objects.publish(agent, b"restored-media-input", "audio/wav")
            source = await i.media.register_upload(agent, obj["id"], "Backup upload")
            job = await i.media.submit(agent, source["id"], "transcribe", {})
            assert (await _settle(i, agent, job["id"]))["state"] == "done"
            return agent, obj, source

    agent, obj, source = asyncio.run(prepare())
    backup(InstancePaths.resolve(home), snapshot)
    restore(snapshot, restored)

    async def verify():
        async with instance(restored) as i:
            row, body = await i.objects.get(agent, obj["id"])
            assert row["references"] == 1 and body == b"restored-media-input"
            window = await i.media.transcript_window(agent, source["id"], None, 0, 12000)
            assert "你好世界" in window["text"] and not (await i.database.integrity())["foreign_keys"]

    asyncio.run(verify())
