"""Boot-specific authenticated drain, cancellation and bounded HTTP receipts; managers and transports are injected."""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from test_v2_service_deployment import plan
from test_v2_service_runtime import boot_identity

from omni.adapters.http import HttpRuntime, create_app
from omni.adapters.platform import service_readiness as readiness
from omni.adapters.platform.runtime.runtime_identity import RuntimeIdentityFiles, canonical_proof
from omni.adapters.platform.service_registration import ServiceRegistration
from omni.adapters.primitives import SystemClock, UUIDs
from omni.domain.contracts import BusinessError
from omni.domain.service_control import drain_receipt, drain_request, make_control, verify_drain, verify_receipt


@pytest.mark.parametrize("corrupt", ["key", "boot", "operation", "stale", "purpose", "accepted_type"])
def test_control_hmac_binds_action_boot_operation_time_and_domain(corrupt):
    value = plan()
    identity = boot_identity(value)
    op = make_control(value, "stop", UUIDs().new(), SystemClock().now(), identity["boot_id"])
    key = b"k" * 32
    now = SystemClock().now()
    request = drain_request(op, now, key)
    if corrupt == "key":
        key = b"x" * 32
    elif corrupt == "boot":
        identity["boot_id"] = UUIDs().new()
    elif corrupt == "operation":
        request["operation_id"] = UUIDs().new()
    elif corrupt == "stale":
        now = (dt.datetime.fromisoformat(now) + dt.timedelta(seconds=61)).isoformat()
    elif corrupt == "purpose":
        request["signature"] = drain_receipt(request, key)["signature"]
    else:
        receipt = drain_receipt(request, key)
        with pytest.raises(BusinessError):
            verify_receipt({**receipt, "accepted": 1}, request, key)
        return
    with pytest.raises(BusinessError):
        verify_drain(request, identity, key, now)


@pytest.mark.parametrize("bad", [None, "signature", "duplicate", "oversize", "remote"])
def test_http_drain_requires_local_hmac_and_persists_receipt_before_closing_admission(tmp_path, bad):
    deployment = plan()
    identity = boot_identity(deployment)
    key_state = {}
    events = []

    async def factory():
        publisher = RuntimeIdentityFiles(
            tmp_path, {key: value for key, value in identity.items() if key not in {"format", "state"}}
        )
        await publisher.start()
        key_state["publisher"] = publisher

        def accepted():
            record = tmp_path / "service-drains" / (identity["boot_id"] + ".json")
            assert record.is_file()
            events.append("draining")
            publisher.stop_accepting()

        async def drain(value):
            return await publisher.request_drain(value, SystemClock().now(), accepted)

        async def close():
            return {"complete": await publisher.close(2)}

        required = {
            field.name: None
            for field in dataclasses.fields(HttpRuntime)
            if field.default is dataclasses.MISSING and field.default_factory is dataclasses.MISSING
        }
        required.update(
            describe=lambda: {**identity, "ready": not events},
            close=close,
            operations=SimpleNamespace(observe=lambda *args: None),
            assets=tmp_path,
            identity_proof=lambda challenge: canonical_proof(
                {**identity, "ready": not events}, challenge, publisher.key
            ),
            drain=drain,
        )
        return HttpRuntime(**required)

    app = create_app(factory)
    with TestClient(app, client=("198.51.100.20" if bad == "remote" else "127.0.0.1", 1234)) as client:
        publisher = key_state["publisher"]
        op = make_control(deployment, "stop", UUIDs().new(), SystemClock().now(), identity["boot_id"])
        request = drain_request(op, SystemClock().now(), publisher.key)
        if bad == "signature":
            request["signature"] = "0" * 64
        if bad == "duplicate":
            body = json.dumps(request)[:-1] + ',"boot_id":"' + identity["boot_id"] + '"}'
            response = client.post("/api/v1/service/drain", content=body)
        elif bad == "oversize":
            response = client.post("/api/v1/service/drain", content=b"x" * 4097)
        else:
            response = client.post("/api/v1/service/drain", json=request)
        if bad is None:
            assert response.status_code == 200
            assert verify_receipt(response.json(), request, publisher.key)["accepted"] is True
            assert events == ["draining"]
            assert client.post("/api/v1/service/drain", json=request).status_code == 200 and len(events) == 1
        else:
            assert response.status_code in {403, 409, 413, 422}
            assert events == [] and not (tmp_path / "service-drains").exists()


