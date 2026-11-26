"""Audit preview APIs and offline CLI preserve bounded frozen pages, scope and read-only ownership."""

import asyncio
import datetime as dt
import json
import subprocess
import sys
import uuid

from fastapi.testclient import TestClient
from omni_test_support.v2.objects import Clock, opened
from sqlalchemy import delete

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.migration_config import head as migration_head
from omni.adapters.platform.runtime.paths import InstanceLock, InstancePaths
from omni.composition.http import http_app


def test_preview_http_authentication_validation_and_no_deletion_endpoint(tmp_path):
    with TestClient(http_app(tmp_path)) as client:
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text().strip()
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        path = f"/api/v1/agents/{agent}/audit/retention-preview"
        assert client.get(path, headers={"Authorization": ""}).status_code == 401
        result = client.get(path).json()
        assert result["items"] == [] and not result["can_apply"] and not result["has_more"]
        assert result["agent_id"] == agent and result["schema_version"] == migration_head()
        assert result["counts"] == {"scanned": 0, "eligible": 0, "protected": 0}
        for query in (
            {"limit": 0},
            {"limit": 51},
            {"cursor": "broken"},
            {"cursor": "x" * 1025},
            {"as_of": "2026-01-01T00:00:00"},
            {"as_of": "9999-01-01T00:00:00+00:00"},
            {"after": 0},
        ):
            assert client.get(path, params=query).status_code == 422
        assert client.get(f"/api/v1/agents/{uuid.uuid4()}/audit/retention-preview").status_code == 404
        assert client.post(path).status_code == 405
        assert client.delete(path).status_code == 405
        assert client.get(f"/api/v1/agents/{agent}/audit").status_code == 200


def test_http_pages_freeze_age_and_ceiling_then_trim_and_epoch_change_expire(tmp_path):
    with TestClient(http_app(tmp_path)) as client:
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text().strip()
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        db = client.app.state.runtime.application._db
        clock, aged, original = Clock(), Clock(), db.clock
        now = clock.current
        aged.current = now - dt.timedelta(days=200)
        db.clock = clock
        path = f"/api/v1/agents/{agent}/audit/retention-preview"

        def seed(u, kind, count, data):
            # Backdate this transaction's fixture rows without changing the live scheduler clock.
            u.clock = aged
            return [u.audit(agent, kind, None, data) for _ in range(count)]

        try:
            identities = client.portal.call(
                db.write,
                lambda u: seed(u, "fixture.protected", 3, {"secret": "not exported"}),
            )
            other = client.portal.call(db.write, lambda u: u.create_agent("Other"))["id"]
            first = client.get(path, params={"limit": 1}).json()
            assert first["has_more"] and first["counts"] == {"scanned": 1, "eligible": 0, "protected": 1}
            assert first["items"][0]["audit_id"] == identities[0]
            assert "not exported" not in str(first) and "data" not in first["items"][0]
            assert (
                client.get(
                    f"/api/v1/agents/{other}/audit/retention-preview", params={"cursor": first["next_cursor"]}
                ).status_code
                == 422
            )
            ordinary = client.get(f"/api/v1/agents/{agent}/audit").json()["next_cursor"]
            assert client.get(path, params={"cursor": ordinary}).status_code == 422
            assert (
                client.get(
                    path, params={"cursor": first["next_cursor"], "as_of": (now - dt.timedelta(seconds=1)).isoformat()}
                ).status_code
                == 422
            )
            # New (even backdated) commits cannot move the original scan ceiling.
            (late,) = client.portal.call(db.write, lambda u: seed(u, "fixture.late", 1, {}))
            clock.current = now + dt.timedelta(days=2)
            second = client.get(
                path, params={"cursor": first["next_cursor"], "as_of": first["as_of"], "limit": 50}
            ).json()
            assert second["as_of"] == first["as_of"] and second["cutoff"] == first["cutoff"]
            assert second["stream"]["high_water"] == first["stream"]["high_water"]
            assert [item["audit_id"] for item in second["items"]] == identities[1:]
            assert not second["has_more"]
            fresh = client.get(path).json()
            assert late in {item["audit_id"] for item in fresh["items"]}
            # Other-Agent deletion does not invalidate this scope.
            disposable = client.portal.call(db.write, lambda u: u.audit(other, "fixture", None, {}))
            client.portal.call(
                db.write, lambda u: u.connection.execute(delete(s.audit).where(s.audit.c.id == disposable))
            )
            assert client.get(path, params={"cursor": first["next_cursor"]}).status_code == 200
            client.portal.call(
                db.write, lambda u: u.connection.execute(delete(s.audit).where(s.audit.c.id == identities[1]))
            )
            expired = client.get(path, params={"cursor": first["next_cursor"]})
            assert expired.status_code == 410 and expired.json()["error"]["code"] == "history_cursor_expired"
            refreshed = client.get(path).json()
            assert refreshed["history_trimmed"] and refreshed["stream"]["deleted_count"] == 1
            client.portal.call(
                db.write, lambda u: u.connection.execute(s.history_identity.update().values(epoch=str(uuid.uuid4())))
            )
            assert client.get(path, params={"cursor": refreshed["next_cursor"]}).status_code == 410
        finally:
            db.clock = original


