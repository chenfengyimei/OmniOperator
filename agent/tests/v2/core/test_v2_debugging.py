"""Temporary debug authorizations fail closed, expire and remain observable after restart."""

import asyncio
import logging
import uuid

import pytest
from fastapi.testclient import TestClient

from omni.adapters.diagnostics import bind_context
from omni.adapters.persistence.operations_repository import OperationsRepositories
from omni.adapters.primitives import SystemClock
from omni.composition.http import http_app
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError


class Clock(SystemClock):
    delta = 0

    def monotonic(self):
        return super().monotonic() + self.delta


def test_debug_is_explicit_agent_scoped_audited_and_boot_bound(tmp_path):
    async def scenario():
        clock = Clock()
        instance = await open_instance(tmp_path, capabilities=(), clock=clock)
        first_boot = instance.boot_id
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            other = (await instance._database.write(lambda u: u.create_agent("Other")))["id"]
            assert not (await instance.operations.debug_status(agent))["active"]
            active = await instance.operations.debug_start(agent, 300)
            assert active["active"] and active["boot_id"] == first_boot
            with bind_context(agent_id=agent, run_id=str(uuid.uuid4())):
                logging.getLogger("omni.v2.debug_fixture").debug(
                    "private-debug-canary", extra={"event": "debug.sample"}
                )
            instance._logging.sink.queue.join()
            excerpt = await instance.operations.debug_logs(agent, active["operation_id"], {"limit": 1})
            assert len(excerpt["items"]) == 1 and "private-debug-canary" not in str(excerpt)
            with pytest.raises(BusinessError):
                await instance.operations.debug_logs(other, active["operation_id"], {})
            occupied = await instance.operations.debug_status(other)
            assert occupied == {"active": False, "occupied": True, "scope": "another_agent"}
            with pytest.raises(BusinessError) as failure:
                await instance.operations.debug_stop(other, active["operation_id"])
            assert failure.value.code == "debug_capture_changed"
            with pytest.raises(BusinessError):
                await instance.operations.debug_stop(agent, str(uuid.uuid4()))
            with pytest.raises(BusinessError):
                await instance.operations.debug_start(agent, 3601)
            clock.delta += 301
            assert not (await instance.operations.debug_status(agent))["active"]
            audit = await instance.application.query("audit", agent)
            rows = [row for row in audit if row["kind"].startswith("diagnostic.debug.")]
            assert [row["kind"] for row in rows] == [
                "diagnostic.debug.requested",
                "diagnostic.debug.started",
                "diagnostic.debug.stopped",
            ]
            assert rows[-1]["data"]["reason"] == "expired"
            assert all(row["data"]["boot_id"] == first_boot for row in rows)
            again = await instance.operations.debug_start(agent, 300)
            assert again["operation_id"] != active["operation_id"]
        finally:
            assert (await instance.close())["complete"]
        restarted = await open_instance(tmp_path, capabilities=())
        try:
            assert restarted.boot_id != first_boot
            assert not (await restarted.operations.debug_status(agent))["active"]
            with pytest.raises(BusinessError):
                await restarted.operations.debug_logs(agent, active["operation_id"], {})
            rows = [
                row
                for row in await restarted.application.query("audit", agent)
                if row["kind"] == "diagnostic.debug.stopped"
            ]
            assert rows[-1]["data"]["reason"] == "shutdown"
        finally:
            assert (await restarted.close())["complete"]

    asyncio.run(scenario())


@pytest.mark.parametrize("failing_kind", ["diagnostic.debug.requested", "diagnostic.debug.started"])
def test_debug_cannot_remain_enabled_when_its_audit_fails(tmp_path, monkeypatch, failing_kind):
    original = OperationsRepositories.diagnostic_audit

    def fail(self, agent, operation, kind, data):
        if kind == failing_kind:
            raise BusinessError("audit_unavailable", "Injected audit failure")
        return original(self, agent, operation, kind, data)

    async def scenario():
        instance = await open_instance(tmp_path, capabilities=())
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            monkeypatch.setattr(OperationsRepositories, "diagnostic_audit", fail)
            with pytest.raises(BusinessError, match="audit failure"):
                await instance.operations.debug_start(agent, 60)
            assert not instance._logging.sink.debug.snapshot()["active"]
        finally:
            monkeypatch.setattr(OperationsRepositories, "diagnostic_audit", original)
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_debug_http_requires_owner_and_strict_bounded_input(tmp_path):
    with TestClient(http_app(tmp_path)) as client:
        assert client.get(f"/api/v1/agents/{uuid.uuid4()}/debug").status_code == 401
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text()
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        path = f"/api/v1/agents/{agent}/debug"
        for value in (0, 3601, True, "60"):
            assert client.post(path, json={"duration_s": value}).status_code == 422
        active = client.post(path, json={"duration_s": 60})
        assert active.status_code == 200 and active.json()["active"]
        assert client.post(path, json={"duration_s": 60}).status_code == 409
        stopped = client.delete(path + "/" + active.json()["operation_id"])
        assert stopped.status_code == 200 and not stopped.json()["active"]
        assert client.get(f"/api/v1/agents/{uuid.uuid4()}/debug").status_code == 404
