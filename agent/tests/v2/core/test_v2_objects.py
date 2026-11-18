"""Object durability, isolation, real references, exclusive GC and snapshot recovery."""

from omni_test_support.v2.objects import (
    SQL as SQL,
)
from omni_test_support.v2.objects import (
    ActionResult as ActionResult,
)
from omni_test_support.v2.objects import (
    Blob as Blob,
)
from omni_test_support.v2.objects import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.objects import (
    Clock as Clock,
)
from omni_test_support.v2.objects import (
    Database as Database,
)
from omni_test_support.v2.objects import (
    FileObjects as FileObjects,
)
from omni_test_support.v2.objects import (
    InstanceLock as InstanceLock,
)
from omni_test_support.v2.objects import (
    InstancePaths as InstancePaths,
)
from omni_test_support.v2.objects import (
    ObjectFiles as ObjectFiles,
)
from omni_test_support.v2.objects import (
    ObjectService as ObjectService,
)
from omni_test_support.v2.objects import (
    Outcome as Outcome,
)
from omni_test_support.v2.objects import (
    Path as Path,
)
from omni_test_support.v2.objects import (
    SystemClock as SystemClock,
)
from omni_test_support.v2.objects import (
    ToolSpec as ToolSpec,
)
from omni_test_support.v2.objects import (
    UUIDs as UUIDs,
)
from omni_test_support.v2.objects import (
    apply_collection as apply_collection,
)
from omni_test_support.v2.objects import (
    asynccontextmanager as asynccontextmanager,
)
from omni_test_support.v2.objects import (
    asyncio as asyncio,
)
from omni_test_support.v2.objects import (
    backup as backup,
)
from omni_test_support.v2.objects import (
    dt as dt,
)
from omni_test_support.v2.objects import (
    insert as insert,
)
from omni_test_support.v2.objects import (
    json as json,
)
from omni_test_support.v2.objects import (
    migration_head as migration_head,
)
from omni_test_support.v2.objects import (
    object_report as object_report,
)
from omni_test_support.v2.objects import (
    object_store as object_store,
)
from omni_test_support.v2.objects import (
    opened as opened,
)
from omni_test_support.v2.objects import (
    os as os,
)
from omni_test_support.v2.objects import (
    plan_collection as plan_collection,
)
from omni_test_support.v2.objects import (
    pytest as pytest,
)
from omni_test_support.v2.objects import (
    registry as registry,
)
from omni_test_support.v2.objects import (
    restore as restore,
)
from omni_test_support.v2.objects import (
    s as s,
)
from omni_test_support.v2.objects import (
    sqlite3 as sqlite3,
)
from omni_test_support.v2.objects import (
    subprocess as subprocess,
)
from omni_test_support.v2.objects import (
    sys as sys,
)
from omni_test_support.v2.objects import (
    uuid as uuid,
)


