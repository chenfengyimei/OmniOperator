"""Owner HTTP consent, view scope and cancelled requests use real application storage."""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from v2_desktop_helpers import FixtureDesktopBackend

from omni.composition.http import http_app


def owner(client, home):
    client.headers["Authorization"] = "Bearer " + (home / "run/owner.token").read_text()
    agent = client.get("/api/v1/agents").json()["items"][0]["id"]
    return agent, f"/api/v1/agents/{agent}/desktop", client.app.state.runtime.application._db


def begin(client, base, view_id=None):
    body = {"consent": True, "view_id": view_id or str(uuid.uuid4())}
    response = client.post(base + "/view", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["view_id"] == body["view_id"]
    return body


def listing(client, base, body):
    response = client.post(base + "/targets", json={**body, "limit": 10})
    assert response.status_code == 200, response.text
    return response.json()


def test_owner_endpoints_are_opt_in_metadata_only_and_no_raw_target_or_control_route_exists(tmp_path):
    home, backend = tmp_path / "home", FixtureDesktopBackend()
    with TestClient(http_app(home, desktop_backend=backend)) as client:
        assert client.get(f"/api/v1/agents/{uuid.uuid4()}/desktop").status_code == 401
        _agent, base, _db = owner(client, home)
        assert client.get(base).json()["capabilities"] is None
        assert not backend.calls
        assert client.post(base + "/probe", json={"consent": True}).status_code == 422
        body = begin(client, base)
        assert client.post(base + "/probe", json={**body, "consent": False}).status_code == 422
        assert client.post(base + "/probe", json=body).status_code == 200
        snapshot = listing(client, base, body)
        assert set(snapshot["items"][0]) == {"target_id", "application", "pid", "backend", "can_observe", "can_capture"}
        assert snapshot["items"][0]["can_observe"] and snapshot["items"][0]["can_capture"]
        query = {**body, "snapshot_id": snapshot["snapshot_id"], "target_id": snapshot["items"][0]["target_id"]}
        assert client.post(base + "/verify", json={**query, "target": {}}).status_code == 422
        checked = client.post(base + "/verify", json=query)
        assert checked.status_code == 200, checked.text
        assert checked.json()["matches"] and checked.json()["observation_only"]
        for forbidden in ("native_id", "process_created", "session_id", "window_text"):
            assert forbidden not in checked.json()
        assert client.post(base + "/click", json=body).status_code == 404
        assert client.post(base + "/screen-capture", json=body).status_code == 404
        assert client.delete(base + "/snapshot").status_code == 422
        assert client.delete(base + "/snapshot", params={"view_id": body["view_id"]}).json()["forgotten"]
        assert client.get(base).json()["snapshot_id"] is None
        assert [name for name, _ in backend.calls] == ["probe", "targets", "verify"]
        assert client.get(f"/api/v1/agents/{uuid.uuid4()}/desktop").status_code == 404
        assert client.get(base).json()["owner_only"] and not client.get(base).json()["model_actions"]


def test_agents_snapshots_and_tabs_reject_stale_view_without_deleting_new_owner_state(tmp_path):
    home, backend = tmp_path / "home", FixtureDesktopBackend()
    with TestClient(http_app(home, desktop_backend=backend)) as client:
        _agent, base, db = owner(client, home)
        first_owner = begin(client, base)
        first = listing(client, base, first_owner)
        other = client.portal.call(db.write, lambda u: u.create_agent("Independent"))["id"]
        other_base = f"/api/v1/agents/{other}/desktop"
        other_owner = begin(client, other_base)
        foreign = {**other_owner, "snapshot_id": first["snapshot_id"], "target_id": first["items"][0]["target_id"]}
        assert client.post(other_base + "/verify", json=foreign).status_code == 410
        fresh_owner = begin(client, base)
        fresh = listing(client, base, fresh_owner)
        assert not client.delete(base + "/snapshot", params={"view_id": first_owner["view_id"]}).json()["forgotten"]
        assert client.get(base).json()["snapshot_id"] == fresh["snapshot_id"]
        assert client.post(base + "/probe", json=first_owner).status_code == 409
        assert client.post(base + "/view", json=fresh_owner).status_code == 200
        assert client.get(base).json()["snapshot_id"] == fresh["snapshot_id"]
        backend.match = False
        mismatch = client.post(
            base + "/verify",
            json={**fresh_owner, "snapshot_id": fresh["snapshot_id"], "target_id": fresh["items"][0]["target_id"]},
        )
        assert mismatch.status_code == 200 and not mismatch.json()["matches"]
        assert mismatch.json()["reason"] == "desktop_target_changed"
        assert client.get(base).json()["snapshot_targets"] == 0


def test_cancelled_http_waiter_keeps_actual_observation_until_durable_completion(tmp_path):
    home, backend = tmp_path / "home", FixtureDesktopBackend()
    with TestClient(http_app(home, desktop_backend=backend)) as client:
        agent, base, db = owner(client, home)
        body = begin(client, base)
        service = client.app.state.runtime.desktop
        backend.hold("probe", after_dispatch=True)

        async def scenario():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=client.app),
                base_url="http://testserver",
                headers={"Authorization": client.headers["Authorization"]},
            ) as transport:
                waiting = asyncio.create_task(transport.post(base + "/probe", json=body))
                await asyncio.wait_for(backend.dispatched["probe"].wait(), 5)
                waiting.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await waiting
                assert service.health()["pending"] == 1
                assert backend.health()["pending"] == 1
                backend.release("probe", after_dispatch=True)
                async with asyncio.timeout(5):
                    while service.health()["pending"]:
                        await asyncio.sleep(0.001)
                assert (await service.status(agent))["capabilities"] is not None
                rows = await db.read(lambda u: u.query("audit", agent))
                completed = [row for row in rows if row["kind"] == "desktop.observation_completed"]
                assert len(completed) == 1 and completed[0]["data"]["operation"] == "probe"
                assert [name for name, _ in backend.calls] == ["probe"]

        try:
            client.portal.call(scenario)
        finally:
            backend.release("probe", after_dispatch=True)
