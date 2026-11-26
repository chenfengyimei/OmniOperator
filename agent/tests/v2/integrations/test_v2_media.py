"""V36 media upgrade, bounded job state machine, immutable artifacts and restart recovery."""

from omni_test_support.v2.media import (
    ADAPTER as ADAPTER,
)
from omni_test_support.v2.media import (
    BV as BV,
)
from omni_test_support.v2.media import (
    CHANNEL as CHANNEL,
)
from omni_test_support.v2.media import (
    EXPIRES as EXPIRES,
)
from omni_test_support.v2.media import (
    VIDEO as VIDEO,
)
from omni_test_support.v2.media import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.media import (
    Database as Database,
)
from omni_test_support.v2.media import (
    FakeGateway as FakeGateway,
)
from omni_test_support.v2.media import (
    FakeProcessor as FakeProcessor,
)
from omni_test_support.v2.media import (
    FileObjects as FileObjects,
)
from omni_test_support.v2.media import (
    Image as Image,
)
from omni_test_support.v2.media import (
    InvocationContext as InvocationContext,
)
from omni_test_support.v2.media import (
    ModelReply as ModelReply,
)
from omni_test_support.v2.media import (
    NoModel as NoModel,
)
from omni_test_support.v2.media import (
    Outcome as Outcome,
)
from omni_test_support.v2.media import (
    Secrets as Secrets,
)
from omni_test_support.v2.media import (
    SystemClock as SystemClock,
)
from omni_test_support.v2.media import (
    TestClient as TestClient,
)
from omni_test_support.v2.media import (
    UUIDs as UUIDs,
)
from omni_test_support.v2.media import (
    _agent_and_source as _agent_and_source,
)
from omni_test_support.v2.media import (
    _context as _context,
)
from omni_test_support.v2.media import (
    _media_instance as _media_instance,
)
from omni_test_support.v2.media import (
    _seed as _seed,
)
from omni_test_support.v2.media import (
    _settle as _settle,
)
from omni_test_support.v2.media import (
    _settle_running as _settle_running,
)
from omni_test_support.v2.media import (
    _wait_done as _wait_done,
)
from omni_test_support.v2.media import (
    asyncio as asyncio,
)
from omni_test_support.v2.media import (
    command as command,
)
from omni_test_support.v2.media import (
    configuration as configuration,
)
from omni_test_support.v2.media import (
    contextmanager as contextmanager,
)
from omni_test_support.v2.media import (
    database_engine as database_engine,
)
from omni_test_support.v2.media import (
    hashlib as hashlib,
)
from omni_test_support.v2.media import (
    http_app as http_app,
)
from omni_test_support.v2.media import (
    io as io,
)
from omni_test_support.v2.media import (
    open_instance as open_instance,
)
from omni_test_support.v2.media import (
    pytest as pytest,
)
from omni_test_support.v2.media import (
    rejects as rejects,
)
from omni_test_support.v2.media import (
    s as s,
)
from omni_test_support.v2.media import (
    select as select,
)
from omni_test_support.v2.media import (
    sheet_png as sheet_png,
)
from omni_test_support.v2.media import (
    source_row as source_row,
)
from omni_test_support.v2.media import (
    time as time,
)
from omni_test_support.v2.media import (
    transcript_payload as transcript_payload,
)
from omni_test_support.v2.media import (
    uuid as uuid,
)


