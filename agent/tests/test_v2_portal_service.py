"""Real scoped HTTP, audits and lifecycle with actual Portal helper protocol over isolated injected peers."""

import asyncio
import json
import sys
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_v2_desktop import begin
from test_v2_desktop_http import begin as http_begin
from test_v2_desktop_http import owner
from v2_desktop_helpers import FixtureDesktopBackend
from v2_portal_helpers import Spawner

from omni.adapters.persistence.repository import Repositories
from omni.adapters.platform import portal as host
from omni.composition.http import http_app
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError


@asynccontextmanager
async def prepared(tmp_path, monkeypatch, *, hold=False, fault=None):
    monkeypatch.setattr(host, "sys", SimpleNamespace(platform="linux", executable=sys.executable))
    spawn = Spawner(hold=hold, fault=fault)
    backend = host.PortalBackend(process_factory=spawn)
    instance = await open_instance(
        tmp_path / "home", capabilities=(), desktop_backend=FixtureDesktopBackend(), portal_backend=backend
    )
    agent = (await instance.application.query("agents", ""))[0]["id"]
    try:
        yield instance, spawn, backend, agent
    finally:
        assert (await instance.close())["complete"]


async def settled(service, agent, view, wanted):
    async with asyncio.timeout(5):
        while True:
            result = await service.status(agent, view["view_id"])
            grant = result["grant"]
            if grant is not None and grant["phase"] == wanted:
                return grant
            await asyncio.sleep(0)


def test_authorization_requires_audit_and_view_detach_releases_actual_helper(tmp_path, monkeypatch):
    async def scenario():
        async with prepared(tmp_path, monkeypatch) as (instance, spawn, backend, agent):
            view = await begin(instance.desktop, agent)
            grant = await instance.desktop_portal.open(agent, view)
            active = await settled(instance.desktop_portal, agent, view, "active")
            assert active["authorized"] and not active["input_authorized"] and not active["capture_available"]
            audits = await instance._database.read(lambda u: u.query("audit", agent))
            own = [a for a in audits if a["operation_id"] == grant["grant_id"]]
            assert {a["kind"] for a in own} >= {"desktop.portal_requested", "desktop.portal_authorized"}
            assert "private-stream-canary" not in json.dumps(own) and "/org/freedesktop/portal" not in json.dumps(own)
            await instance.desktop.forget(agent, view["view_id"])
            assert all(o.reclaimed for o in spawn.owners) and backend.health()["resident"] == 0
            with pytest.raises(BusinessError):
                await instance.desktop_portal.status(agent, view["view_id"])
        for p in (tmp_path / "home").rglob("*.jsonl"):
            assert "private-stream-canary" not in p.read_text(encoding="utf-8")

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["desktop.portal_requested", "desktop.portal_authorized"])
def test_failed_audit_never_exposes_permission_and_closes_helper(tmp_path, monkeypatch, stage):
    original = Repositories.diagnostic_audit

    def audit(self, agent, operation, kind, data):
        if kind == stage:
            raise BusinessError("audit_unavailable", "private-audit-canary", status=503)
        return original(self, agent, operation, kind, data)

    monkeypatch.setattr(Repositories, "diagnostic_audit", audit)

    async def scenario():
        async with prepared(tmp_path, monkeypatch) as (instance, spawn, backend, agent):
            view = await begin(instance.desktop, agent)
            if stage == "desktop.portal_requested":
                with pytest.raises(BusinessError) as failed:
                    await instance.desktop_portal.open(agent, view)
                assert failed.value.code == "portal_audit_failed" and "private" not in str(failed.value)
                assert (
                    not spawn.calls and (await instance.desktop_portal.status(agent, view["view_id"]))["grant"] is None
                )
            else:
                await instance.desktop_portal.open(agent, view)
                failed = await settled(instance.desktop_portal, agent, view, "failed")
                assert not failed["authorized"] and failed["error_code"] == "portal_audit_failed"
                async with asyncio.timeout(5):
                    while not all(o.reclaimed for o in spawn.owners):
                        await asyncio.sleep(0)
            assert backend.health()["resident"] == 0

    asyncio.run(scenario())


def test_explicit_reprobe_and_new_view_revoke_previous_share(tmp_path, monkeypatch):
    async def scenario():
        async with prepared(tmp_path, monkeypatch, hold=True) as (instance, spawn, backend, agent):
            old = await begin(instance.desktop, agent)
            await instance.desktop_portal.open(agent, old)
            await settled(instance.desktop_portal, agent, old, "authorizing")
            new = await begin(instance.desktop, agent)
            assert spawn.owners[0].reclaimed
            await instance.desktop_portal.open(agent, new)
            await settled(instance.desktop_portal, agent, new, "authorizing")
            await instance.desktop.probe(agent, new)
            assert all(o.reclaimed for o in spawn.owners) and not backend.health()["resident"]

    asyncio.run(scenario())


