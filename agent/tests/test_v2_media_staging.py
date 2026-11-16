"""Staging failures settle jobs and cleanup still runs after settlement errors."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from omni.application import media
from omni.domain.contracts import BusinessError


class Database:
    def __init__(self, operation="subtitle", *, cancelled=False, finish_error=None):
        self.job = {
            "id": "job",
            "agent_id": "agent",
            "source_id": "source",
            "operation": operation,
            "state": "cancelled" if cancelled else "queued",
        }
        self.starts, self.finishes, self.finish_error = 0, [], finish_error

    async def read(self, operation):
        return operation(self)

    async def write(self, operation):
        return operation(self)

    def media_job_get(self, job_id):
        return dict(self.job)

    def media_job_start(self, job_id):
        self.starts += 1
        if self.job["state"] == "cancelled":
            raise BusinessError("media_job_cancelled", "Cancelled before execution", status=409)
        self.job["state"] = "running"

    def media_source_get(self, agent_id, source_id):
        return {"config_id": "config", "config_revision_id": "revision", "bvid": "BV1xx411c7mD"}

    def bilibili_read_authorized(self, agent_id, config_id, revision_id):
        return {"revision_id": revision_id, "value": {}, "authority": "fixture-authority"}

    def media_job_finish(self, job_id, state, code):
        self.finishes.append((state, code))
        if self.finish_error:
            raise self.finish_error
        if self.job["state"] == "cancelled":
            raise BusinessError("media_job_state_invalid", "Already terminal", status=409)
        self.job.update(state=state, error_code=code)


class Gateway:
    def __init__(self):
        self.calls = []

    async def read(self, configuration, operation, target):
        self.calls.append(operation)
        return {"text": ""}


def fixture(tmp_path, **options):
    database, gateway = Database(**options), Gateway()
    service = media.MediaService(database, None, None, gateway, None, None, tmp_path / "staging")
    return service, database, gateway


@pytest.mark.parametrize("failure", [PermissionError, FileExistsError, OSError])
def test_staging_creation_failure_is_terminal_without_dispatch(tmp_path, monkeypatch, caplog, failure):
    async def scenario():
        service, database, gateway = fixture(tmp_path)
        staging, mkdir = service._staging / "job", Path.mkdir

        def fail(path, *args, **kwargs):
            if path == staging:
                raise failure("PRIVATE_STAGING_CREATION_ERROR")
            return mkdir(path, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", fail)
        await service._execute("job")
        assert database.job["state"] == "failed" and database.job["error_code"] == "media_staging_unavailable"
        assert database.starts == 1 and database.finishes == [("failed", "media_staging_unavailable")]
        assert not gateway.calls and not staging.exists()
        assert "PRIVATE_STAGING_CREATION_ERROR" not in caplog.text

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["subtitle", "transcribe", "frames"])
def test_already_cancelled_job_does_not_create_staging_or_settle_again(tmp_path, monkeypatch, operation):
    async def scenario():
        service, database, gateway = fixture(tmp_path, operation=operation, cancelled=True)
        created, staging, mkdir = [], service._staging / "job", Path.mkdir

        def observe(path, *args, **kwargs):
            if path == staging:
                created.append(path)
            return mkdir(path, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", observe)
        await service._execute("job")
        assert database.job["state"] == "cancelled" and database.starts == 1
        assert not created and not database.finishes and not gateway.calls and not staging.exists()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["business", "io"])
def test_failed_terminal_write_still_removes_staging_and_preserves_error(tmp_path, kind):
    async def scenario():
        error = (
            BusinessError("fixture_settlement_failure", "Settlement unavailable", status=503)
            if kind == "business"
            else OSError("PRIVATE_SETTLEMENT_FAILURE")
        )
        service, database, gateway = fixture(tmp_path, finish_error=error)
        staging = service._staging / "job"
        staging.mkdir(parents=True)
        (staging / "input.bin").write_bytes(b"owned temporary bytes")
        with pytest.raises(type(error)) as caught:
            await service._execute("job")
        assert caught.value is error and not staging.exists()
        assert database.job["state"] == "running" and database.finishes == [("failed", "media_subtitle_missing")]
        assert gateway.calls == ["subtitle"]

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["permission", "missing_child"])
def test_cleanup_failure_reports_residual_without_native_error_body(tmp_path, monkeypatch, caplog, failure):
    service, _, _ = fixture(tmp_path)
    staging = service._staging / "job"
    staging.mkdir(parents=True)

    def remove(path, *, ignore_errors=False):
        assert path == staging
        if not ignore_errors:
            if failure == "missing_child":
                raise FileNotFoundError(2, "PRIVATE_STAGING_DELETE_ERROR", str(staging / "child.bin"))
            raise PermissionError("PRIVATE_STAGING_DELETE_ERROR")

    monkeypatch.setattr(media.shutil, "rmtree", remove)
    service._cleanup(staging)
    records = [record for record in caplog.records if getattr(record, "event", "") == "media.staging_left"]
    assert len(records) == 1 and records[0].path == str(staging) and records[0].exc_info is None
    assert "PRIVATE_STAGING_DELETE_ERROR" not in caplog.text


def test_missing_staging_is_already_clean_without_warning(tmp_path, caplog):
    service, _, _ = fixture(tmp_path)
    service._cleanup(service._staging / "missing")
    assert not [record for record in caplog.records if getattr(record, "event", "") == "media.staging_left"]


@pytest.mark.parametrize("fault", ["mkdir", "queued_cancel", "finish"])
def test_real_job_staging_faults_preserve_durable_state_and_allow_new_work(tmp_path, monkeypatch, fault):
    from test_v2_media import BV, FakeProcessor, _media_instance, _seed, _settle

    async def scenario():
        processor = FakeProcessor()
        instance = await _media_instance(tmp_path, processor=processor)
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            operation = "transcribe" if fault == "finish" else "subtitle"
            job = await instance._database.write(
                lambda unit: unit.media_job_create(agent, str(source["id"]), operation, {})
            )
            identity, original = str(job["id"]), instance.media._db
            staging = instance.media._staging / identity
            error = OSError("PRIVATE_REAL_SETTLEMENT_ERROR")
            with monkeypatch.context() as patch:
                if fault == "mkdir":
                    mkdir = Path.mkdir

                    def fail(path, *args, **kwargs):
                        if path == staging:
                            raise PermissionError("PRIVATE_REAL_STAGING_ERROR")
                        return mkdir(path, *args, **kwargs)

                    patch.setattr(Path, "mkdir", fail)
                elif fault == "queued_cancel":
                    await instance.media.cancel(agent, identity)
                else:
                    processor.failure = BusinessError(
                        "media_component_missing", "Controlled missing component", status=503
                    )

                    class Work:
                        def __init__(self, unit):
                            self.unit = unit

                        def __getattr__(self, name):
                            return getattr(self.unit, name)

                        def media_job_finish(self, *args):
                            raise error

                    class FailedFinish:
                        async def read(self, callback):
                            return await original.read(callback)

                        async def write(self, callback):
                            return await original.write(lambda unit: callback(Work(unit)))

                    patch.setattr(instance.media, "_db", FailedFinish())
                result = (await asyncio.gather(instance.media._execute(identity), return_exceptions=True))[0]
                if fault == "finish":
                    assert result is error
                else:
                    assert result is None
            row = await instance.media.job(agent, identity)
            expected = {"mkdir": "failed", "queued_cancel": "cancelled", "finish": "running"}[fault]
            assert row["state"] == expected and not staging.exists()
            if fault == "mkdir":
                assert row["error_code"] == "media_staging_unavailable"
            view = await instance.media.source(agent, str(source["id"]))
            assert not view["latest_transcript_id"] and not view["latest_frames_id"]
            processor.failure = None
            fresh = await instance.media.submit(agent, str(source["id"]), "subtitle", {})
            assert (await _settle(instance, agent, fresh["id"]))["state"] == "done"
            assert (await instance.media.job(agent, identity))["state"] == expected
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())
