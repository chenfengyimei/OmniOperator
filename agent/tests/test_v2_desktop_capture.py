"""Capture receipts, scoped durable references and revocation at external I/O boundaries."""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import threading
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import insert, inspect, select, update
from test_v2_desktop import begin, listed, no_pending, prepared, verification
from test_v2_desktop_backend import Owner, encoded, factory
from test_v2_desktop_http import begin as http_begin
from test_v2_desktop_http import listing, owner
from test_v2_objects import Clock, opened
from v2_desktop_helpers import FixtureDesktopBackend, captured_frame, target_identity

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.maintenance import backup, restore
from omni.adapters.persistence.migration_config import head as migration_head
from omni.adapters.persistence.object_inventory import registry
from omni.adapters.platform import desktop_capture as files
from omni.adapters.platform.desktop import DesktopBackend
from omni.adapters.platform.paths import InstancePaths
from omni.composition.http import http_app
from omni.domain.contracts import BusinessError
from omni.domain.desktop import VERSION, parse_owner, parse_request, parse_response


def receipt(frame=None):
    frame = frame or captured_frame()
    return {
        "target": target_identity(),
        "width": frame.width,
        "height": frame.height,
        "bytes": len(frame.content),
        "sha256": frame.sha256,
        "method": frame.method,
    }


def test_capture_contracts_do_not_accept_client_paths_or_unbound_receipts():
    body = {
        "consent": True,
        **{key: str(uuid.uuid4()) for key in ("view_id", "snapshot_id", "target_id", "session_id")},
    }
    assert parse_owner("capture", body) == body
    with pytest.raises(BusinessError):
        parse_owner("capture", {**body, "output_path": "private.png"})
    request = parse_request(
        {
            "version": VERSION,
            "request_id": str(uuid.uuid4()),
            "operation": "capture",
            "target": target_identity(),
            "limit": 1,
            "output_path": "host-selected-file",
        }
    )
    reply = {
        "version": VERSION,
        "request_id": request["request_id"],
        "operation": "capture",
        "ok": True,
        "result": receipt(),
        "error_code": None,
    }
    assert parse_response(reply, request)["result"] == receipt()
    for field, changed in (
        ("bytes", 16777217),
        ("width", True),
        ("height", 9999),
        ("sha256", "not-a-digest"),
        ("method", "full_desktop"),
    ):
        with pytest.raises(BusinessError):
            parse_response({**reply, "result": {**receipt(), field: changed}}, request)
    with pytest.raises(BusinessError):
        parse_response({**reply, "result": {**receipt(), "target": {**target_identity(), "pid": 1}}}, request)