def test_age_boundary_and_empty_continuation_do_not_mutate_history(tmp_path):
    async def scenario():
        clock = Clock()
        now = clock.current
        async with opened(tmp_path, clock) as (_, db, _, agent):
            ids = []
            for offset in (-1, 0, 1):
                clock.current = now - dt.timedelta(days=180) + dt.timedelta(microseconds=offset)
                ids.append(await db.write(lambda u: u.audit(agent, "fixture", None, {"private": "body"})))
            clock.current = now
            before = await db.read(lambda u: u.history_page("audit", agent))
            first = await db.read(lambda u: u.audit_retention_preview(agent, as_of=now.isoformat(), limit=1))
            assert first["has_more"] and first["items"][0]["audit_id"] == ids[0]
            second = await db.read(lambda u: u.audit_retention_preview(agent, cursor=first["next_cursor"]))
            assert [row["audit_id"] for row in second["items"]] == [ids[1]] and not second["has_more"]
            empty = await db.read(lambda u: u.audit_retention_preview(agent, cursor=second["next_cursor"]))
            assert empty["items"] == [] and empty["next_cursor"] == second["next_cursor"]
            assert await db.read(lambda u: u.history_page("audit", agent)) == before
            # Advancing the clock creates no new candidates on the old page chain.
            clock.current += dt.timedelta(days=1)
            assert not (await db.read(lambda u: u.audit_retention_preview(agent, cursor=empty["next_cursor"])))["items"]
            assert len((await db.read(lambda u: u.audit_retention_preview(agent)))["items"]) == 3

    asyncio.run(scenario())


def test_offline_cli_preview_is_read_only_and_refuses_an_online_instance(tmp_path):
    home = tmp_path / "home"

    async def setup():
        clock = Clock()
        now = clock.current
        async with opened(home, clock) as (_, db, _, agent):
            clock.current -= dt.timedelta(days=200)
            identity = await db.write(lambda u: u.audit(agent, "fixture", None, {"private": "body"}))
            clock.current = now
            return agent, identity

    agent, identity = asyncio.run(setup())
    command = [
        sys.executable,
        "-m",
        "omni.composition.cli",
        "--home",
        str(home),
        "retention-audit-preview",
        "--agent",
        agent,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert [row["audit_id"] for row in report["items"]] == [identity]
    assert report["verification"]["integrity_ok"] and not report["can_apply"]
    assert "body" not in result.stdout and "private" not in result.stdout
    invalid = subprocess.run(command + ["--limit", "51"], capture_output=True, text=True, timeout=30, check=False)
    assert invalid.returncode == 1 and json.loads(invalid.stderr)["error"] == "invalid_audit_retention_query"
    lock = InstanceLock(InstancePaths.resolve(home))
    lock.acquire()
    try:
        busy = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
        assert busy.returncode == 1 and json.loads(busy.stderr)["error"] == "instance_busy"
    finally:
        lock.close()