@pytest.mark.parametrize("vad_empty", [False, True])
def test_worker_asr_uses_only_local_models_and_retries_without_vad_only_for_empty_output(
    tmp_path, monkeypatch, vad_empty
):
    import json
    import sys
    import types

    from omni.adapters.workers import media as worker

    audio = object()
    constructors, calls = [], []

    class Model:
        def __init__(self, name, **options):
            constructors.append((name, options))

        def transcribe(self, samples, *, vad_filter, beam_size):
            assert samples is audio and beam_size == 5
            calls.append(vad_filter)
            rows = [] if vad_empty and vad_filter else [types.SimpleNamespace(start=0.25, end=1.5, text="  离线字幕  ")]
            return iter(rows), types.SimpleNamespace(language="zh")

    module = types.ModuleType("faster_whisper")
    module.WhisperModel = Model
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    monkeypatch.setattr(worker, "_decode_audio_16k_mono", lambda path: audio)
    output = tmp_path / "transcript.json"
    result = worker._transcribe(
        {
            "audio": str(tmp_path / "owned.wav"),
            "output": str(output),
            "models_root": str(tmp_path / "models"),
            "model": "fixture-model",
            "device": "cpu",
            "compute_type": "int8",
        }
    )
    assert constructors == [
        (
            "fixture-model",
            {
                "device": "cpu",
                "compute_type": "int8",
                "download_root": str(tmp_path / "models"),
                "local_files_only": True,
            },
        )
    ]
    assert calls == ([True, False] if vad_empty else [True])
    assert json.loads(output.read_bytes()) == {
        "version": 1,
        "kind": "asr",
        "language": "zh",
        "segments": [[0.25, 1.5, "离线字幕"]],
    }
    assert result == {"segments": 1, "language": "zh", "duration_s": 1.5, "bytes": output.stat().st_size}


def test_v36_upgrade_registers_sources_and_bounds_jobs(tmp_path):
    path, clock, ids = tmp_path / "omni.db", SystemClock(), UUIDs()
    engine = database_engine(path)
    try:
        with engine.connect() as connection:
            cfg = configuration()
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "v2_0035")
            connection.commit()
    finally:
        engine.dispose()

    async def scenario():
        db = Database(path, clock, ids)
        try:
            await db.write(lambda u: u.recover())
            assert db.schema_version == "v2_0059" and len(s.metadata.tables) == 161
            agent, source = await _agent_and_source(db, tmp_path)
            view = await db.read(lambda u: u.media_source_get(agent, str(source["id"])))
            assert view["bvid"] == VIDEO["bvid"] and view["active_jobs"] == 0

            # Reregistering the same video is idempotent and never duplicates rows.
            again = await db.write(
                lambda u: u.media_source_register(
                    agent, source_row(str(source["config_id"]), str(source["config_revision_id"]), VIDEO)
                )
            )
            assert str(again["id"]) == str(source["id"])

            jobs = []
            for _ in range(8):
                created = await db.write(
                    lambda u: u.media_job_create(agent, str(source["id"]), "transcribe", {"model": "small"})
                )
                jobs.append(str(created["id"]))
            # The admission trigger caps queued+running jobs per source at eight.
            with rejects("data_constraint"):
                await db.write(
                    lambda u: u.media_job_create(agent, str(source["id"]), "frames", {"count": 6, "width": 640})
                )
            running = await db.write(lambda u: u.media_job_start(jobs[0]))
            assert running["state"] == "running"
            with rejects("data_constraint"):
                await db.write(
                    lambda u: u.media_job_create(agent, str(source["id"]), "frames", {"count": 6, "width": 640})
                )
            cancelled = await db.write(lambda u: u.media_job_cancel(jobs[1]))
            assert cancelled["state"] == "cancelled" and cancelled["error_code"] == "media_cancelled"
            admitted = await db.write(lambda u: u.media_job_create(agent, str(source["id"]), "subtitle", None))
            # A cancelled job can never start again; the specific code names the owner's cancel.
            with rejects("media_job_cancelled"):
                await db.write(lambda u: u.media_job_start(jobs[1]))

            # A done job must carry its artifact; finishing without one is refused.
            with rejects("data_constraint"):
                await db.write(lambda u: u.media_job_finish(jobs[0], "done", ""))

            blob = FileObjects(tmp_path / "objects").put(
                agent, transcript_payload("asr", "zh", [[0.0, 2.0, "Hello world"]])
            )
            obj = await db.write(lambda u: u.object_register(blob, "application/json", EXPIRES, False))
            done = await db.write(
                lambda u: u.media_job_complete_transcript(
                    jobs[0],
                    {"kind": "asr", "language": "zh", "object_id": str(obj["id"]), "bytes": int(obj["bytes"])},
                )
            )
            assert done["state"] == "done" and done["error_code"] == ""
            # Terminal rows are immutable; a late finish is an idempotent no-op
            # because a cancelled racing task must observe the completed state.
            repeat = await db.write(lambda u: u.media_job_finish(jobs[0], "failed", "late"))
            assert repeat["state"] == "done" and repeat["error_code"] == ""
            transcript_id = str(
                (
                    await db.read(
                        lambda u: u.connection.execute(
                            select(s.media_transcripts.c.id).where(s.media_transcripts.c.job_id == jobs[0])
                        ).scalar_one()
                    )
                )
            )
            transcript = await db.read(lambda u: u.media_transcript_get(agent, transcript_id))
            assert transcript["kind"] == "asr" and transcript["language"] == "zh"

            # A queued job only ever leaves via cancel; failures apply to running work.
            await db.write(lambda u: u.media_job_start(jobs[2]))
            failed = await db.write(lambda u: u.media_job_finish(jobs[2], "failed", "media_processing_failed"))
            assert failed["state"] == "failed" and failed["error_code"] == "media_processing_failed"

            # Deleting a source with active jobs is refused; after they drain it cascades.
            with rejects("media_source_busy"):
                await db.write(lambda u: u.media_source_delete(agent, str(source["id"])))
            await db.write(lambda u: u.media_job_cancel(str(admitted["id"])))
            for job_id in jobs[3:8]:
                await db.write(lambda u: u.media_job_cancel(job_id))
            await db.write(lambda u: u.media_source_delete(agent, str(source["id"])))
            assert not await db.read(lambda u: u.connection.execute(select(s.media_jobs)).all())
            assert not await db.read(lambda u: u.connection.execute(select(s.media_transcripts)).all())
            with rejects("media_source_not_found"):
                await db.read(lambda u: u.media_source_get(agent, str(source["id"])))
            assert not (await db.integrity())["foreign_keys"]
        finally:
            await db.close()

    asyncio.run(scenario())


