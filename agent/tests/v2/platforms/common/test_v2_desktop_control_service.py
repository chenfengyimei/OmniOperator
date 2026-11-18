"""Actual owner APIs, SQLite audit and lifecycle over owned injected control pipes; no native control grant or input."""

from omni_test_support.v2.desktop_control_service import (
    PROGRAM as PROGRAM,
)
from omni_test_support.v2.desktop_control_service import (
    PURPOSE as PURPOSE,
)
from omni_test_support.v2.desktop_control_service import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.desktop_control_service import (
    DesktopControlBackend as DesktopControlBackend,
)
from omni_test_support.v2.desktop_control_service import (
    FixtureDesktopBackend as FixtureDesktopBackend,
)
from omni_test_support.v2.desktop_control_service import (
    OwnedProcess as OwnedProcess,
)
from omni_test_support.v2.desktop_control_service import (
    Repositories as Repositories,
)
from omni_test_support.v2.desktop_control_service import (
    TestClient as TestClient,
)
from omni_test_support.v2.desktop_control_service import (
    asynccontextmanager as asynccontextmanager,
)
from omni_test_support.v2.desktop_control_service import (
    asyncio as asyncio,
)
from omni_test_support.v2.desktop_control_service import (
    backend as backend,
)
from omni_test_support.v2.desktop_control_service import (
    begin as begin,
)
from omni_test_support.v2.desktop_control_service import (
    http_app as http_app,
)
from omni_test_support.v2.desktop_control_service import (
    http_begin as http_begin,
)
from omni_test_support.v2.desktop_control_service import (
    http_owner as http_owner,
)
from omni_test_support.v2.desktop_control_service import (
    json as json,
)
from omni_test_support.v2.desktop_control_service import (
    linux as linux,
)
from omni_test_support.v2.desktop_control_service import (
    open_instance as open_instance,
)
from omni_test_support.v2.desktop_control_service import (
    prepared as prepared,
)
from omni_test_support.v2.desktop_control_service import (
    pytest as pytest,
)
from omni_test_support.v2.desktop_control_service import (
    request as request,
)
from omni_test_support.v2.desktop_control_service import (
    settled as settled,
)
from omni_test_support.v2.desktop_control_service import (
    sys as sys,
)
from omni_test_support.v2.desktop_control_service import (
    uuid as uuid,
)


def test_system_permission_is_separately_audited_and_view_detach_reaps_the_actual_child(tmp_path, monkeypatch):
    async def scenario():
        async with prepared(tmp_path, monkeypatch) as (instance, agent, native, owners):
            view = await begin(instance.desktop, agent)
            grant = await instance.desktop_control.open(agent, request(view))
            active = await settled(instance.desktop_control, agent, view, "system_authorized")
            assert (
                active["system_permission_granted"]
                and active["system_permission_recorded"]
                and not active["input_authorized"]
            )
            assert active["input_error_code"] == "portal_input_guard_pending"
            audits = await instance._database.read(lambda unit: unit.query("audit", agent))
            own = [row for row in audits if row["operation_id"] == grant["grant_id"]]
            assert {row["kind"] for row in own} == {"desktop.control_requested", "desktop.control_system_authorized"}
            assert all(row["data"]["input_authorized"] is False for row in own)
            await instance.desktop.forget(agent, view["view_id"])
            assert owners and all(child.reclaimed for child in owners) and native.health()["resident"] == 0
            audits = await instance._database.read(lambda unit: unit.query("audit", agent))
            assert any(
                row["kind"] == "desktop.control_closed" and row["operation_id"] == grant["grant_id"] for row in audits
            )
            with pytest.raises(BusinessError):
                await instance.desktop_control.status(agent, view["view_id"])

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["desktop.control_requested", "desktop.control_system_authorized"])
def test_failed_business_audit_cannot_leave_system_permission_exposed_or_a_child_unowned(tmp_path, monkeypatch, stage):
    original = Repositories.diagnostic_audit

    def audit(self, agent, operation, kind, data):
        if kind == stage:
            raise BusinessError("audit_unavailable", "private-audit-body", status=503)
        return original(self, agent, operation, kind, data)

    monkeypatch.setattr(Repositories, "diagnostic_audit", audit)

    async def scenario():
        async with prepared(tmp_path, monkeypatch) as (instance, agent, native, owners):
            view = await begin(instance.desktop, agent)
            if stage == "desktop.control_requested":
                with pytest.raises(BusinessError) as denied:
                    await instance.desktop_control.open(agent, request(view))
                assert denied.value.code == "portal_control_audit_failed" and "private-audit-body" not in str(
                    denied.value
                )
                assert not owners
            else:
                await instance.desktop_control.open(agent, request(view))
            status = await settled(instance.desktop_control, agent, view, "failed")
            assert not status["input_authorized"] and not status["system_permission_granted"]
            async with asyncio.timeout(5):
                while native.health()["resident"]:
                    await asyncio.sleep(0)
            assert all(child.reclaimed for child in owners)

    asyncio.run(scenario())


