"""Bounded UI content, explicit owner access and revocation across actual waits."""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from test_v2_desktop import begin, listed, no_pending, prepared, verification
from test_v2_desktop_http import begin as http_begin
from test_v2_desktop_http import listing, owner
from v2_desktop_helpers import FixtureDesktopBackend, target_identity

from omni.composition.http import http_app
from omni.domain.contracts import BusinessError
from omni.domain.desktop import VERSION, parse_request, parse_response, validate_observation


def observed():
    return {
        "target": target_identity(),
        "truncated": False,
        "nodes": [
            {
                "id": 1,
                "parent": None,
                "role": "ControlType.Window",
                "name": "private-ui-canary",
                "text": "line one\n第二行",
                "password": False,
                "enabled": True,
                "offscreen": False,
                "bounds": [-100, 0, 640, 480],
            }
        ],
    }


def test_observation_response_has_bound_identity_and_does_not_accept_action_fields():
    request = parse_request(
        {
            "version": VERSION,
            "request_id": str(uuid.uuid4()),
            "operation": "observe",
            "target": target_identity(),
            "limit": 1,
        }
    )
    response = {
        "version": VERSION,
        "request_id": request["request_id"],
        "operation": "observe",
        "ok": True,
        "result": observed(),
        "error_code": None,
    }
    assert parse_response(response, request)["result"] == observed()
    response["result"]["target"]["process_created"] += 1
    with pytest.raises(BusinessError):
        parse_response(response, request)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda v: v["nodes"].clear(),
        lambda v: v["nodes"][0].update(password=True),
        lambda v: v["nodes"][0].update(name="x" * 513),
        lambda v: v["nodes"][0].update(text="\ud800"),
        lambda v: v["nodes"][0].update(text="\0secret"),
        lambda v: v["nodes"][0].update(parent=1),
        lambda v: v["nodes"][0].update(bounds=[True, 0, 1, 1]),
        lambda v: v["nodes"][0].update(bounds=[0, 0, -1, 1]),
        lambda v: v["nodes"][0].update(bounds=[10**1000, 0, 1, 1]),
        lambda v: v["nodes"][0].update(bounds=[float("nan"), 0, 1, 1]),
        lambda v: v["nodes"][0].update(approve=True),
    ],
)
def test_observation_rejects_malformed_or_protected_content(mutation):
    value = observed()
    mutation(value)
    with pytest.raises(BusinessError) as error:
        validate_observation(value, target_identity())
    assert error.value.code == "desktop_invalid"


def test_observation_enforces_depth_count_and_utf8_budget():
    value = observed()
    node = value["nodes"][0]
    value["nodes"] = [{**node, "id": i, "parent": i - 1 if i > 1 else None} for i in range(1, 10)]
    with pytest.raises(BusinessError):
        validate_observation(value, target_identity())
    value["nodes"] = [{**node, "id": i, "parent": 1 if i > 1 else None} for i in range(1, 130)]
    with pytest.raises(BusinessError):
        validate_observation(value, target_identity())
    value["nodes"] = [{**node, "name": "中" * 512, "id": i, "parent": 1 if i > 1 else None} for i in range(1, 12)]
    with pytest.raises(BusinessError):
        validate_observation(value, target_identity())


