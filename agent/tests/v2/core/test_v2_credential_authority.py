"""Owner credential authority, unknown side effects and restored epochs; isolated fake stores only."""

import asyncio
import hashlib
import json
import subprocess
import sys
import threading
import uuid

import pytest
from fastapi.testclient import TestClient
from omni_test_support.v2.credentials import Backend, entered
from omni_test_support.v2.v2_configuration_helpers import add_model_headers_column, model_instance
from omni_test_support.v2.v2_delivery_helpers import output, setup
from omni_test_support.v2.v2_legacy_seed import LegacySeedRepositories
from sqlalchemy import create_engine, delete, update

from omni.adapters.persistence import repository_base
from omni.adapters.persistence import schema as s
from omni.adapters.persistence.credential_repository import CredentialRepositories
from omni.adapters.persistence.database import Database
from omni.adapters.persistence.maintenance import backup, restore
from omni.adapters.persistence.migration_config import configuration
from omni.adapters.persistence.migration_config import head as migration_head
from omni.adapters.platform.runtime.paths import InstancePaths
from omni.adapters.primitives import SystemClock, UUIDs
from omni.composition.http import http_app
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError, ModelReply
from omni.domain.history import encode_cursor


async def current(instance):
    return (await instance.credentials.page())["items"][0]


async def review(instance, row, decision="allow_current"):
    return await instance.credentials.review(
        row["id"],
        row["revision"],
        row["authority_epoch"],
        row["fingerprint"],
        row["policy"],
        decision,
        "Verified the isolated store externally; no real account involved",
    )


async def unknown(instance, backend):
    backend.failure = True
    with pytest.raises(BusinessError) as error:
        await instance.credentials.change("fixture/key", "private-authority-canary")
    assert error.value.code == "credential_store_unavailable"
    backend.failure = False
    return await current(instance)


def test_unknown_blocks_model_and_changes_until_explicit_review_without_rewriting_history(tmp_path):
    class Model:
        calls = 0

        async def complete(self, *args):
            self.calls += 1
            return ModelReply("isolated reply", cost_micro=0)

    async def scenario():
        backend, model = Backend(), Model()
        instance = await open_instance(tmp_path, capabilities=(), credential_backend=backend, model=model)
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            profile = await model_instance(instance, agent, credential_ref="fixture/key")
            row = await unknown(instance, backend)
            assert row["state"] == "unknown" and row["current_operation"]["state"] == "unknown"
            diagnosis = await instance.configurations.diagnose(agent, profile["id"])
            assert not diagnosis["available"] and diagnosis["state"] == "credential_requires_review"
            assert "execution_token" not in str(row) and "private-authority-canary" not in str(row)
            # A configuration save, an Agent change and registration cannot reset instance owner authority.
            await model_instance(instance, agent, credential_ref="fixture/key")
            other = await instance._database.write(lambda u: u.create_agent("Other"))
            await model_instance(instance, other["id"], credential_ref="fixture/key")
            assert (await instance.credentials.register("fixture/key"))["state"] == "unknown"
            before = list(backend.events)
            with pytest.raises(BusinessError) as failure:
                await instance.credentials.change("fixture/key", None)
            assert failure.value.code == "credential_requires_review" and backend.events == before
            for identity in (agent, other["id"]):
                turn = await instance.application.submit_turn(identity, None, "blocked", "input")
                async with asyncio.timeout(10):
                    while (await instance.application.get_run(identity, turn["run_id"]))["state"] in {
                        "queued",
                        "running",
                    }:
                        await asyncio.sleep(0.01)
                assert (await instance.application.get_run(identity, turn["run_id"]))["state"] == "failed"
                assert await instance.application.query("calls", identity) == []
            assert model.calls == 0
            allowed = await review(instance, await current(instance))
            assert allowed["state"] == "ready"
            assert allowed["current_operation"]["state"] == "unknown"
            assert len((await instance.credentials.history(row["id"]))["items"]) == 1
            assert len((await instance.credentials.history(row["id"], reviews=True))["items"]) == 1
            assert (await instance.operations.status())["persisted"]["unresolved_credential_changes"] == 0
            await instance._database.read(lambda u: u.credential_authorize("fixture/key"))
            await instance.credentials.change("fixture/key", "new-value")
            assert (await current(instance))["state"] == "ready"
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