def test_v36_restart_recovery_empties_active_jobs_without_scope_violation(tmp_path):
    path, clock, ids = tmp_path / "omni.db", SystemClock(), UUIDs()

    async def scenario():
        db = Database(path, clock, ids)
        try:
            await db.write(lambda u: u.recover())
            agent, source = await _agent_and_source(db, tmp_path)
            for _ in range(3):
                await db.write(lambda u: u.media_job_create(agent, str(source["id"]), "subtitle", None))
            rows = await db.read(lambda u: u.media_job_page(agent, str(source["id"])))
            await db.write(lambda u: u.media_job_start(str(rows[0]["id"])))

            report = await db.write(lambda u: u.media_recover())
            assert report == {"cancelled": 2, "unknown": 1}
            rows = await db.read(lambda u: u.media_job_page(agent, str(source["id"])))
            assert len(rows) == 3
            interrupted = [row for row in rows if row["state"] == "cancelled"]
            unknown = [row for row in rows if row["state"] == "unknown"]
            assert len(interrupted) == 2 and all(
                row["error_code"] == "media_restart_interrupted" for row in interrupted
            )
            assert len(unknown) == 1 and unknown[0]["error_code"] == "media_restart_unknown"
            # Recovery is idempotent and never touches finished rows.
            assert await db.write(lambda u: u.media_recover()) == {"cancelled": 0, "unknown": 0}
            assert not (await db.integrity())["foreign_keys"]
        finally:
            await db.close()

    asyncio.run(scenario())


# ---------- service production path through a real instance ----------