def test_http_requires_owner_exact_view_and_grant_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(host, "sys", SimpleNamespace(platform="linux", executable=sys.executable))
    spawn = Spawner(hold=True)
    backend = host.PortalBackend(process_factory=spawn)
    home = tmp_path / "home"
    with TestClient(http_app(home, desktop_backend=FixtureDesktopBackend(), portal_backend=backend)) as client:
        assert client.post(f"/api/v1/agents/{uuid.uuid4()}/desktop/portal", json={}).status_code == 401
        _agent, base, db = owner(client, home)
        view = http_begin(client, base)
        path = base + "/portal"
        for extra in ({"consent": False}, {"session_handle": "/other"}, {"types": 1}, {"restore_token": "private"}):
            assert client.post(path, json={**view, **extra}).status_code == 422
        response = client.post(path, json=view)
        assert response.status_code == 202, response.text
        grant = response.json()
        assert grant["capture_available"] is False and grant["input_authorized"] is False
        assert (
            client.delete(path, params={"view_id": view["view_id"], "grant_id": str(uuid.uuid4())}).status_code == 409
        )
        other = (client.portal.call(db.write, lambda u: u.create_agent("other")))["id"]
        assert (
            client.get(f"/api/v1/agents/{other}/desktop/portal", params={"view_id": view["view_id"]}).status_code == 409
        )
        assert (
            client.delete(path, params={"view_id": view["view_id"], "grant_id": grant["grant_id"]}).status_code == 200
        )
        assert all(o.reclaimed for o in spawn.owners)


def test_evicted_desktop_view_releases_its_owned_portal_session(tmp_path, monkeypatch):
    async def scenario():
        async with prepared(tmp_path, monkeypatch) as (instance, spawn, backend, agent):
            view = await begin(instance.desktop, agent)
            await instance.desktop_portal.open(agent, view)
            await settled(instance.desktop_portal, agent, view, "active")
            for index in range(8):
                other = (await instance._database.write(lambda u: u.create_agent("view-" + str(index))))["id"]
                await begin(instance.desktop, other)
            async with asyncio.timeout(5):
                while not spawn.owners[0].reclaimed:
                    await asyncio.sleep(0)
            assert not backend.health()["resident"]
            with pytest.raises(BusinessError):
                await instance.desktop_portal.status(agent, view["view_id"])

    asyncio.run(scenario())


def test_cancelled_admission_waiter_does_not_discard_real_spawn_or_allow_late_dialog(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.setattr(host, "sys", SimpleNamespace(platform="linux", executable=sys.executable))
        entered, release = asyncio.Event(), asyncio.Event()
        spawn = Spawner()

        async def slow(*args, **kwargs):
            entered.set()
            await release.wait()
            return await spawn(*args, **kwargs)

        backend = host.PortalBackend(process_factory=slow)
        instance = await open_instance(
            tmp_path / "home", capabilities=(), desktop_backend=FixtureDesktopBackend(), portal_backend=backend
        )
        agent = (await instance.application.query("agents", ""))[0]["id"]
        view = await begin(instance.desktop, agent)
        caller = asyncio.create_task(instance.desktop_portal.open(agent, view))
        detached = None
        try:
            await entered.wait()
            caller.cancel()
            await asyncio.gather(caller, return_exceptions=True)
            assert instance.desktop_portal.health()["pending"] > 0
            detached = asyncio.create_task(instance.desktop.forget(agent, view["view_id"]))
            # forget first performs an async Agent lookup. Observe the actual
            # revocation boundary before releasing a real pending spawn.
            async with asyncio.timeout(5):
                while True:
                    try:
                        await instance.desktop.require_view(agent, view["view_id"])
                    except BusinessError as changed:
                        assert changed.code == "desktop_view_changed"
                        break
            release.set()
            await detached
            async with asyncio.timeout(5):
                while instance.desktop_portal.health()["pending"]:
                    await asyncio.sleep(0)
            assert all(o.reclaimed and not o.process.stdin.frames for o in spawn.owners)
        finally:
            release.set()
            assert (await instance.close())["complete"]
            if detached is not None:
                await asyncio.gather(detached, return_exceptions=True)

    asyncio.run(scenario())


def test_shutdown_retries_retained_reclaim_after_view_already_detached(tmp_path, monkeypatch):
    async def scenario():
        async with prepared(tmp_path, monkeypatch) as (instance, spawn, backend, agent):
            view = await begin(instance.desktop, agent)
            await instance.desktop_portal.open(agent, view)
            await settled(instance.desktop_portal, agent, view, "active")
            spawn.owners[0].fail_cleanup = True
            with pytest.raises(BusinessError, match="still owned"):
                await instance.desktop.forget(agent, view["view_id"])
            assert backend.health()["cleanup_pending"] == 1
            assert not (await instance.close(0.1))["complete"]
            assert (await instance._database.read(lambda u: u.query("agents", "")))[0]["id"] == agent
            spawn.owners[0].fail_cleanup = False
            assert (await instance.close(6))["complete"]
            assert spawn.owners[0].reclaimed and backend.health()["resident"] == 0

    asyncio.run(scenario())
