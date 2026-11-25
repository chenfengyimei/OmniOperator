"""Trusted features contribute one instance, approved tools and owner HTTP."""

import asyncio
import time
import uuid

import pytest
from fastapi import APIRouter, Request
from fastapi.testclient import TestClient
from omni_test_support.v2.media import FakeProcessor

from omni.adapters.http_routes import FeatureRoutes, mount_feature_routes
from omni.application.capabilities import Capability
from omni.application.engine import Application
from omni.application.media import MediaService
from omni.application.scheduling import SchedulingService
from omni.composition.features import FeatureBinding, FeaturePlugin
from omni.composition.features import mcp as mcp_feature
from omni.composition.http import http_app
from omni.composition.instance import open_instance
from omni.domain.contracts import ActionResult, Invocation, ModelReply, Outcome, ToolSpec

FEATURE_WRITE = ToolSpec(
    "fixture_feature_write",
    "Record a test event",
    {"type": "object", "additionalProperties": False},
    writes=True,
)


def test_a_registered_feature_shares_one_service_between_owner_http_and_the_approved_tool(tmp_path):
    installed = []
    report_path = "/agents/{agent_id}/runs/{run_id}/fixture-notes"

    class Service:
        count = 0
        closes = 0

        async def invoke(self, name, arguments, *, operation_id, context):
            self.count += 1
            return ActionResult(Outcome.OK, "Recorded")

        async def close(self, timeout_s):
            self.closes += 1
            return True

    async def install(context):
        assert not context.accepting()
        service = Service()
        installed.append(service)
        context.lifetime.own("fixture-notes", 0, service.close)
        return FeatureBinding(service, (Capability(FEATURE_WRITE, service),))

    def routes(runtime):
        router = APIRouter()

        @router.get("/agents/{agent_id}/fixture-notes/count")
        async def count(agent_id: uuid.UUID, request: Request):
            service = runtime(request).feature_services["fixture-notes"]
            return {"count": service.count}

        @router.get(report_path)
        async def report(agent_id: uuid.UUID, run_id: uuid.UUID, request: Request):
            return await count(agent_id, request)

        return router

    class Model:
        async def complete(self, messages, tools, settings):
            if messages[-1]["role"] != "tool" and FEATURE_WRITE in tools:
                return ModelReply("", (Invocation("one", FEATURE_WRITE.name, {}),), cost_micro=0)
            return ModelReply("Completed", cost_micro=0)

    plugin = FeaturePlugin("fixture-notes", factory=routes, install=install, extra_paths=(report_path,))
    app = http_app(tmp_path, model=Model(), feature_plugins=(plugin,))
    assert "/api/v1/agents/{agent_id}/fixture-notes/count" in app.openapi()["paths"]
    assert installed == []
    with TestClient(app) as client:
        assert len(installed) == 1
        route = f"/api/v1/agents/{uuid.uuid4()}/fixture-notes/count"
        assert client.get(route).status_code == 401
        assert client.get("/api/v1" + report_path.format(agent_id=uuid.uuid4(), run_id=uuid.uuid4())).status_code == 401
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text(encoding="utf-8")
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        base = f"/api/v1/agents/{agent}"
        route = base + "/fixture-notes/count"
        assert client.get(route).json() == {"count": 0}

        settings = client.get(base + "/settings").json()
        profile_url = base + "/configurations/" + settings["value"]["model_profile_id"]
        profile = client.get(profile_url).json()
        configured = client.put(
            profile_url,
            json={
                "name": profile["name"],
                "enabled": True,
                "revision": profile["revision"],
                "value": {**profile["value"], "model": "fake", "context_tokens": 65536},
            },
        )
        assert configured.status_code == 200, configured.text
        response = client.post(base + "/turns", json={"key": "one", "message": "Record one event", "mode": "agent"})
        assert response.status_code == 202, response.text
        run = response.json()["run_id"]
        approvals_url = base + "/approvals"
        deadline = time.monotonic() + 15
        items = []
        while time.monotonic() < deadline:
            items = client.get(approvals_url).json()["items"]
            if items:
                break
            time.sleep(0.02)
        [approval] = items
        assert approval["run_id"] == run and approval["tool"] == FEATURE_WRITE.name
        assert client.get(route).json() == {"count": 0}
        decision = client.post(
            approvals_url + "/" + approval["id"],
            json={"allowed": True, "policy_revision": approval["policy_revision"]},
        )
        assert decision.status_code == 200, decision.text
        deadline = time.monotonic() + 15
        final = {}
        while time.monotonic() < deadline:
            final = client.get(base + "/runs/" + run).json()
            if final["state"] in {"succeeded", "failed"}:
                break
            time.sleep(0.02)
        assert final["state"] == "succeeded", final
        assert client.get(route).json() == {"count": 1}
        assert client.get("/api/v1" + report_path.format(agent_id=agent, run_id=run)).json() == {"count": 1}
    assert installed[0].closes == 1


