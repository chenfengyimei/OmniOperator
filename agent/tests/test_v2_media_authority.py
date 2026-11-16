"""Media captures one authority and rechecks it before later I/O and commit."""

from __future__ import annotations

import asyncio

import pytest
from test_v2_media import BV, CHANNEL, FakeGateway, FakeProcessor

from omni.application.media import MediaService
from omni.domain.contracts import BusinessError

STAGES = [
    ("subtitle", "after_subtitle", 0, 0, 0, 0),
    ("transcribe", "after_streams", 0, 0, 0, 0),
    ("transcribe", "before_download_dispatch", 0, 0, 0, 0),
    ("transcribe", "after_download", 1, 0, 0, 0),
    ("transcribe", "after_worker", 1, 1, 0, 0),
    ("transcribe", "after_object", 1, 1, 1, 0),
    ("transcribe", "before_artifact_commit", 1, 1, 1, 0),
    ("frames", "after_worker", 1, 1, 0, 0),
    ("frames", "after_object", 1, 1, 1, 0),
    ("frames", "after_image", 1, 1, 1, 1),
    ("frames", "before_artifact_commit", 1, 1, 1, 1),
]


class Database:
    def __init__(self, operation):
        self.revision, self.authority, self.enabled, self.binding = "revision", 1, True, "config"
        self.deferred, self.sources, self.artifacts = None, [], []
        self.job = {
            "id": "job",
            "agent_id": "agent",
            "source_id": "source",
            "operation": operation,
            "state": "queued",
            "params": {"count": 3, "width": 320, "model": "small", "device": "cpu", "compute_type": "int8"},
        }

    def change(self, kind):
        if kind == "revision":
            self.revision = "new-revision"
        elif kind == "authority":
            self.authority += 1
        elif kind == "disabled":
            self.enabled = False
        else:
            assert kind == "rebind"
            self.binding = "other-config"

    async def read(self, operation):
        return operation(self)

    async def write(self, operation):
        if self.deferred:
            kind, self.deferred = self.deferred, None
            self.change(kind)
        return operation(self)

    def bilibili_read_authorized(self, agent_id, config_id, revision_id=""):
        if not self.enabled or (revision_id and revision_id != self.revision):
            raise BusinessError("bilibili_configuration_revoked", "Controlled configuration change")
        return {
            "revision_id": self.revision,
            "authority": f"{self.revision}:{self.authority}",
            "value": {**CHANNEL, "endpoint": ""},
        }

    def media_source_register(self, agent_id, row):
        self.sources.append(row)
        return {"id": "source", **row}

    def media_source_get(self, agent_id, source_id):
        return {"config_id": self.binding, "config_revision_id": "revision", "bvid": BV}

    def media_job_get(self, job_id):
        return dict(self.job)

    def media_job_start(self, job_id):
        self.job["state"] = "running"

    def media_job_finish(self, job_id, state, code):
        self.job.update(state=state, error_code=code)
        return dict(self.job)

    def media_job_complete_transcript(self, job_id, info):
        self.artifacts.append(info)
        self.job["state"] = "done"
        return dict(self.job)

    media_job_complete_frames = media_job_complete_transcript


def fixture(tmp_path, operation, phase="none", change="revision"):
    database, events = Database(operation), {"downloads": 0, "objects": 0, "images": 0}

    def changed(stage):
        if phase == stage:
            database.change(change)

    class Gateway(FakeGateway):
        async def read(self, configuration, action, target, limit=10):
            result = await super().read(configuration, action, target, limit)
            changed("after_" + action)
            if action == "video" and phase == "before_registration_commit":
                database.deferred = change
            return result

        async def download(self, configuration, url, destination, *, max_bytes, budget_s, before_dispatch=None):
            changed("before_download_dispatch")
            if before_dispatch:
                await before_dispatch()
            events["downloads"] += 1
            result = await super().download(configuration, url, destination, max_bytes=max_bytes, budget_s=budget_s)
            changed("after_download")
            return result

    class Processor(FakeProcessor):
        async def transcribe(self, *args):
            result = await super().transcribe(*args)
            changed("after_worker")
            return result

        async def frames(self, *args):
            result = await super().frames(*args)
            changed("after_worker")
            return result

    class Objects:
        async def publish(self, agent_id, content, mime):
            events["objects"] += 1
            changed("after_object")
            if phase == "before_artifact_commit" and operation != "frames":
                database.deferred = change
            return {"id": "object", "bytes": len(content)}

    class Images:
        async def prepare(self, agent_id, object_id):
            events["images"] += 1
            changed("after_image")
            if phase == "before_artifact_commit":
                database.deferred = change
            return {"state": "registered_not_attached"}

    gateway, processor = Gateway(), Processor()
    service = MediaService(database, Objects(), Images(), gateway, processor, None, tmp_path / "staging")
    return service, database, gateway, processor, events


@pytest.mark.parametrize("phase", ["after_video", "before_registration_commit"])
@pytest.mark.parametrize("change", ["revision", "authority", "disabled"])
def test_registration_rechecks_authority_inside_its_write_transaction(tmp_path, phase, change):
    async def scenario():
        service, database, gateway, _, _ = fixture(tmp_path, "subtitle", phase, change)
        with pytest.raises(BusinessError) as error:
            await service.register("agent", "config", BV)
        assert error.value.code == (
            "bilibili_credential_changed" if change == "authority" else "bilibili_configuration_revoked"
        )
        assert not database.sources and gateway.reads == [("video", BV)]
        assert await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation,phase,downloads,workers,objects,images", STAGES)