def test_http_observation_returns_opaque_nodes_without_persisting_ui_text(tmp_path):
    backend = FixtureDesktopBackend()
    with TestClient(http_app(tmp_path / "home", desktop_backend=backend)) as client:
        agent, base, db = owner(client, tmp_path / "home")
        view = http_begin(client, base)
        snapshot = listing(client, base, view)
        query = verification(view, snapshot)
        assert client.post(base + "/observe", json={**query, "consent": False}).status_code == 422
        assert client.post(base + "/observe", json={**query, "native_id": "123"}).status_code == 422
        response = client.post(base + "/observe", json=query)
        assert response.status_code == 200, response.text
        value = response.json()
        assert value["nodes"][0]["name"] == "<fixture-private-ui>"
        assert value["observation_only"] and uuid.UUID(value["observation_id"])
        assert uuid.UUID(value["nodes"][0]["node_id"]) and value["nodes"][0]["parent_id"] is None
        assert not {"target", "native_id", "process_created"}.intersection(value)
        audits = client.portal.call(db.read, lambda u: u.query("audit", agent))
        assert "fixture-ui-body" not in json.dumps(audits) and "fixture-private-ui" not in json.dumps(audits)
        assert any(row["data"].get("operation") == "observe" for row in audits)
        assert "fixture-ui-body" not in str(client.app.state.runtime.desktop._views)
        client.delete(base + "/snapshot", params={"view_id": view["view_id"]})
        assert client.post(base + "/observe", json=query).status_code == 409
    for path in (tmp_path / "home").rglob("*.jsonl"):
        assert "fixture-ui-body" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("after_dispatch", [False, True])
def test_revoked_observation_keeps_owned_work_but_cannot_publish_late_content(tmp_path, after_dispatch):
    async def scenario():
        async with prepared(tmp_path) as (instance, backend, _clock, agent):
            view = await begin(instance.desktop, agent)
            snapshot = await listed(instance.desktop, agent, view)
            backend.hold("observe", after_dispatch=after_dispatch)
            task = asyncio.create_task(instance.desktop.observe(agent, verification(view, snapshot)))
            await asyncio.wait_for((backend.dispatched if after_dispatch else backend.entered)["observe"].wait(), 5)
            await instance.desktop.forget(agent, view["view_id"])
            backend.release("observe", after_dispatch=after_dispatch)
            with pytest.raises(BusinessError) as error:
                await task
            assert error.value.code == "desktop_view_changed"
            assert sum(name == "observe" for name, _ in backend.calls) == int(after_dispatch)

    asyncio.run(scenario())


def test_cancelled_waiter_and_expiry_never_cache_ui_content(tmp_path):
    async def scenario():
        async with prepared(tmp_path) as (instance, backend, clock, agent):
            view = await begin(instance.desktop, agent)
            snapshot = await listed(instance.desktop, agent, view)
            backend.hold("observe", after_dispatch=True)
            task = asyncio.create_task(instance.desktop.observe(agent, verification(view, snapshot)))
            await asyncio.wait_for(backend.dispatched["observe"].wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert instance.desktop.health()["pending"] == 1
            clock.advance(61)
            backend.release("observe", after_dispatch=True)
            await no_pending(instance.desktop)
            assert (await instance.desktop.status(agent))["snapshot_id"] is None
            assert "fixture-ui-body" not in str(instance.desktop._views)

    asyncio.run(scenario())


def test_observation_agent_scope_and_failed_audit_do_not_publish_content(tmp_path):
    async def scenario():
        async with prepared(tmp_path) as (instance, backend, _clock, agent):
            service = instance.desktop
            view = await begin(service, agent)
            snapshot = await listed(service, agent, view)
            other = (await instance._database.write(lambda u: u.create_agent("Other UI owner")))["id"]
            foreign_view = await begin(service, other)
            with pytest.raises(BusinessError) as error:
                await service.observe(other, verification(foreign_view, snapshot))
            assert error.value.code == "desktop_snapshot_expired"
            assert not any(name == "observe" for name, _ in backend.calls)
            original = service._audit
            for fail_completed in (False, True):

                async def audit(*args, completed=False, **kwargs):
                    if completed == fail_completed:
                        raise BusinessError("audit_unavailable", "Fixture audit failure", status=503)
                    return await original(*args, completed=completed, **kwargs)

                service._audit = audit
                with pytest.raises(BusinessError) as error:
                    await service.observe(agent, verification(view, snapshot))
                assert error.value.code == "audit_unavailable"
            assert sum(name == "observe" for name, _ in backend.calls) == 1
            assert "fixture-ui-body" not in str(service._views)

    asyncio.run(scenario())