def test_publish_dedup_scope_revision_and_pages(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (paths, db, service, agent):
            first = await service.publish(agent, b"original", "text/plain")
            duplicate = await service.publish(agent, b"original", "application/octet-stream")
            assert first["id"] == duplicate["id"] and duplicate["media_type"] == "text/plain"
            other = (await db.write(lambda u: u.create_agent("Second")))["id"]
            independent = await service.publish(other, b"original", "text/plain")
            assert independent["id"] != first["id"]
            with pytest.raises(BusinessError) as error:
                await service.get(other, first["id"])
            assert error.value.status == 404
            pin = await service.pin(agent, first["id"], True, 1)
            assert pin["pinned"] and pin["revision"] == 2
            with pytest.raises(BusinessError, match="changed"):
                await service.pin(agent, first["id"], False, 1)
            assert (await service.get(agent, first["id"]))[1] == b"original"
            for content in (b"two", b"three"):
                await service.publish(agent, content, "text/plain")
            page = await service.page(agent, limit=2)
            following = await service.page(agent, after=page["next_after"], limit=2)
            assert len(page["items"]) == 2 and len(following["items"]) == 1
            assert len({row["id"] for row in page["items"] + following["items"]}) == 3
            blob = Blob(agent, first["sha256"], first["bytes"])
            assert (paths.objects / blob.key).read_bytes() == b"original"
            assert len(list(paths.objects.rglob("*.partial"))) == 0

    asyncio.run(scenario())


def test_file_publication_failure_never_registers_success(tmp_path, monkeypatch):
    def failed_move(*args):
        raise OSError("injected publication failure")

    monkeypatch.setattr(object_store, "publish_file", failed_move)

    async def scenario():
        async with opened(tmp_path, Clock()) as (paths, _db, service, agent):
            with pytest.raises(BusinessError) as error:
                await service.publish(agent, b"private-payload", "text/plain")
            assert error.value.code == "object_io_failed"
            assert (await service.page(agent))["items"] == []
            assert not list(paths.objects.rglob("*.blob"))
            assert not list(paths.objects.rglob("*.partial"))

    asyncio.run(scenario())


def test_request_cancellation_drains_publication_before_storage_close(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (paths, db, _service, agent):
            published, release = asyncio.Event(), asyncio.Event()
            files = ObjectFiles(paths.objects)

            class DelayedFiles:
                async def put(self, agent_id, content):
                    blob = await files.put(agent_id, content)
                    published.set()
                    await release.wait()
                    return blob

                async def read(self, blob):
                    return await files.read(blob)

                async def close(self):
                    await files.close()

            service = ObjectService(db, DelayedFiles(), Clock())
            caller = asyncio.create_task(service.publish(agent, b"durable", "text/plain"))
            try:
                async with asyncio.timeout(10):
                    await published.wait()
                caller.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await caller
            finally:
                release.set()
                assert await service.close()
            rows = await db.read(lambda u: u.object_page(agent, "", 100))
            assert len(rows) == 1
            assert FileObjects(paths.objects).read(Blob(agent, rows[0]["sha256"], rows[0]["bytes"])) == b"durable"

    asyncio.run(scenario())


def test_gc_protects_pins_and_references_and_is_idempotent(tmp_path):
    clock = Clock()

    async def prepare():
        async with opened(tmp_path, clock) as (paths, db, service, agent):
            unreferenced = await service.publish(agent, b"expired", "text/plain")
            pinned = await service.publish(agent, b"fixed", "text/plain")
            reference = await service.publish(agent, b"source", "text/plain")
            await service.pin(agent, pinned["id"], True, 1)
            turn = await db.write(lambda u: u.accept_turn(agent, None, "one", "message", {}))
            message = (await db.read(lambda u: u.messages(agent, turn["session_id"])))[0]
            link = await db.write(lambda u: u.object_link(agent, reference["id"], "message", message["id"]))
            assert link == await db.write(lambda u: u.object_link(agent, reference["id"], "message", message["id"]))
            other = (await db.write(lambda u: u.create_agent("Other")))["id"]
            with pytest.raises(BusinessError):
                await db.write(lambda u: u.object_link(other, reference["id"], "message", message["id"]))
            with pytest.raises(BusinessError, match="constraint"):
                await db.write(
                    lambda u: u.connection.execute(
                        insert(s.message_parts).values(
                            id=str(uuid.uuid4()),
                            agent_id=other,
                            created_at=clock.now(),
                            message_id=message["id"],
                            object_id=reference["id"],
                            position=3,
                            kind="object",
                        )
                    )
                )
            with pytest.raises(BusinessError) as error:
                plan_collection(paths)
            assert error.value.code == "instance_busy"
            return paths, unreferenced, pinned, reference

    paths, expired, pinned, reference = asyncio.run(prepare())
    clock.current += dt.timedelta(days=2)
    plan = plan_collection(paths, now=clock.current)
    assert [row["object"]["id"] for row in plan["entries"]] == [expired["id"]]
    assert apply_collection(paths, plan, now=clock.current)["complete"]
    repeated = apply_collection(paths, plan, now=clock.current)
    assert repeated["complete"] and repeated["resumed"] and repeated["unlinked_files"] == 0
    remaining = registry(paths.database)[2]
    assert {row["id"] for row in remaining} == {pinned["id"], reference["id"]}
    assert len(list(paths.objects.rglob("*.blob"))) == 2


@pytest.mark.parametrize("change", ["pin", "reference", "fingerprint"])
def test_gc_revalidates_every_target_before_mutation(tmp_path, change):
    clock = Clock()

    async def prepare():
        async with opened(tmp_path, clock) as (paths, _db, service, agent):
            return (
                paths,
                agent,
                await service.publish(agent, b"one", "text/plain"),
                await service.publish(agent, b"two", "text/plain"),
            )

    paths, agent, one, two = asyncio.run(prepare())
    clock.current += dt.timedelta(days=2)
    plan = plan_collection(paths, now=clock.current)

    async def mutate():
        async with opened(tmp_path, clock) as (_, db, service, _):
            if change == "pin":
                await service.pin(agent, one["id"], True, 1)
            elif change == "reference":
                turn = await db.write(lambda u: u.accept_turn(agent, None, "ref", "message", {}))
                message = (await db.read(lambda u: u.messages(agent, turn["session_id"])))[0]
                await db.write(lambda u: u.object_link(agent, one["id"], "message", message["id"]))
            else:
                (paths.objects / Blob(agent, one["sha256"], one["bytes"]).key).write_bytes(b"bad")

    asyncio.run(mutate())
    with pytest.raises(BusinessError) as error:
        apply_collection(paths, plan, now=clock.current)
    assert error.value.code == "stale_gc_plan"
    assert {row["id"] for row in registry(paths.database)[2]} == {one["id"], two["id"]}
    assert (paths.objects / Blob(agent, two["sha256"], two["bytes"]).key).read_bytes() == b"two"


def test_gc_process_crash_after_commit_can_resume(tmp_path):
    clock = Clock()
    home = tmp_path / "home"

    async def prepare():
        async with opened(home, clock) as (paths, _db, service, agent):
            await service.publish(agent, b"unreferenced", "text/plain")
            return paths

    paths = asyncio.run(prepare())
    clock.current += dt.timedelta(days=2)
    plan = plan_collection(paths, now=clock.current)
    plan_path = tmp_path / "gc.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    code = """
import datetime as dt,json,os,sys
from pathlib import Path
from omni.adapters.persistence import object_gc
from omni.adapters.platform.runtime.paths import InstancePaths
original=object_gc.durable_json
def stop_after_commit(path,value):
    if value.get('phase')=='metadata_committed':
        os._exit(79)
    original(path,value)
object_gc.durable_json=stop_after_commit
object_gc.apply_collection(InstancePaths.resolve(Path(sys.argv[1])),json.loads(Path(sys.argv[2]).read_text(encoding='utf-8')),now=dt.datetime.fromisoformat(sys.argv[3]))
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(home), str(plan_path), clock.now()],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 79, result.stderr
    assert registry(paths.database)[2] == [] and len(list(paths.objects.rglob("*.blob"))) == 1
    resumed = apply_collection(paths, plan, now=clock.current)
    assert resumed["complete"] and resumed["resumed"] and resumed["unlinked_files"] == 1


def test_gc_reports_unlink_failure_and_retries_without_losing_protection(tmp_path, monkeypatch):
    clock = Clock()

    async def prepare():
        async with opened(tmp_path, clock) as (paths, _db, service, agent):
            row = await service.publish(agent, b"garbage", "text/plain")
            return paths, paths.objects / Blob(agent, row["sha256"], row["bytes"]).key

    paths, blob = asyncio.run(prepare())
    clock.current += dt.timedelta(days=2)
    plan = plan_collection(paths, now=clock.current)
    original = Path.unlink

    def blocked(path, *args, **kwargs):
        if path == blob:
            raise PermissionError("injected occupied file")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", blocked)
        report = apply_collection(paths, plan, now=clock.current)
    assert not report["complete"] and report["residual"][0]["error_code"] == "object_unlink_failed"
    assert blob.exists() and registry(paths.database)[2] == []
    assert object_report(paths)["pending_collection"][0]["plan_id"] == plan["plan_id"]
    assert apply_collection(paths, plan, now=clock.current)["complete"] and not blob.exists()
    assert object_report(paths)["pending_collection"] == []


def test_backup_restores_registered_objects_and_rejects_missing_or_tampered_content(tmp_path):
    home = tmp_path / "home"
    clock = Clock()

    async def prepare():
        async with opened(home, clock) as (paths, _db, service, agent):
            row = await service.publish(agent, b"source payload", "text/plain")
            await _db.write(lambda u: u.issue_token("f" * 64, "2050-01-01T00:00:00+00:00"))
            FileObjects(paths.objects).put(agent, b"orphan payload")
            return paths, agent, row

    paths, agent, row = asyncio.run(prepare())
    snapshot = tmp_path / "snapshot"
    manifest = backup(paths, snapshot)
    assert len(manifest["files"]) == 2
    target = tmp_path / "restored"
    assert restore(snapshot, target)["database"]["integrity"] == "ok"
    assert restore(snapshot, target)["already_restored"]

    async def read():
        async with opened(target, clock) as (_, _db, service, _):
            assert (await service.get(agent, row["id"]))[1] == b"source payload"
            assert not await _db.read(lambda u: u.token_valid("f" * 64))

    asyncio.run(read())
    blob = Blob(agent, row["sha256"], row["bytes"])
    (paths.objects / blob.key).unlink()
    with pytest.raises(BusinessError, match="missing"):
        backup(paths, tmp_path / "incomplete")
    assert not (tmp_path / "incomplete/manifest.json").exists()
    (snapshot / "objects" / blob.key).write_bytes(b"tampered")
    import hashlib

    for entry in manifest["files"]:
        if entry["path"].startswith("objects/"):
            entry.update(bytes=8, sha256=hashlib.sha256(b"tampered").hexdigest())
    (snapshot / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(BusinessError) as error:
        restore(snapshot, tmp_path / "tampered-restore")
    assert error.value.code == "object_integrity_failed" and not (tmp_path / "tampered-restore").exists()


def test_hardlinks_and_directory_links_are_rejected(tmp_path):
    root, outside = tmp_path / "objects", tmp_path / "outside"
    outside.mkdir()
    agent = str(uuid.uuid4())
    files = FileObjects(root)
    blob = files.put(agent, b"original")
    os.link(root / blob.key, outside / "linked")
    with pytest.raises(BusinessError, match="regular file"):
        files.read(blob)
    (outside / "linked").unlink()
    linked_agent = root / str(uuid.uuid4())
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(linked_agent), str(outside)], capture_output=True, timeout=10, check=False
        )
        assert result.returncode == 0, result.stderr
    else:
        linked_agent.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(BusinessError, match="links"):
            files.put(linked_agent.name, b"never outside")
        assert [path.name for path in outside.iterdir()] == []
    finally:
        linked_agent.rmdir() if os.name == "nt" else linked_agent.unlink()