def test_failed_close_audit_keeps_business_settlement_and_database_until_explicit_retry(tmp_path, monkeypatch):
    original = Repositories.diagnostic_audit
    failing = [True]

    def audit(self, agent, operation, kind, data):
        if kind == "desktop.control_closed" and failing[0]:
            raise BusinessError("audit_unavailable", "private-close-body", status=503)
        return original(self, agent, operation, kind, data)

    monkeypatch.setattr(Repositories, "diagnostic_audit", audit)

    async def scenario():
        async with prepared(tmp_path, monkeypatch) as (instance, agent, native, owners):
            view = await begin(instance.desktop, agent)
            grant = await instance.desktop_control.open(agent, request(view))
            await settled(instance.desktop_control, agent, view, "system_authorized")
            try:
                with pytest.raises(BusinessError):
                    await instance.desktop_control.close_grant(agent, view["view_id"], grant["grant_id"])
                assert all(child.reclaimed for child in owners) and native.health()["resident"] == 0
                assert not await instance.desktop_control.close(0.1)
                assert await instance._database.read(lambda unit: unit.query("agents", ""))
            finally:
                failing[0] = False
                assert await instance.desktop_control.close(5)

    asyncio.run(scenario())


def test_owner_http_requires_separate_consent_exact_scope_and_hides_private_native_details(tmp_path, monkeypatch):
    linux(monkeypatch)
    owners = []
    app = http_app(tmp_path / "home", desktop_backend=FixtureDesktopBackend(), control_backend=backend(owners))
    with TestClient(app) as client:
        _agent, base, _database = http_owner(client, tmp_path / "home")
        headers = dict(client.headers)
        view = http_begin(client, base)
        url = base + "/control"
        assert client.post(url, headers={"Authorization": ""}, json=request(view)).status_code == 401
        for changed in (
            {"view_id": view["view_id"], "consent": True},
            {**request(view), "purpose": "window-share"},
            {**request(view), "consent": 1},
            {**request(view), "devices": 4},
            {**request(view), "restore_token": "no"},
        ):
            assert client.post(url, headers=headers, json=changed).status_code == 422
        opened = client.post(url, headers=headers, json=request(view, 1))
        assert opened.status_code == 202, opened.text
        grant = opened.json()["grant_id"]
        state = None
        for _ in range(100):
            response = client.get(url, headers=headers, params={"view_id": view["view_id"]})
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == "no-store"
            state = response.json()["grant"]
            if state["system_permission_recorded"]:
                break
        assert state["system_permission_recorded"] and not state["input_authorized"]
        assert (
            client.delete(
                url, headers=headers, params={"view_id": view["view_id"], "grant_id": str(uuid.uuid4())}
            ).status_code
            == 409
        )
        closed = client.delete(url, headers=headers, params={"view_id": view["view_id"], "grant_id": grant})
        assert closed.status_code == 200, closed.text
        assert closed.json()["closed"] and not closed.json()["input_authorized"]
        assert all(child.reclaimed for child in owners)
        assert "NotifyKeyboard" not in json.dumps(state) and "/org/freedesktop/portal" not in json.dumps(state)