@pytest.mark.parametrize("decision", ["allow_current", "revoke"])
def test_review_compare_and_swap_is_single_use_and_terminal_observation_is_immutable(tmp_path, decision):
    async def scenario():
        backend = Backend()
        instance = await open_instance(tmp_path, capabilities=(), credential_backend=backend)
        try:
            row = await unknown(instance, backend)
            result = await asyncio.gather(
                review(instance, row, decision), review(instance, row, decision), return_exceptions=True
            )
            assert sum(isinstance(value, BusinessError) for value in result) == 1
            assert (
                next(value for value in result if isinstance(value, BusinessError)).code == "credential_review_changed"
            )
            assert (await current(instance))["state"] == ("ready" if decision == "allow_current" else "revoked")
            assert (await instance.credentials.register("fixture/key"))["state"] == (
                "ready" if decision == "allow_current" else "revoked"
            )
            op = row["current_operation"]
            with pytest.raises(BusinessError, match="constraint"):
                await instance._database.write(
                    lambda u: u.connection.execute(
                        update(s.credential_operations)
                        .where(s.credential_operations.c.id == op["id"])
                        .values(state="applied", revision=s.credential_operations.c.revision + 1)
                    )
                )
            for operation in (update(s.credential_reviews).values(evidence="overwrite"), delete(s.credential_reviews)):
                with pytest.raises(BusinessError, match="constraint"):
                    await instance._database.write(lambda u: u.connection.execute(operation))
            if decision == "revoke":
                with pytest.raises(BusinessError) as error:
                    await instance._database.read(lambda u: u.credential_authorize("fixture/key"))
                assert error.value.code == "credential_revoked"
            assert (await instance._database.integrity())["foreign_keys"] == 0
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_active_native_mutation_cannot_be_reviewed_or_recovered_by_live_process(tmp_path):
    async def scenario():
        backend = Backend()
        backend.block = "set"
        instance = await open_instance(tmp_path, capabilities=(), credential_backend=backend)
        task = asyncio.create_task(instance.credentials.change("fixture/key", "changed"))
        try:
            await entered(backend)
            row = await current(instance)
            assert row["state"] == "changing" and row["current_operation"]["state"] == "running"
            with pytest.raises(BusinessError) as failure:
                await review(instance, row)
            assert failure.value.code == "credential_changing"
            assert await instance._database.write(lambda u: u.credential_recover()) == 0
            with pytest.raises(BusinessError) as failure:
                await instance._database.read(lambda u: u.credential_authorize("fixture/key"))
            assert failure.value.code == "credential_changing"
            backend.release.set()
            await task
            with pytest.raises(BusinessError) as failure:
                await review(instance, row)
            assert failure.value.code == "credential_review_changed"
            assert (await current(instance))["state"] == "ready"
        finally:
            backend.release.set()
            await asyncio.gather(task, return_exceptions=True)
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