def test_schema_upgrade_is_preceded_by_a_validated_snapshot(tmp_path):
    paths = InstancePaths.resolve(tmp_path)
    paths.initialize()
    with sqlite3.connect(paths.database) as connection:
        for statement in SQL:
            connection.execute(statement)
        connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
        connection.execute("INSERT INTO alembic_version VALUES ('v2_0001')")

    async def upgrade():
        database = Database(paths.database, Clock(), UUIDs(), objects_root=paths.objects)
        try:
            assert database.schema_version == migration_head()
            assert (await database.integrity())["integrity"] == "ok"
        finally:
            await database.close()

    asyncio.run(upgrade())
    snapshots = list(paths.database.parent.glob("schema-backups/*/manifest.json"))
    assert len(snapshots) == 1
    report = json.loads(snapshots[0].read_text(encoding="utf-8"))
    assert report["complete"] and report["database"]["schema"] == "v2_0001"


def test_gc_orphan_grace_and_unrelated_files(tmp_path):
    clock = Clock()

    async def prepare():
        async with opened(tmp_path, clock) as (paths, _db, _service, agent):
            blob = FileObjects(paths.objects).put(agent, b"interrupted registration")
            partial = paths.objects / agent / blob.sha256[:2] / f".{uuid.uuid4()}.partial"
            partial.write_bytes(b"unfinished upload")
            unrelated = paths.objects / "user-notes.txt"
            unrelated.write_text("not owned by collection", encoding="utf-8")
            return paths, partial, unrelated

    paths, partial, unrelated = asyncio.run(prepare())
    assert plan_collection(paths, now=clock.current)["entries"] == []
    clock.current += dt.timedelta(days=2)
    plan = plan_collection(paths, now=clock.current)
    assert {row["reason"] for row in plan["entries"]} == {"unregistered_blob", "interrupted_write"}
    assert apply_collection(paths, plan, now=clock.current)["complete"]
    assert not partial.exists() and not list(paths.objects.rglob("*.blob"))
    assert unrelated.read_text(encoding="utf-8") == "not owned by collection"