@pytest.mark.parametrize("change", ["revision", "authority"])
def test_job_stops_at_revoked_stage_and_never_commits_an_artifact(
    tmp_path, operation, phase, downloads, workers, objects, images, change
):
    async def scenario():
        service, database, _, processor, events = fixture(tmp_path, operation, phase, change)
        await service._execute("job")
        assert database.job["state"] == "failed"
        assert database.job["error_code"] == (
            "bilibili_credential_changed" if change == "authority" else "bilibili_configuration_revoked"
        )
        assert events == {"downloads": downloads, "objects": objects, "images": images}
        assert len(processor.calls) == workers and not database.artifacts
        assert not (service._staging / "job").exists() and await service.close()

    asyncio.run(scenario())


def test_rebound_source_blocks_old_configuration_before_download(tmp_path):
    async def scenario():
        service, database, _, processor, events = fixture(tmp_path, "transcribe", "after_streams", "rebind")
        await service._execute("job")
        assert database.job["state"] == "failed" and database.job["error_code"] == "bilibili_configuration_revoked"
        assert events == {"downloads": 0, "objects": 0, "images": 0} and not processor.calls and not database.artifacts
        assert await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["subtitle", "transcribe", "frames"])
def test_unchanged_authority_keeps_successful_media_behavior(tmp_path, operation):
    async def scenario():
        service, database, _, _, events = fixture(tmp_path, operation)
        await service._execute("job")
        assert database.job["state"] == "done" and len(database.artifacts) == 1
        assert events["objects"] == 1 and events["images"] == (operation == "frames")
        assert not (service._staging / "job").exists() and await service.close()

    asyncio.run(scenario())


def test_real_gateway_download_retry_uses_media_authority_guard(tmp_path):
    import httpx
    from test_v2_bilibili_gateway import Secrets

    from omni.adapters.bilibili import BilibiliGateway
    from omni.adapters.diagnostics import Redactor

    async def scenario():
        service, database, gateway, processor, _ = fixture(tmp_path, "transcribe")
        calls, original_read = [], gateway.read

        def handle(request):
            calls.append(request)
            if len(calls) == 1:
                database.change("authority")
                return httpx.Response(503)
            return httpx.Response(200, content=b"owned media bytes")

        async def unused(_):
            raise AssertionError("No deliveries")

        transport = BilibiliGateway(
            Secrets(),
            Redactor(),
            unused,
            client_factory=lambda **options: httpx.AsyncClient(transport=httpx.MockTransport(handle), **options),
        )

        async def read(*args, **kwargs):
            result = await original_read(*args, **kwargs)
            if args[1] == "streams":
                result["audio"]["url"] = "https://cdn.bilivideo.com/owned.m4s"
            return result

        gateway.read, gateway.download = read, transport.download
        try:
            await service._execute("job")
            assert database.job["state"] == "failed" and database.job["error_code"] == "bilibili_credential_changed"
            assert len(calls) == 1 and not processor.calls and not database.artifacts
        finally:
            assert await service.close() and await transport.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["after_streams", "after_worker", "before_artifact_commit"])
def test_real_database_revocation_blocks_later_work_and_allows_explicit_rebind(tmp_path, monkeypatch, phase):
    from test_v2_media import _media_instance, _seed, _settle

    async def scenario():
        gateway, processor = FakeGateway(), FakeProcessor()
        instance = await _media_instance(tmp_path, gateway=gateway, processor=processor)
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            original_db, original_publish, original_read, original_transcribe = (
                instance.media._db,
                instance.media._objects.publish,
                gateway.read,
                processor.transcribe,
            )
            armed, published = [], []

            async def revoke():
                await instance.configurations.save(
                    agent, str(config["id"]), "Changed fixture", True, CHANNEL, int(config["revision"])
                )

            async def read(*args, **kwargs):
                result = await original_read(*args, **kwargs)
                if args[1] == "streams" and phase == "after_streams":
                    await revoke()
                return result

            async def transcribe(*args, **kwargs):
                result = await original_transcribe(*args, **kwargs)
                if phase == "after_worker":
                    await revoke()
                return result

            async def publish(*args, **kwargs):
                result = await original_publish(*args, **kwargs)
                published.append(result)
                if phase == "before_artifact_commit":
                    armed.append(True)
                return result

            class QueuedWrite:
                async def read(self, callback):
                    return await original_db.read(callback)

                async def write(self, callback):
                    if armed:
                        armed.clear()
                        await revoke()
                    return await original_db.write(callback)

            with monkeypatch.context() as patch:
                patch.setattr(gateway, "read", read)
                patch.setattr(processor, "transcribe", transcribe)
                patch.setattr(instance.media._objects, "publish", publish)
                patch.setattr(instance.media, "_db", QueuedWrite())
                job = await instance.media.submit(agent, str(source["id"]), "transcribe", {})
                row = await _settle(instance, agent, job["id"])
            assert row["state"] == "failed" and row["error_code"] == "bilibili_configuration_revoked"
            assert len(published) == (phase == "before_artifact_commit")
            assert len(gateway.downloads) == (phase != "after_streams")
            assert len(processor.calls) == (phase != "after_streams")
            view = await instance.media.source(agent, str(source["id"]))
            assert not view["latest_transcript_id"] and not view["latest_frames_id"]
            assert not (instance.media._staging / str(job["id"])).exists()
            rebound = await instance.media.register(agent, str(config["id"]), BV)
            assert rebound["config_revision_id"] != source["config_revision_id"]
            fresh = await instance.media.submit(agent, str(source["id"]), "subtitle", {})
            assert (await _settle(instance, agent, fresh["id"]))["state"] == "done"
            assert (await instance.media.job(agent, str(job["id"])))["state"] == "failed"
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())