def test_media_service_registers_transcribes_and_serves_tool_windows(tmp_path):
    async def scenario():
        gateway, processor = FakeGateway(), FakeProcessor()
        i = await _media_instance(tmp_path, gateway, processor)
        try:
            agent, config = await _seed(i)
            source = await i.media.register(agent, str(config["id"]), BV)
            assert source["bvid"] == BV and source["duration_s"] == 600
            assert gateway.reads == [("video", BV)]

            subtitle = await _settle(
                i, agent, (await i.media.submit(agent, str(source["id"]), "subtitle", {}))["id"], wait_for_cleanup=True
            )
            assert subtitle["state"] == "done" and subtitle["operation"] == "subtitle"
            # Subtitle jobs never download stream bytes.
            assert not gateway.downloads

            asr = await _settle(
                i,
                agent,
                (
                    await i.media.submit(
                        agent,
                        str(source["id"]),
                        "transcribe",
                        {"model": "small", "device": "cpu", "compute_type": "int8"},
                    )
                )["id"],
                wait_for_cleanup=True,
            )
            assert asr["state"] == "done"
            assert gateway.downloads[-1][0].endswith("audio.m4s")
            assert processor.calls[-1][0] == "transcribe" and processor.calls[-1][2]["model"] == "small"

            frames = await _settle(
                i,
                agent,
                (await i.media.submit(agent, str(source["id"]), "frames", {"count": 3, "width": 320}))["id"],
                wait_for_cleanup=True,
            )
            assert frames["state"] == "done"
            assert gateway.downloads[-1][0].endswith("video.m4s")
            assert processor.calls[-1][0] == "frames" and processor.calls[-1][1] == 3

            view = await i.media.source(agent, str(source["id"]))
            assert view["latest_transcript_id"] and view["latest_frames_id"]
            # Per-job staging directories are removed after every job.
            staging = i.paths.control / "media-staging"
            assert not staging.exists() or list(staging.iterdir()) == []

            # Model tools only expose bounded anchored windows of real rows.
            context = _context(agent)
            listing = await i.media.invoke("media_list", {"limit": 5}, operation_id="fixture", context=context)
            assert listing.status == Outcome.OK and BV in listing.text
            window = await i.media.invoke(
                "media_transcript",
                {"source_id": str(source["id"]), "length": 200},
                operation_id="fixture",
                context=context,
            )
            assert window.status == Outcome.OK
            assert "[00:00-00:02] 你好世界" in window.text and "[00:02-00:04] 第二句锚点" in window.text
            assert "kind=asr" in window.text and window.data["end"] == window.data["total_chars"]

            # The subtitle transcript keeps its full text without invented anchors.
            subtitle_id = await i._database.read(
                lambda u: u.connection.execute(
                    select(s.media_transcripts.c.id).where(s.media_transcripts.c.job_id == str(subtitle["id"]))
                ).scalar_one()
            )
            plain = await i.media.invoke(
                "media_transcript",
                {"source_id": str(source["id"]), "transcript_id": str(subtitle_id)},
                operation_id="fixture",
                context=context,
            )
            assert plain.status == Outcome.OK and plain.data["text"] == "第一行字幕\n第二行字幕"
            assert plain.data["kind"] == "subtitle"

            # Missing rows surface as honest errors, not invented text.
            missing = await i.media.invoke(
                "media_transcript",
                {"source_id": str(source["id"]), "transcript_id": str(uuid.uuid4())},
                operation_id="fixture",
                context=context,
            )
            assert missing.status == Outcome.ERROR and missing.error_code == "media_transcript_not_found"
            unknown = await i.media.invoke("media_nope", {}, operation_id="fixture", context=context)
            assert unknown.status == Outcome.ERROR and unknown.error_code == "tool_not_found"

            # Frame sheets reuse the verified image chain: the object decodes and registers.
            sheet = await i.media.frames(agent, str(view["latest_frames_id"]))
            assert sheet["frame_count"] == 3 and sheet["width"] == 320 and sheet["height"] == 180
            image = await i._database.read(
                lambda u: u.connection.execute(
                    select(s.image_objects.c.width, s.image_objects.c.height).where(
                        s.image_objects.c.object_id == str(sheet["object_id"])
                    )
                ).first()
            )
            assert tuple(image) == (320, 180)
            _, content = await i.objects.get(agent, str(sheet["object_id"]))
            assert content.startswith(b"\x89PNG")
        finally:
            assert (await i.close())["complete"]

    asyncio.run(scenario())