@pytest.mark.parametrize("dispatched", [False, True])
def test_normal_restart_classifies_prepared_and_running_without_native_replay(tmp_path, dispatched):
    async def scenario():
        backend = Backend()
        instance = await open_instance(tmp_path, capabilities=(), credential_backend=backend)
        operation = await instance._database.write(
            lambda u: u.credential_begin("fixture/key", "set", str(uuid.uuid4()))
        )
        if dispatched:
            await instance._database.write(
                lambda u: u.credential_dispatch(operation["id"], operation["execution_token"])
            )
        assert (await instance.close())["complete"]
        restarted = await open_instance(tmp_path, capabilities=(), credential_backend=backend)
        try:
            row = await current(restarted)
            assert row["state"] == ("unknown" if dispatched else "ready")
            assert row["current_operation"]["state"] == ("unknown" if dispatched else "not_applied")
            assert backend.events == []
            with pytest.raises(BusinessError) as failure:
                await restarted._database.write(
                    lambda u: u.credential_settle(operation["id"], operation["execution_token"], "applied")
                )
            assert failure.value.code == "credential_operation_fenced"
            assert await restarted._database.write(lambda u: u.credential_recover()) == 0
        finally:
            assert (await restarted.close())["complete"]

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["approval", "claim", "start"])
def test_delivery_rechecks_credential_authority_at_each_boundary(tmp_path, stage):
    async def scenario():
        db, agent, channel, _clock, port, service = await setup(tmp_path)
        try:
            _, message = await output(db, agent)
            row = await service.create(agent, message["id"], channel["id"], "target", "credential-gate")
            if stage != "approval":
                row = await service.decide(agent, row["id"], True, row["revision"], row["fingerprint"])
            if stage == "start":
                claim = await db.write(lambda u: u.delivery_claim(agent, row["id"], port.identity, "account-one"))
                assert claim is not None
            # Deliberately omit service revocation to prove each repository boundary has its own gate.
            operation = await db.write(lambda u: u.credential_begin("fixture/key", "set", str(uuid.uuid4())))
            await db.write(lambda u: u.credential_dispatch(operation["id"], operation["execution_token"]))
            await db.write(lambda u: u.credential_settle(operation["id"], operation["execution_token"], "unknown"))
            if stage == "approval":
                with pytest.raises(BusinessError) as failure:
                    await service.decide(agent, row["id"], True, row["revision"], row["fingerprint"])
                assert failure.value.code == "credential_requires_review"
            elif stage == "start":
                assert (
                    await db.write(
                        lambda u: u.delivery_start(agent, claim["id"], claim["token"], port.identity, "account-one")
                    )
                    is None
                )
                assert (await service.get(agent, row["id"]))["state"] == "blocked"
            else:
                await service.tick()
                async with asyncio.timeout(10):
                    while service._active:
                        await asyncio.sleep(0.01)
                assert (await service.get(agent, row["id"]))["state"] == "blocked"
            assert port.calls == []
        finally:
            await service.close()
            await db.close()

    asyncio.run(scenario())


def test_restore_invalidates_ready_authority_previous_review_and_owner_tokens(tmp_path):
    home, target, snapshot = tmp_path / "home", tmp_path / "restored", tmp_path / "snapshot"

    async def seed():
        backend = Backend()
        instance = await open_instance(home, capabilities=(), credential_backend=backend)
        row = await review(instance, await unknown(instance, backend))
        await instance._database.write(lambda u: u.issue_token("old-token-hash", "2099-01-01T00:00:00+00:00"))
        assert (await instance.close())["complete"]
        return row

    old = asyncio.run(seed())
    backup(InstancePaths.resolve(home), snapshot)
    report = restore(snapshot, target)
    assert report["quarantine"]["revoked_owner_tokens"] >= 1
    assert report["quarantine"]["unverified_credentials"] == 1
    assert restore(snapshot, target)["already_restored"]

    async def check():
        backend = Backend()
        instance = await open_instance(target, capabilities=(), credential_backend=backend)
        try:
            row = await current(instance)
            assert row["state"] == "unknown" and row["authority_epoch"] == old["authority_epoch"] + 1
            assert not await instance._database.read(lambda u: u.token_valid("old-token-hash"))
            assert len((await instance.credentials.history(row["id"], reviews=True))["items"]) == 1
            with pytest.raises(BusinessError) as failure:
                await review(instance, old)
            assert failure.value.code == "credential_review_changed"
            assert (await review(instance, row))["state"] == "ready"
            assert len((await instance.credentials.history(row["id"], reviews=True))["items"]) == 2
            assert backend.events == []
            assert (await instance._database.integrity())["foreign_keys"] == 0
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(check())