@pytest.mark.parametrize("defect", ["prefix", "starlette", "duplicate-route", "duplicate-id", "existing-http"])
def test_feature_router_rejects_endpoints_that_break_mount_or_authorization_rules(defect):
    parent = APIRouter(prefix="/api/v1")

    async def ping():
        return {"ok": True}

    def factory(runtime):
        prefix = "/private" if defect == "prefix" else "/agents/{agent_id}/fixture"
        routes = APIRouter(prefix=prefix)
        if defect == "starlette":
            routes.add_route("/ping", ping, methods=["GET"])
        else:
            routes.add_api_route("/ping", ping, methods=["GET"])
        if defect == "duplicate-route":
            routes.add_api_route("/ping", ping, methods=["GET"])
        return routes

    if defect == "existing-http":
        parent.add_api_route("/agents/{agent_id}/fixture/ping", ping, methods=["GET"])
    feature = FeatureRoutes("fixture", factory)
    declared = (feature, feature) if defect == "duplicate-id" else (feature,)
    expected = {
        "prefix": "agent-scoped prefix",
        "starlette": "authenticated HTTP",
        "duplicate-route": "Duplicate feature HTTP route",
        "duplicate-id": "Duplicate feature route IDs",
        "existing-http": "Duplicate feature HTTP route",
    }
    with pytest.raises(ValueError, match=expected[defect]):
        mount_feature_routes(parent, declared, lambda _: None)


@pytest.mark.parametrize("defect", ["undeclared-path", "missing-path", "existing-path"])
def test_feature_route_aliases_only_mount_exact_declared_paths(defect):
    parent = APIRouter(prefix="/api/v1")
    alias = "/agents/{agent_id}/runs/{run_id}/fixture"

    async def ping():
        return {"ok": True}

    def factory(runtime):
        routes = APIRouter()
        if defect != "missing-path":
            removed = alias + "/extra" if defect == "undeclared-path" else alias
            routes.add_api_route(removed, ping, methods=["GET"])
        return routes

    if defect == "existing-path":
        parent.add_api_route(alias, ping, methods=["GET"])
    expected = {
        "undeclared-path": "authenticated HTTP within their prefix",
        "missing-path": "Declared feature paths are missing",
        "existing-path": "Duplicate feature HTTP route",
    }
    with pytest.raises(ValueError, match=expected[defect]):
        mount_feature_routes(parent, (FeatureRoutes("fixture", factory, extra_paths=(alias,)),), lambda _: None)