def test_settled_action_artifacts_have_real_scoped_references(tmp_path):
    clock = Clock()

    async def scenario():
        async with opened(tmp_path, clock) as (paths, db, service, agent):
            row = await service.publish(agent, b"tool artifact", "text/plain")
            turn = await db.write(lambda u: u.accept_turn(agent, None, "artifact", "make an artifact", {}))
            await db.write(lambda u: u.transition(agent, turn["run_id"], "running"))
            action = await db.write(
                lambda u: u.prepare_action(agent, turn["run_id"], ToolSpec("read", "test", {}), {}, 1)
            )
            await db.write(lambda u: u.authorize_action(agent, action["id"]))
            await db.write(
                lambda u: u.settle_action(agent, action["id"], ActionResult(Outcome.OK, "done", artifacts=(row["id"],)))
            )
            assert (await service.describe(agent, row["id"]))["references"] == 1
            return paths

    paths = asyncio.run(scenario())
    clock.current += dt.timedelta(days=2)
    assert plan_collection(paths, now=clock.current)["entries"] == []


def test_publisher_process_crash_before_registration_leaves_collectible_orphan(tmp_path):
    home = tmp_path / "home"
    code = """
import asyncio,os,sys
from pathlib import Path
from omni.composition.instance import open_instance
from omni.adapters.persistence.object_repository import ObjectRepositories
def stop_before_register(*args,**kwargs):
    os._exit(78)
ObjectRepositories.object_register=stop_before_register
async def run():
    instance=await open_instance(Path(sys.argv[1]))
    agent=(await instance.application.query('agents',''))[0]['id']
    await instance.objects.publish(agent,b'committed physical bytes','text/plain')
asyncio.run(run())
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(home)], capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 78, result.stderr
    paths = InstancePaths.resolve(home)
    assert registry(paths.database)[2] == [] and len(list(paths.objects.rglob("*.blob"))) == 1
    future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2)
    plan = plan_collection(paths, now=future)
    assert plan["entries"][0]["reason"] == "unregistered_blob"
    assert apply_collection(paths, plan, now=future)["complete"]


def test_cli_doctor_and_collection_workflow_are_offline_and_repeatable(tmp_path):
    home = tmp_path / "home"
    command = [sys.executable, "-m", "omni.composition.cli", "--home", str(home)]

    def run(*args):
        return subprocess.run(
            [*command, *args], capture_output=True, text=True, encoding="utf-8", timeout=30, check=False
        )

    initialized = run("init")
    assert initialized.returncode == 0, initialized.stderr
    doctor = run("doctor")
    assert doctor.returncode == 0 and json.loads(doctor.stdout)["objects"]["ok"]
    plan = tmp_path / "collection.json"
    assert run("objects", "gc-plan", str(plan)).returncode == 0
    applied = run("objects", "gc-apply", str(plan))
    assert applied.returncode == 0 and json.loads(applied.stdout)["complete"]
    assert run("objects", "gc-apply", str(plan)).returncode == 0
    assert run("objects", "check").returncode == 0
    plan.write_text("{broken", encoding="utf-8")
    invalid = run("objects", "gc-apply", str(plan))
    assert invalid.returncode == 1 and json.loads(invalid.stderr)["error"] == "invalid_gc_plan"


def test_reimport_after_interrupted_collection_invalidates_old_deletion_intent(tmp_path, monkeypatch):
    clock = Clock()

    async def prepare():
        async with opened(tmp_path, clock) as (paths, _db, service, agent):
            row = await service.publish(agent, b"reimported", "text/plain")
            return paths, row

    paths, original_row = asyncio.run(prepare())
    clock.current += dt.timedelta(days=2)
    plan = plan_collection(paths, now=clock.current)
    blob = paths.objects / Blob(original_row["agent_id"], original_row["sha256"], original_row["bytes"]).key
    unlink = Path.unlink

    def occupied(path, *args, **kwargs):
        if path == blob:
            raise PermissionError("injected occupied object")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", occupied)
        assert not apply_collection(paths, plan, now=clock.current)["complete"]

    async def republish():
        async with opened(tmp_path, clock) as (_paths, _db, service, agent):
            row = await service.publish(agent, b"reimported", "text/plain", pinned=True)
            assert row["id"] != original_row["id"]
            return row

    new_row = asyncio.run(republish())
    with pytest.raises(BusinessError) as error:
        apply_collection(paths, plan, now=clock.current)
    assert error.value.code == "stale_gc_plan"
    assert blob.read_bytes() == b"reimported"
    assert registry(paths.database)[2][0]["id"] == new_row["id"]