def test_capture_file_verifies_fingerprint_png_and_refuses_uniform_or_linked_files(tmp_path):
    root = tmp_path / "captures"
    path = files.prepare(root, str(uuid.uuid4()))
    frame = captured_frame()
    path.write_bytes(frame.content)
    assert files.read(root, path, receipt()) == frame
    linked = tmp_path / "foreign-link"
    os.link(path, linked)
    with pytest.raises(BusinessError):
        files.read(root, path, receipt())
    with pytest.raises(BusinessError):
        files.discard(root, path)
    assert linked.read_bytes() == frame.content
    linked.unlink()
    with pytest.raises(BusinessError):
        files.read(root, path, {**receipt(), "sha256": "0" * 64})
    image = Image.new("RGB", (32, 24), "black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    content = buffer.getvalue()
    path.write_bytes(content)
    with pytest.raises(BusinessError, match="Uniform"):
        files.read(root, path, {**receipt(), "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)})
    path.write_bytes(b"x" * len(frame.content))
    with pytest.raises(BusinessError):
        files.read(root, path, receipt())
    files.discard(root, path)
    assert not list(root.iterdir())


def helper_response(request):
    from pathlib import Path

    Path(request["output_path"]).write_bytes(captured_frame().content)
    return encoded(request, receipt())


def test_capture_file_worker_stays_owned_after_waiter_cancellation(tmp_path, monkeypatch):
    import sys

    if sys.platform != "win32":
        # Native command selection is Windows-only; file contracts are tested on every platform above.
        return
    entered, release = threading.Event(), threading.Event()
    original = files.read

    def blocked(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(files, "read", blocked)

    async def scenario():
        backend = DesktopBackend(process_factory=factory(Owner(builder=helper_response)), capture_root=tmp_path)
        task = asyncio.create_task(backend.capture(target_identity()))
        try:
            async with asyncio.timeout(5):
                while not entered.is_set():
                    await asyncio.sleep(0.001)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not await backend.close(0.01)
            assert backend.health()["resident"] == 1
        finally:
            release.set()
            assert await backend.close(5)
            assert not list(tmp_path.iterdir())

    asyncio.run(scenario())


def test_capture_cleanup_failure_is_visible_and_retried_without_losing_owner(tmp_path, monkeypatch):
    import sys

    if sys.platform != "win32":
        return
    original = files.discard

    def denied(*_args):
        raise PermissionError("private-path-canary")

    monkeypatch.setattr(files, "discard", denied)

    async def scenario():
        backend = DesktopBackend(process_factory=factory(Owner(builder=helper_response)), capture_root=tmp_path)
        try:
            with pytest.raises(BusinessError) as error:
                await backend.capture(target_identity())
            assert error.value.code == "desktop_cleanup_pending"
            assert "private-path-canary" not in str(error.value)
            assert backend.health()["cleanup_pending"] == 1
            assert not await backend.close(0.1)
            monkeypatch.setattr(files, "discard", original)
        finally:
            assert await backend.close(5)
        assert not list(tmp_path.iterdir())

    asyncio.run(scenario())


def test_abandoned_staging_inventory_is_bounded_and_does_not_remove_foreign_files(tmp_path):
    for i in range(102):
        (tmp_path / str(i)).mkdir()
    report = files.inventory(tmp_path, {"0"})
    assert report == {"residual_count": 100, "truncated": True, "error_code": "desktop_staging_residual"}
    assert len(list(tmp_path.iterdir())) == 102


def test_unwritable_staging_is_visible_in_capability_report(tmp_path):
    root = tmp_path / "not-a-directory"
    root.write_text("unrelated content", encoding="utf-8")

    async def scenario():
        backend = DesktopBackend(process_factory=factory(Owner()), capture_root=root)
        try:
            report = await backend.probe()
            capability = next(item for item in report["capabilities"] if item["id"] == "screen_capture")
            assert not capability["available"] and capability["error_code"] == "desktop_staging_unavailable"
            assert backend.health()["capture_staging"]["error_code"] == "desktop_staging_unavailable"
        finally:
            assert await backend.close()
        assert root.read_text(encoding="utf-8") == "unrelated content"

    asyncio.run(scenario())


async def session(instance, agent, key="capture-session"):
    return await instance._database.write(lambda u: u.accept_turn(agent, None, key, "test fixture message", {}))


def test_capture_publication_links_real_object_audit_and_release_is_scoped(tmp_path):
    async def scenario():
        async with prepared(tmp_path) as (instance, backend, clock, agent):
            service = instance.desktop
            turn = await session(instance, agent)
            view = await begin(service, agent)
            snapshot = await listed(service, agent, view)
            result = await service.capture(agent, {**verification(view, snapshot), "session_id": turn["session_id"]})
            assert result["attached_to_model"] is False and result["observation_only"]
            metadata, content = await instance.objects.get(agent, result["object_id"])
            assert content == captured_frame().content and metadata["references"] == 1
            clock.advance(86401)
            with pytest.raises(BusinessError) as error:
                await instance._database.write(lambda u: u.object_remove(agent, metadata["id"], metadata["revision"]))
            assert error.value.code == "object_protected"
            other = (await instance._database.write(lambda u: u.create_agent("other")))["id"]
            with pytest.raises(BusinessError):
                await instance.objects.captures(other, turn["session_id"])
            wrong_session = await session(instance, agent, "wrong-session")
            with pytest.raises(BusinessError):
                await instance.objects.release_capture(agent, wrong_session["session_id"], result["id"])
            records = await instance.objects.captures(agent, turn["session_id"])
            assert len(records["items"]) == 1
            audit = await instance._database.read(
                lambda u: list(
                    u.connection.execute(
                        select(s.audit.c.kind).where(s.audit.c.operation_id == result["operation_id"])
                    ).scalars()
                )
            )
            assert "desktop.capture_published" in audit
            await instance.objects.release_capture(agent, turn["session_id"], result["id"])
            assert (await instance.objects.describe(agent, result["object_id"]))["references"] == 0
            await instance._database.write(lambda u: u.object_remove(agent, metadata["id"], metadata["revision"]))
            assert len([call for call in backend.calls if call[0] == "capture"]) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["view", "erasure", "expiry", "target", "audit"])
def test_capture_drops_late_native_result_without_publishing(tmp_path, monkeypatch, change):
    async def scenario():
        async with prepared(tmp_path) as (instance, backend, clock, agent):
            service = instance.desktop
            turn = await session(instance, agent)
            view = await begin(service, agent)
            snapshot = await listed(service, agent, view)
            backend.hold("capture", after_dispatch=True)
            if change == "target":
                backend.errors["capture"] = BusinessError("desktop_target_changed", "fixture", status=409)
            task = asyncio.create_task(
                service.capture(agent, {**verification(view, snapshot), "session_id": turn["session_id"]})
            )
            await asyncio.wait_for(backend.dispatched["capture"].wait(), 5)
            if change == "view":
                await service.forget(agent, view["view_id"])
            elif change == "expiry":
                clock.advance(61)
            elif change == "erasure":
                await instance._database.write(
                    lambda u: u.erase_message(agent, u.get_run(agent, turn["run_id"])["input_message_id"])
                )
            elif change == "audit":
                from omni.adapters.persistence.repository import Repositories

                original = Repositories.audit

                def failing(self, agent_id, kind, operation_id, data):
                    if kind == "desktop.capture_published":
                        raise BusinessError("audit_failed", "fixture", status=503)
                    return original(self, agent_id, kind, operation_id, data)

                monkeypatch.setattr(Repositories, "audit", failing)
            backend.release("capture", after_dispatch=True)
            with pytest.raises(BusinessError):
                await task
            assert (await instance.objects.captures(agent, turn["session_id"]))["items"] == []
            assert (await instance.objects.page(agent))["items"] == []

    asyncio.run(scenario())


def test_capture_cancelled_http_waiter_keeps_actual_work_owned_until_settlement(tmp_path):
    async def scenario():
        async with prepared(tmp_path) as (instance, backend, _clock, agent):
            turn = await session(instance, agent)
            view = await begin(instance.desktop, agent)
            snapshot = await listed(instance.desktop, agent, view)
            backend.hold("capture", after_dispatch=True)
            task = asyncio.create_task(
                instance.desktop.capture(agent, {**verification(view, snapshot), "session_id": turn["session_id"]})
            )
            await asyncio.wait_for(backend.dispatched["capture"].wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert instance.desktop.health()["pending"] == 1
            backend.release("capture", after_dispatch=True)
            await no_pending(instance.desktop)
            assert len((await instance.objects.captures(agent, turn["session_id"]))["items"]) == 1

    asyncio.run(scenario())


def test_http_capture_requires_owner_session_and_opaque_target(tmp_path):
    backend = FixtureDesktopBackend()
    with TestClient(http_app(tmp_path / "home", desktop_backend=backend)) as client:
        agent, base, db = owner(client, tmp_path / "home")
        turn = client.portal.call(db.write, lambda u: u.accept_turn(agent, None, "capture", "fixture", {}))
        view = http_begin(client, base)
        snap = listing(client, base, view)
        query = {**verification(view, snap), "session_id": turn["session_id"]}
        assert client.post(base + "/capture", json={**query, "output_path": "arbitrary"}).status_code == 422
        response = client.post(base + "/capture", json=query)
        assert response.status_code == 200, response.text
        value = response.json()
        path = f"/api/v1/agents/{agent}/sessions/{turn['session_id']}/captures"
        assert len(client.get(path).json()["items"]) == 1
        assert client.delete(path + "/" + value["id"]).status_code == 204
        assert client.get(path).json()["items"] == []
        assert "no-store" in response.headers["cache-control"]


def test_capture_schema_scope_immutability_and_verified_snapshot_restore(tmp_path):
    async def seed():
        async with prepared(tmp_path) as (instance, _backend, _clock, agent):
            turn = await session(instance, agent)
            view = await begin(instance.desktop, agent)
            snap = await listed(instance.desktop, agent, view)
            capture = await instance.desktop.capture(
                agent, {**verification(view, snap), "session_id": turn["session_id"]}
            )
            db = instance._database
            assert db.schema_version == migration_head() and len(s.metadata.tables) == 125

            def check(unit):
                reflection = inspect(unit.connection)
                assert {
                    tuple(item["constrained_columns"]) for item in reflection.get_foreign_keys("session_captures")
                } >= {("object_id", "agent_id"), ("session_id", "agent_id")}
                assert (s.session_captures, s.session_captures.c.object_id) in s.OBJECT_REFERENCES
                return dict(unit.connection.execute(select(s.session_captures)).mappings().one())

            row = await db.read(check)
            other = (await db.write(lambda u: u.create_agent("other")))["id"]
            with pytest.raises(BusinessError) as constraint:
                await db.write(
                    lambda u: u.connection.execute(
                        insert(s.session_captures).values(**{**row, "id": str(uuid.uuid4()), "agent_id": other})
                    )
                )
            assert constraint.value.code == "data_constraint"
            with pytest.raises(BusinessError) as constraint:
                await db.write(lambda u: u.connection.execute(update(s.session_captures).values(width=1)))
            assert constraint.value.code == "data_constraint"
            return agent, capture

    agent, capture = asyncio.run(seed())
    paths = InstancePaths.resolve(tmp_path / "home")
    assert capture["object_id"] in registry(paths.database)[3]
    snapshot, restored = tmp_path / "backup", tmp_path / "restored"
    manifest = backup(paths, snapshot)
    assert len(manifest["files"]) == 2
    assert restore(snapshot, restored)["database"]["integrity"] == "ok"

    async def verify():
        async with opened(restored, Clock()) as (_, db, service, _agent):
            assert (await service.captures(agent, capture["session_id"]))["items"][0]["id"] == capture["id"]
            assert (await service.get(agent, capture["object_id"]))[1] == captured_frame().content
            await db.write(
                lambda u: u.connection.execute(
                    update(s.sessions).where(s.sessions.c.id == capture["session_id"]).values(deleted_at=u.clock.now())
                )
            )
            assert (await service.describe(agent, capture["object_id"]))["references"] == 0

    asyncio.run(verify())