@pytest.mark.parametrize("failed_step", ["constructor", "recovery"])
def test_mcp_install_reclaims_one_transport_if_startup_fails(tmp_path, monkeypatch, failed_step):
    class Transport:
        closes = 0

        def stop_accepting(self):
            pass

        async def close(self, timeout_s):
            self.closes += 1
            return True

    def fail_constructor(*args):
        raise RuntimeError("fixture MCP startup failure")

    async def fail_recovery(service):
        raise RuntimeError("fixture MCP startup failure")

    async def scenario():
        transport = Transport()
        with monkeypatch.context() as patch:
            if failed_step == "constructor":
                patch.setattr(mcp_feature, "MCPService", fail_constructor)
            else:
                patch.setattr(mcp_feature.MCPService, "recover", fail_recovery)
            with pytest.raises(RuntimeError, match="fixture MCP startup failure"):
                await open_instance(tmp_path, mcp_transport=transport, capabilities=())
        assert transport.closes == 1
        reopened = await open_instance(tmp_path, capabilities=())
        try:
            assert reopened.feature_services["extensions"] is reopened.extensions
            assert reopened.feature_services["mcp"] is reopened.mcp
            assert reopened.feature_services["skills"] is reopened.skills
        finally:
            assert (await reopened.close())["complete"]

    asyncio.run(scenario())


@pytest.mark.parametrize("recovery_fails", [False, True])
def test_media_processor_has_one_shutdown_owner_on_startup_and_rollback(tmp_path, monkeypatch, recovery_fails):
    class Processor(FakeProcessor):
        closes = 0

        async def close(self, timeout_s=30):
            self.closes += 1
            return True

    processor = Processor()

    async def failed_recovery(service):
        raise RuntimeError("fixture media recovery failure")

    async def scenario():
        if recovery_fails:
            monkeypatch.setattr(MediaService, "recover", failed_recovery)
            with pytest.raises(RuntimeError, match="fixture media recovery failure"):
                await open_instance(tmp_path, media_processor=processor, capabilities=())
        else:
            instance = await open_instance(tmp_path, media_processor=processor, capabilities=())
            try:
                assert instance.feature_services["media"] is instance.media
                assert instance.feature_services["android"] is instance.android
                assert instance.feature_services["documents"] is instance.documents
                assert instance.feature_services["people"].people is instance.people
                assert instance.documents._persona is instance.persona_growth is instance.media._persona
            finally:
                assert (await instance.close())["complete"]
        assert processor.closes == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("start_fails", [False, True])
def test_scheduling_starts_after_kernel_recovery_and_is_reclaimed_on_failure(tmp_path, monkeypatch, start_fails):
    start_application = Application.start
    start_scheduler = SchedulingService.start
    ready = []
    started = []

    async def observe_kernel(application):
        await start_application(application)
        assert application.health()["ready"]
        ready.append(application)

    def observe_scheduler(scheduler):
        assert len(ready) == len(started) + 1
        assert "cronjob_manage" in ready[-1]._tools
        start_scheduler(scheduler)
        started.append(scheduler)
        if start_fails and len(started) == 1:
            raise RuntimeError("fixture scheduler start failure")

    monkeypatch.setattr(Application, "start", observe_kernel)
    monkeypatch.setattr(SchedulingService, "start", observe_scheduler)

    async def scenario():
        if start_fails:
            with pytest.raises(RuntimeError, match="fixture scheduler start failure"):
                await open_instance(tmp_path)
            assert started[0]._worker.done()
            assert started[0].health()["closing"]

        instance = await open_instance(tmp_path)
        try:
            assert instance.feature_services["schedules"] is instance.schedules is started[-1]
            assert instance.schedules.health()["ready"]
            assert instance.feature_services["action-reviews"] is instance.action_reviews
            assert instance.action_reviews._notify == instance.schedules.notify
            assert instance.action_reviews._blockers == instance.application.action_review_blockers
            assert instance.terminal_reviews._guard == instance.terminals.while_quiescent
        finally:
            assert (await instance.close())["complete"]
        assert instance.schedules._worker.done()

    asyncio.run(scenario())


def test_injected_feature_cannot_replace_a_feature_that_depends_on_the_kernel(tmp_path):
    async def install(context):
        pytest.fail("conflicting feature must be rejected before startup")

    plugin = FeaturePlugin("schedules", factory=lambda _: APIRouter(), install=install)
    home = tmp_path / "instance"
    with pytest.raises(ValueError, match="Duplicate feature plugin IDs"):
        asyncio.run(open_instance(home, feature_plugins=(plugin,)))
    assert not home.exists()
