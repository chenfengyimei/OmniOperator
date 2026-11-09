"""Authenticated history APIs use scoped cursors, explicit expiration and atomic trim visibility."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from omni.adapters.persistence import schema as s
from omni.composition.http import http_app
from omni.domain.contracts import BusinessError


def test_three_history_endpoints_validate_auth_bounds_and_cursor_scope(tmp_path):
    with TestClient(http_app(tmp_path)) as client:
        assert client.get("/api/v1/system/audit").status_code == 401
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text()
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        database = client.app.state.runtime.application._db

        def seed(u):
            other = u.create_agent("Other")
            turn = u.accept_turn(agent, None, "history-http", "permanent message", {})
            u.transition(agent, turn["run_id"], "cancelled")
            return other, turn

        other, turn = client.portal.call(database.write, seed)
        base = f"/api/v1/agents/{agent}"
        events = base + f"/runs/{turn['run_id']}/events"
        audit = base + "/audit"
        first = client.get(events, params={"limit": 1}).json()
        assert len(first["items"]) == 1 and first["has_more"] and first["stream"]["high_water"] >= 2
        second = client.get(events, params={"limit": 1, "cursor": first["next_cursor"]}).json()
        assert len(second["items"]) == 1 and not second["has_more"]
        assert second["items"][0]["sequence"] > first["items"][0]["sequence"]
        assert client.get(audit, params={"cursor": first["next_cursor"]}).status_code == 422
        page = client.get(audit).json()
        wrong_agent = client.get(f"/api/v1/agents/{other['id']}/audit", params={"cursor": page["next_cursor"]})
        assert wrong_agent.status_code == 422 and wrong_agent.json()["error"]["code"] == "history_scope_mismatch"
        assert client.get(f"/api/v1/agents/{other['id']}/runs/{turn['run_id']}/events").status_code == 404
        for path in (events, audit, "/api/v1/system/audit"):
            for params in ({"after": 0}, {"limit": 201}, {"cursor": "broken"}):
                assert client.get(path, params=params).status_code == 422
            instance_page = client.get(path, params={"limit": 1}).json()
            assert {"items", "next_cursor", "has_more", "stream", "history_trimmed"} <= instance_page.keys()
            assert client.get(path, headers={"Authorization": ""}).status_code == 401


def test_delete_watermark_rolls_back_with_source_fk_and_http_reports_expiry(tmp_path):
    with TestClient(http_app(tmp_path)) as client:
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text()
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        database = client.app.state.runtime.application._db
        path = f"/api/v1/agents/{agent}/audit"

        def seed(u):
            memory = u.remember(agent, "protected manual fact")
            source = u.connection.execute(
                select(s.source_anchors.c.manual_audit_id).where(s.source_anchors.c.memory_id == memory["id"])
            ).scalar_one()
            return source, u.audit(agent, "fixture", None, {})

        source, ordinary = client.portal.call(database.write, seed)
        before = client.get(path).json()

        def invalid_trim(u):
            u.connection.execute(delete(s.audit).where(s.audit.c.id == ordinary))
            u.connection.execute(delete(s.audit).where(s.audit.c.id == source))

        with pytest.raises(BusinessError) as failure:
            client.portal.call(database.write, invalid_trim)
        assert failure.value.code == "data_constraint"
        assert client.get(path).json() == before
        client.portal.call(
            database.write, lambda u: u.connection.execute(delete(s.audit).where(s.audit.c.id == ordinary))
        )
        expired = client.get(path, params={"cursor": before["next_cursor"]})
        assert expired.status_code == 410 and expired.json()["error"]["code"] == "history_cursor_expired"
        assert expired.json()["error"]["request_id"] == expired.headers["x-request-id"]
        fresh = client.get(path).json()
        assert fresh["history_trimmed"] and fresh["stream"]["deleted_count"] == 1
        assert source in {row["id"] for row in fresh["items"]}