def test_authority_http_is_owner_only_bounded_and_versioned_without_secret_or_execution_token(tmp_path):
    backend = Backend()
    backend.failure = True
    with TestClient(http_app(tmp_path, credential_backend=backend)) as client:
        assert client.get("/api/v1/credentials").status_code == 401
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text().strip()
        response = client.put(
            "/api/v1/credentials", params={"reference": "fixture/key"}, json={"value": "private-http-canary"}
        )
        assert response.status_code == 503
        backend.failure = False
        row = client.get("/api/v1/credentials").json()["items"][0]
        assert row["state"] == "unknown" and "execution_token" not in str(row)
        body = {key: row[key] for key in ("revision", "authority_epoch", "fingerprint", "policy")}
        body.update(decision="allow_current", evidence="Verified the fake key store")
        assert (
            client.post(f"/api/v1/credentials/{row['id']}/review", json={**body, "revision": True}).status_code == 422
        )
        assert (
            client.post(
                f"/api/v1/credentials/{row['id']}/review", json={**body, "value": "private-http-canary"}
            ).status_code
            == 422
        )
        assert client.post(f"/api/v1/credentials/{row['id']}/review", json=body).json()["state"] == "ready"
        assert client.post(f"/api/v1/credentials/{row['id']}/review", json=body).status_code == 409
        for endpoint in ("/credentials", f"/credentials/{row['id']}/operations", f"/credentials/{row['id']}/reviews"):
            assert client.get("/api/v1" + endpoint, params={"limit": 201}).status_code == 422
            assert client.get("/api/v1" + endpoint, params={"after": "not-a-uuid"}).status_code == 422
        assert client.delete("/api/v1/credentials", params={"reference": "fixture/key"}).status_code == 200
        assert (
            client.post("/api/v1/credentials/register", json={"reference": "fixture/key"}).json()["state"] == "revoked"
        )
        history = client.get(f"/api/v1/credentials/{row['id']}/operations").json()
        assert len(history["items"]) == 2
        assert "execution_token" not in str(history) and "private-http-canary" not in str(history)


def test_review_and_revocation_rollback_together_when_audit_write_fails(tmp_path, monkeypatch):
    async def scenario():
        backend = Backend()
        instance = await open_instance(tmp_path, capabilities=(), credential_backend=backend)
        try:
            row = await unknown(instance, backend)
            original = CredentialRepositories._credential_audit

            def fail(self, identity, kind, *args):
                if kind == "credential.reviewed":
                    raise BusinessError("audit_unavailable", "Injected audit failure")
                return original(self, identity, kind, *args)

            with monkeypatch.context() as patch:
                patch.setattr(CredentialRepositories, "_credential_audit", fail)
                with pytest.raises(BusinessError):
                    await review(instance, row)
            assert (await current(instance))["fingerprint"] == row["fingerprint"]
            assert (await instance.credentials.history(row["id"], reviews=True))["items"] == []
            assert (await review(instance, row))["state"] == "ready"
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_cancelled_review_remains_owned_until_the_atomic_commit_finishes(tmp_path, monkeypatch):
    async def scenario():
        backend = Backend()
        instance = await open_instance(tmp_path, capabilities=(), credential_backend=backend)
        started, release = threading.Event(), threading.Event()
        original = CredentialRepositories._credential_audit

        def block(self, identity, kind, *args):
            if kind == "credential.reviewed":
                started.set()
                assert release.wait(10)
            return original(self, identity, kind, *args)

        try:
            row = await unknown(instance, backend)
            with monkeypatch.context() as patch:
                patch.setattr(CredentialRepositories, "_credential_audit", block)
                task = asyncio.create_task(review(instance, row))
                async with asyncio.timeout(10):
                    while not started.is_set():
                        await asyncio.sleep(0.01)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert instance.credentials.health()["pending_changes"] == 1
                assert not (await instance.close(0.02))["complete"]
                release.set()
                assert (await instance.close(10))["complete"]
        finally:
            release.set()
            await instance.close()
        restarted = await open_instance(tmp_path, capabilities=(), credential_backend=backend)
        try:
            assert (await current(restarted))["state"] == "ready"
            assert len((await restarted.credentials.history(row["id"], reviews=True))["items"]) == 1
        finally:
            assert (await restarted.close())["complete"]

    asyncio.run(scenario())


