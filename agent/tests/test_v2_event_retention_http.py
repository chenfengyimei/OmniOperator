"""Event preview auth, frozen pages and trim expiration stay distinct from audit history."""

import datetime as dt
import json
import subprocess
import sys
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import delete
from test_v2_objects import Clock

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.migration_config import head as migration_head
from omni.composition.http import http_app


def test_preview_auth_scope_bounds_age_ceiling_and_independent_trim(tmp_path):
    with TestClient(http_app(tmp_path)) as client:
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text().strip()
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        path = f"/api/v1/agents/{agent}/events/retention-preview"
        assert client.get(path, headers={"Authorization": ""}).status_code == 401
        empty = client.get(path).json()
        assert empty["items"] == [] and empty["can_apply"] is False
        assert empty["schema_version"] == migration_head()
        for query in ({"after": 0}, {"limit": 0}, {"limit": 51}, {"cursor": "bad"}, {"as_of": "2026-11-05T00:00:00"}):
            assert client.get(path, params=query).status_code == 422
        assert client.get(f"/api/v1/agents/{uuid.uuid4()}/events/retention-preview").status_code == 404
        assert client.post(path).status_code == 405 and client.delete(path).status_code == 405
        db = client.app.state.runtime.application._db
        clock, original = Clock(), db.clock
        now = clock.current
        db.clock = clock
        try:
            clock.current = now - dt.timedelta(days=200)

            def seed(u):
                other = u.create_agent("Other")
                turn = u.accept_turn(agent, None, "event-history", "private-message-not-exported", {})
                u.transition(agent, turn["run_id"], "cancelled")
                return turn, other

            turn, other = client.portal.call(db.write, seed)
            clock.current = now
            first = client.get(path, params={"limit": 1}).json()
            assert first["has_more"] and first["counts"] == {"scanned": 1, "eligible": 0, "protected": 1}
            assert "private-message-not-exported" not in json.dumps(first)
            assert (
                client.get(
                    f"/api/v1/agents/{other['id']}/events/retention-preview", params={"cursor": first["next_cursor"]}
                ).status_code
                == 422
            )
            audit = client.get(f"/api/v1/agents/{agent}/audit").json()
            assert client.get(path, params={"cursor": audit["next_cursor"]}).status_code == 422
            clock.current = now - dt.timedelta(days=200)
            client.portal.call(db.write, lambda u: u.event(agent, turn["run_id"], "fixture.late", {}))
            clock.current = now + dt.timedelta(days=1)
            second = client.get(
                path, params={"cursor": first["next_cursor"], "as_of": first["as_of"], "limit": 50}
            ).json()
            assert second["as_of"] == first["as_of"] and second["stream"]["high_water"] == first["stream"]["high_water"]
            assert len(second["items"]) == 2 and not second["has_more"]
            assert [item["kind"] for item in second["items"]] == ["routing.selected", "run.cancelled"]
            scope_before = client.get(f"/api/v1/agents/{agent}/audit", params={"cursor": audit["next_cursor"]})
            assert scope_before.status_code == 200
            sequence = first["items"][0]["sequence"]
            client.portal.call(
                db.write,
                lambda u: u.connection.execute(delete(s.run_events).where(s.run_events.c.sequence == sequence)),
            )
            expired = client.get(path, params={"cursor": first["next_cursor"]})
            assert expired.status_code == 410 and expired.json()["error"]["code"] == "history_cursor_expired"
            assert (
                client.get(f"/api/v1/agents/{agent}/audit", params={"cursor": audit["next_cursor"]}).status_code == 200
            )
            fresh = client.get(path).json()
            assert fresh["history_trimmed"] and fresh["stream"]["deleted_count"] == 1
            assert any(
                row["kind"] == "fixture.late" and "event_kind_not_eligible" in row["protected_reasons"]
                for row in fresh["items"]
            )
        finally:
            db.clock = original


def test_event_cli_preview_refuses_running_instance(tmp_path):
    with TestClient(http_app(tmp_path)):
        command = [
            sys.executable,
            "-m",
            "omni.composition.cli",
            "--home",
            str(tmp_path),
            "retention-events-preview",
            "--agent",
            str(uuid.uuid4()),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
        assert result.returncode == 1 and json.loads(result.stderr)["error"] == "instance_busy"