def test_media_service_classifies_failures_unknowns_and_credential_drift(tmp_path):
    async def scenario():
        processor = FakeProcessor()
        i = await _media_instance(tmp_path, None, processor)
        try:
            agent, config = await _seed(i)
            source = await i.media.register(agent, str(config["id"]), BV)

            processor.failure = BusinessError("media_component_missing", "A component is not installed", status=503)
            failed = await _settle(i, agent, (await i.media.submit(agent, str(source["id"]), "transcribe", {}))["id"])
            assert failed["state"] == "failed" and failed["error_code"] == "media_component_missing"

            processor.failure = BusinessError("media_processing_timeout", "Exceeded budget", status=504)
            unknown = await _settle(i, agent, (await i.media.submit(agent, str(source["id"]), "transcribe", {}))["id"])
            assert unknown["state"] == "unknown" and unknown["error_code"] == "media_processing_timeout"

            processor.failure = RuntimeError("worker exploded without business code")
            crashed = await _settle(i, agent, (await i.media.submit(agent, str(source["id"]), "transcribe", {}))["id"])
            assert crashed["state"] == "unknown" and crashed["error_code"] == "media_processing_failed"
            processor.failure = None

            # Revising the bound configuration strands the source until re-registration.
            await i.configurations.save(
                agent, str(config["id"]), "Bili fixture", True, CHANNEL, int(config["revision"])
            )
            stranded = await _settle(i, agent, (await i.media.submit(agent, str(source["id"]), "subtitle", {}))["id"])
            assert stranded["state"] == "failed"
            assert stranded["error_code"] == "bilibili_configuration_revoked"
            rebound = await i.media.register(agent, str(config["id"]), BV)
            assert rebound["config_revision_id"] != source["config_revision_id"]
            revived = await _settle(i, agent, (await i.media.submit(agent, str(rebound["id"]), "subtitle", {}))["id"])
            assert revived["state"] == "done"
        finally:
            assert (await i.close())["complete"]

    asyncio.run(scenario())


def test_media_service_cancels_running_jobs_and_reconciles_restart(tmp_path):
    async def scenario():
        # Phase one: a running job is cancelled by its owner.
        processor = FakeProcessor()
        processor.hang = True
        i = await _media_instance(tmp_path, None, processor)
        agent, config = await _seed(i)
        source = await i.media.register(agent, str(config["id"]), BV)
        running = await i.media.submit(agent, str(source["id"]), "transcribe", {})
        await _settle_running(i, agent, str(running["id"]))
        cancelled = await i.media.cancel(agent, str(running["id"]))
        assert cancelled["state"] == "cancelled" and cancelled["error_code"] == "media_cancelled"
        assert (await i.close())["complete"]
        source_id = str(source["id"])

        # Phase two: the next process strands jobs at the repository level.
        second = await _media_instance(tmp_path)
        rows = (await second.media.sources(agent))["items"]
        assert rows and str(rows[0]["id"]) == source_id
        queued = await second._database.write(lambda u: u.media_job_create(agent, source_id, "subtitle", None))
        started = await second._database.write(lambda u: u.media_job_create(agent, source_id, "transcribe", {}))
        await second._database.write(lambda u: u.media_job_start(str(started["id"])))
        assert (await second.close())["complete"]

        # Phase three: startup recovery empties them with explicit codes.
        third = await _media_instance(tmp_path)
        try:
            rows = {row["id"]: row for row in (await third.media.jobs(agent, source_id))["items"]}
            reconciled, orphaned = rows[str(queued["id"])], rows[str(started["id"])]
            assert reconciled["state"] == "cancelled"
            assert reconciled["error_code"] == "media_restart_interrupted"
            assert orphaned["state"] == "unknown"
            assert orphaned["error_code"] == "media_restart_unknown"
            assert third.health()["media"]["pending"] == 0
        finally:
            assert (await third.close())["complete"]

    asyncio.run(scenario())


def test_media_service_delete_propagates_and_keeps_object_evidence(tmp_path):
    async def scenario():
        i = await _media_instance(tmp_path)
        try:
            agent, config = await _seed(i)
            source = await i.media.register(agent, str(config["id"]), BV)
            done = await _settle(i, agent, (await i.media.submit(agent, str(source["id"]), "subtitle", {}))["id"])
            assert done["state"] == "done"
            await i.media.delete(agent, str(source["id"]))
            with rejects("media_source_not_found"):
                await i.media.source(agent, str(source["id"]))
            assert await i._database.read(lambda u: u.connection.execute(select(s.media_jobs)).all()) == []
            assert await i._database.read(lambda u: u.connection.execute(select(s.media_transcripts)).all()) == []
            # The published object outlives the source row; evidence GC is owner-driven.
            assert await i._database.read(lambda u: u.connection.execute(select(s.objects)).all())
        finally:
            assert (await i.close())["complete"]

    asyncio.run(scenario())