def test_mutations_and_reviews_share_one_bounded_owned_queue(tmp_path):
    async def scenario():
        backend = Backend()
        backend.block = "set"
        instance = await open_instance(tmp_path, capabilities=(), credential_backend=backend)
        tasks = [asyncio.create_task(instance.credentials.change(f"fixture/{index}", "isolated")) for index in range(4)]
        try:
            await entered(backend)
            assert instance.credentials.health()["pending_changes"] == 4
            with pytest.raises(BusinessError) as failure:
                await instance.credentials.review(
                    str(uuid.uuid4()), 1, 1, "a" * 64, "credential-authority-v1", "revoke", "checked"
                )
            assert failure.value.code == "credential_busy"
            backend.release.set()
            await asyncio.gather(*tasks)
            assert backend.events == ["set"] * 4
            assert {row["state"] for row in (await instance.credentials.page())["items"]} == {"ready"}
        finally:
            backend.release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


@pytest.mark.parametrize("cut", ["before_commit", "prepared", "running", "after_native"])
def test_process_death_preserves_atomic_intent_and_never_replays_ambiguous_native_change(tmp_path, cut):
    async def seed():
        instance = await open_instance(tmp_path, capabilities=(), credential_backend=Backend())
        await instance.credentials.register("fixture/key")
        assert (await instance.close())["complete"]

    asyncio.run(seed())
    code = """
import asyncio,os,sys
from pathlib import Path
from omni.composition.instance import open_instance
from omni.adapters.persistence.credential_repository import CredentialRepositories
home=Path(sys.argv[1]); cut=sys.argv[2]
original=CredentialRepositories.credential_begin
def begin(self,*args,**kwargs):
    result=original(self,*args,**kwargs)
    os._exit(93)
def dispatch(*args): os._exit(93)
if cut=='before_commit': CredentialRepositories.credential_begin=begin
if cut=='prepared': CredentialRepositories.credential_dispatch=dispatch
class Native:
    def set(self,ref,value):
        if cut=='after_native': (home/'fixture-native-marker').write_text('applied',encoding='utf-8')
        os._exit(93)
async def main():
    instance=await open_instance(home,capabilities=(),credential_backend=Native())
    await instance.credentials.change('fixture/key','isolated-value')
    raise AssertionError('Cut did not occur')
asyncio.run(main())
"""
    process = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path), cut], capture_output=True, text=True, timeout=30, check=False
    )
    assert process.returncode == 93, process.stderr
    assert (tmp_path / "fixture-native-marker").exists() == (cut == "after_native")

    async def recover():
        backend = Backend()
        instance = await open_instance(tmp_path, capabilities=(), credential_backend=backend)
        try:
            row = await current(instance)
            assert row["state"] == ("ready" if cut in {"before_commit", "prepared"} else "unknown")
            history = (await instance.credentials.history(row["id"]))["items"]
            assert len(history) == (0 if cut == "before_commit" else 1)
            if history:
                assert history[0]["state"] == ("not_applied" if cut == "prepared" else "unknown")
            assert backend.events == []
            assert (await instance._database.integrity())["foreign_keys"] == 0
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(recover())