def test_cancelled_http_waiter_does_not_abandon_server_owned_audit_and_admission(tmp_path, monkeypatch):
    identity = boot_identity(plan())

    async def scenario():
        publisher = RuntimeIdentityFiles(
            tmp_path, {key: value for key, value in identity.items() if key not in {"format", "state"}}
        )
        await publisher.start()
        entered, release = asyncio.Event(), asyncio.Event()
        original = publisher.prepare_drain
        events = []

        async def prepare(request, now):
            entered.set()
            await release.wait()
            return await original(request, now)

        monkeypatch.setattr(publisher, "prepare_drain", prepare)
        op = make_control(plan(), "stop", UUIDs().new(), SystemClock().now(), identity["boot_id"])
        request = drain_request(op, SystemClock().now(), publisher.key)
        task = asyncio.create_task(
            publisher.request_drain(request, SystemClock().now(), lambda: events.append("drain"))
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not await publisher.close(0) and not events
        release.set()
        await publisher._control
        assert events == ["drain"] and await publisher.close(2)

    asyncio.run(scenario())


@pytest.mark.parametrize("target", ["linux-user", "macos-user", "windows-user"])
def test_drifted_definition_is_denied_before_native_start(target, monkeypatch):
    async def scenario():
        native = ServiceRegistration()

        async def inspect(_):
            return {
                "available": True,
                "installed": True,
                "definition_matches": False,
                "enabled": True,
                "state": "stopped",
            }

        monkeypatch.setattr(native, "_supported", lambda value: True)
        monkeypatch.setattr(native, "inspect", inspect)
        monkeypatch.setattr(native, "_program", lambda value: pytest.fail("Drift must not reach native dispatch"))
        try:
            assert (await native.start(plan(target)))["effect"] == "none"
        finally:
            assert await native.close(2)

    asyncio.run(scenario())


def test_shutdown_supervisor_keeps_owned_cleanup_after_normal_budget_expires():
    from omni.composition.lifecycle import close_until_quiescent

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def close(budget):
            calls.append(budget)
            if len(calls) == 1:
                return {"complete": False, "residual": ["owned_native"]}
            entered.set()
            await release.wait()
            return {"complete": True, "residual": []}

        task = asyncio.create_task(close_until_quiescent(close, 0))
        await entered.wait()
        assert not task.done() and calls == [0, 5]
        release.set()
        assert (await task)["complete"] is True

    asyncio.run(scenario())


@pytest.mark.parametrize("attack", [None, "fake_listener", "bad_receipt", "compressed", "wrong_boot"])
def test_client_proves_endpoint_before_sending_drain_and_never_reposts(attack, monkeypatch):
    deployment = plan()
    identity = boot_identity(deployment)
    op = make_control(deployment, "stop", UUIDs().new(), SystemClock().now(), identity["boot_id"])
    if attack == "wrong_boot":
        identity["boot_id"] = UUIDs().new()
    key, calls = b"p" * 32, []
    monkeypatch.setattr(readiness, "_preflight", lambda _: (identity, key, "http://127.0.0.1:8766"))
    client_type = httpx.AsyncClient

    def handler(request):
        calls.append(request)
        assert "authorization" not in request.headers and "cookie" not in request.headers
        if request.method == "GET":
            return httpx.Response(
                200,
                json=canonical_proof(
                    {**identity, "ready": True},
                    request.url.params["challenge"],
                    b"x" * 32 if attack == "fake_listener" else key,
                ),
                headers={"Set-Cookie": "must-not-forward=private"},
            )
        payload = json.loads(request.content)
        checked = verify_drain(payload, identity, key, SystemClock().now())
        receipt = drain_receipt(checked, b"x" * 32 if attack == "bad_receipt" else key)
        return httpx.Response(200, json=receipt, headers={"Content-Encoding": "gzip"} if attack == "compressed" else {})

    monkeypatch.setattr(
        readiness.httpx, "AsyncClient", lambda **kwargs: client_type(**kwargs, transport=httpx.MockTransport(handler))
    )

    async def scenario():
        client = readiness.ServiceReadiness()
        try:
            result = await client.drain(op)
            assert result["accepted"] is (attack is None)
            assert sum(item.method == "POST" for item in calls) == (
                0 if attack in {"fake_listener", "wrong_boot"} else 1
            )
            assert key.hex() not in json.dumps(result)
        finally:
            assert await client.close(2)

    asyncio.run(scenario())


@pytest.mark.parametrize("target", ["linux-user", "macos-user", "windows-user"])
def test_native_start_uses_fixed_non_force_commands_and_requires_matching_idle_definition(target, monkeypatch):
    async def scenario():
        native = ServiceRegistration()
        value = plan(target)
        calls = []

        async def inspect(_):
            return {
                "available": True,
                "installed": True,
                "definition_present": True,
                "definition_matches": True,
                "enabled": True,
                "state": "stopped",
                "loaded": False,
                "pid": None,
            }

        async def command(argv):
            calls.append(argv)
            return 0, b""

        async def windows(value, action):
            calls.append(["fixed-windows-helper", action])
            return {"effect": "applied"}

        monkeypatch.setattr(native, "_supported", lambda value: True)
        monkeypatch.setattr(native, "inspect", inspect)
        monkeypatch.setattr(native, "_program", lambda value: "/fixed/manager")
        monkeypatch.setattr(native, "_command", command)
        monkeypatch.setattr(native, "_windows", windows)
        try:
            assert (await native.start(value))["effect"] == "applied"
            assert len(calls) == 1 and not {"stop", "restart", "--now", "--force", "-k", "sudo"} & set(calls[0])
        finally:
            assert await native.close(2)

    asyncio.run(scenario())