# ---------- owner HTTP contract ----------


def test_media_http_contract_covers_sources_jobs_artifacts_and_probe(tmp_path):
    with TestClient(
        http_app(
            tmp_path,
            model=NoModel(),
            bilibili_backend=FakeGateway(),
            media_processor=FakeProcessor(),
            credential_backend=Secrets(),
        )
    ) as client:
        assert client.get("/api/v1/agents").status_code == 401
        token = (tmp_path / "run/owner.token").read_text(encoding="utf-8")
        client.headers["Authorization"] = "Bearer " + token
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        base = f"/api/v1/agents/{agent}"
        config = client.post(
            f"{base}/configurations",
            json={"kind": "channel", "name": "Bili fixture", "enabled": True, "value": CHANNEL},
        ).json()

        # Registration validates the target shape before any gateway call.
        assert (
            client.post(f"{base}/media/sources", json={"config_id": config["id"], "target": "AV123"}).status_code == 422
        )
        assert (
            client.post(f"{base}/media/sources", json={"config_id": str(uuid.uuid4()), "target": BV}).status_code == 404
        )
        source = client.post(f"{base}/media/sources", json={"config_id": config["id"], "target": BV})
        assert source.status_code == 201
        source = source.json()
        assert source["bvid"] == BV
        listed = client.get(f"{base}/media/sources").json()
        assert listed["items"][0]["id"] == source["id"] and listed["items"][0]["active_jobs"] == 0

        # Invalid operations and parameters are rejected by the request model.
        assert client.post(f"{base}/media/sources/{source['id']}/jobs", json={"operation": "rip"}).status_code == 422
        assert (
            client.post(
                f"{base}/media/sources/{source['id']}/jobs",
                json={"operation": "frames", "count": 99, "width": 640},
            ).status_code
            == 422
        )

        subtitle = client.post(f"{base}/media/sources/{source['id']}/jobs", json={"operation": "subtitle"}).json()
        subtitle = _wait_done(client, base, subtitle["id"])
        assert subtitle["state"] == "done"

        frames = client.post(
            f"{base}/media/sources/{source['id']}/jobs",
            json={"operation": "frames", "count": 3, "width": 320},
        ).json()
        frames = _wait_done(client, base, frames["id"])
        assert frames["state"] == "done"

        view = client.get(f"{base}/media/sources/{source['id']}").json()
        transcript_id, frames_id = view["latest_transcript_id"], view["latest_frames_id"]

        full = client.get(f"{base}/media/transcripts/{transcript_id}").json()
        assert full["kind"] == "subtitle" and full["segments"]
        window = client.get(f"{base}/media/transcripts/{transcript_id}/window").json()
        assert window["text"] == "第一行字幕\n第二行字幕"
        narrow = client.get(f"{base}/media/transcripts/{transcript_id}/window", params={"start": 4, "length": 4}).json()
        assert narrow["text"] == "幕\n第二" and narrow["start"] == 4 and narrow["end"] == 8

        sheet = client.get(f"{base}/media/frames/{frames_id}").json()
        assert sheet["frame_count"] == 3 and sheet["width"] == 320
        content = client.get(f"{base}/objects/{sheet['object_id']}/content")
        assert content.content.startswith(b"\x89PNG")

        health = client.get(f"{base}/media/processor").json()
        assert "processor" in health and "pending" in health
        probe = client.post(f"{base}/media/processor/probe")
        assert probe.status_code == 200 and probe.json()["components"]["PIL"] is True

        # Cancelling a finished job reports its terminal state honestly.
        cancelled = client.post(f"{base}/media/jobs/{subtitle['id']}/cancel").json()
        assert cancelled["state"] == "done"

        assert client.delete(f"{base}/media/sources/{source['id']}").json() == {"deleted": True}
        missing = client.get(f"{base}/media/sources/{source['id']}")
        assert missing.status_code == 404 and missing.json()["error"]["code"] == "media_source_not_found"