@pytest.mark.parametrize("interrupted", [False, True])
def test_v9_adoption_quarantines_config_and_hash_only_intents_preserving_audit_cursor(
    tmp_path, monkeypatch, interrupted
):
    from alembic import command

    paths = InstancePaths.resolve(tmp_path)
    paths.initialize()
    engine = create_engine("sqlite+pysqlite:///" + str(paths.database))
    ids, clock = UUIDs(), SystemClock()
    operation = ids.new()
    cfg = configuration()
    with engine.begin() as connection, monkeypatch.context() as legacy_history:
        # Pre-v14 audit rows used SQLite's INTEGER PRIMARY KEY allocation.
        legacy_history.setattr(repository_base, "allocate_history", lambda *args, **kwargs: None)
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "v2_0009")
        add_model_headers_column(connection)
        u = LegacySeedRepositories(connection, clock, ids, ids.new())
        u.initialize()
        agent = u.query("agents", "")[0]["id"]
        with monkeypatch.context() as patch:
            # Build exactly the v9 typed rows without introducing v10 registration.
            patch.setattr(CredentialRepositories, "credential_register", lambda *args, **kwargs: {})
            u.configuration_create(
                agent, "model", "legacy", True, {"model": "fixture", "credential_ref": "legacy/model"}
            )
        owner = connection.exec_driver_sql("SELECT owner_id FROM instances").scalar_one()
        fingerprint = hashlib.sha256(b"legacy/hash-only").hexdigest()
        for kind, outcome in (("credential.requested", "requested"), ("credential.resolved", "applied")):
            connection.exec_driver_sql(
                "INSERT INTO instance_audit(id,created_at,owner_id,operation_id,kind,outcome,data) VALUES(?,?,?,?,?,?,?)",
                (
                    ids.new(),
                    clock.now(),
                    owner,
                    operation,
                    kind,
                    outcome,
                    json.dumps({"reference_sha256": fingerprint, "operation": "set", "boot_id": ids.new()}),
                ),
            )
        connection.exec_driver_sql("UPDATE sqlite_sequence SET seq=900 WHERE name='instance_audit'")
    engine.dispose()
    if interrupted:
        code = """
import os,sys
from pathlib import Path
from omni.adapters.persistence.database import Database
from omni.adapters.primitives import SystemClock,UUIDs
from sqlalchemy import event,Engine
@event.listens_for(Engine,'after_cursor_execute')
def cut(connection,cursor,statement,parameters,context,executemany):
    if statement.lstrip().upper().startswith('INSERT INTO CREDENTIAL_OPERATIONS'): os._exit(93)
Database(Path(sys.argv[1]),SystemClock(),UUIDs(),objects_root=Path(sys.argv[2]))
"""
        process = subprocess.run(
            [sys.executable, "-c", code, str(paths.database), str(paths.objects)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert process.returncode == 93, process.stderr
        from omni.adapters.persistence.sqlite_access import sqlite_connection

        with sqlite_connection(paths.database) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "v2_0009"
            assert connection.execute("SELECT count(*) FROM instance_audit").fetchone()[0] == 2
            assert (
                connection.execute("SELECT count(*) FROM sqlite_master WHERE name='credential_references'").fetchone()[
                    0
                ]
                == 0
            )

    async def upgraded():
        db = Database(paths.database, clock, ids, objects_root=paths.objects)
        try:
            assert db.schema_version == migration_head()
            rows = await db.read(lambda u: u.credential_page())
            assert len(rows) == 2 and {row["state"] for row in rows} == {"unknown"}
            hashed = next(row for row in rows if row["reference"] is None)
            assert hashed["current_operation"]["state"] == "applied" and hashed["current_operation"]["legacy"]
            with pytest.raises(BusinessError) as failure:
                await db.write(
                    lambda u: u.credential_review(
                        hashed["id"],
                        hashed["revision"],
                        hashed["authority_epoch"],
                        hashed["fingerprint"],
                        hashed["policy"],
                        "allow_current",
                        "checked",
                    )
                )
            assert failure.value.code == "credential_reference_unbound"
            bound = await db.write(lambda u: u.credential_register("legacy/hash-only"))
            assert bound["id"] == hashed["id"] and bound["state"] == "unknown"

            def legacy_watermark(unit):
                epoch = unit.connection.exec_driver_sql("SELECT epoch FROM history_identity").scalar_one()
                instance_id = unit.connection.exec_driver_sql("SELECT id FROM instances").scalar_one()
                return encode_cursor(epoch, "instance_audit", instance_id, 0, 900)

            cursor = await db.read(legacy_watermark)
            audit = (await db.read(lambda u: u.history_page("instance_audit", cursor=cursor)))["items"]
            assert len(audit) == 1 and audit[0]["sequence"] == 901
            assert (await db.integrity())["foreign_keys"] == 0
            assert list((paths.database.parent / "schema-backups").glob("*/manifest.json"))
        finally:
            await db.close()

    asyncio.run(upgraded())
