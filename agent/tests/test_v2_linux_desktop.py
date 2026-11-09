"""Linux production chain with injected logind/AT-SPI peers; no native platform claim."""

from __future__ import annotations

import asyncio
import copy
import io
import json
import sys
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_v2_desktop import verification
from test_v2_desktop_backend import Owner, encoded
from test_v2_desktop_backend import factory as process_factory
from test_v2_desktop_http import begin, listing, owner
from v2_desktop_helpers import FixtureDesktopBackend
from v2_linux_desktop_helpers import APP, SESSION, WINDOW, Factory, node, variant

from omni.adapters.platform.linux_atspi import AtspiDesktop, parse_native_id
from omni.adapters.platform.linux_bus import Process, RemoteError
from omni.adapters.platform.linux_session import SessionInspector
from omni.adapters.workers.linux_desktop import perform
from omni.composition.http import http_app
from omni.domain.contracts import BusinessError
from omni.domain.desktop import VERSION, parse_response, validate_capabilities, validate_observation


def request(operation, target=None):
    return {
        "version": VERSION,
        "request_id": str(uuid.uuid4()),
        "operation": operation,
        "target": target,
        "limit": 5 if operation == "targets" else 1,
    }


async def prepared(factory):
    adapter = AtspiDesktop(SessionInspector(factory.ops, factory))
    await adapter.setup()
    return adapter


def test_linux_probe_proves_session_socket_service_and_only_read_capabilities(monkeypatch):
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "tcp:host=private-canary,port=1")
    monkeypatch.setenv("DISPLAY", "private.example:99")

    async def scenario():
        f = Factory()
        query = request("probe")
        response = await perform(query, ops=f.ops, factory=f)
        report = validate_capabilities(parse_response(response, query)["result"])
        assert report["platform"] == "linux" and report["session"]["kind"] == "interactive"
        assert [c["available"] for c in report["capabilities"]] == [True, True, False, False]
        assert report["acceptance"] == "pending"
        assert [e.address for e in f.opened] == [
            "/run/dbus/system_bus_socket",
            "/run/user/1000/bus",
            "/run/user/1000/at-spi/bus",
        ]
        assert all(b.closed for b in f.buses.values())
        assert "private-canary" not in json.dumps(response)
        assert not any(c[4] in {"StartServiceByName", "Set", "AddMatch"} for c in f.calls)

    asyncio.run(scenario())


def test_linux_target_and_observation_bind_session_and_keep_password_subtree_unread():
    async def scenario():
        f = Factory()
        adapter = await prepared(f)
        try:
            listing = await adapter.targets(5)
            assert len(listing["items"]) == 1 and not listing["truncated"]
            target = listing["items"][0]
            assert target["backend"] == "atspi" and target["pid"] == 20
            assert parse_native_id(target["native_id"]) == (APP, WINDOW)
            observed = await adapter.observe(target)
            assert validate_observation(observed, target) == observed
            assert observed["coordinate_space"] == "window" and not observed["truncated"]
            assert len(observed["nodes"]) == 3 and observed["nodes"][2]["text"] == "hello\n你好"
            password = observed["nodes"][1]
            assert password["password"] and password["name"] == password["text"] == ""
            assert not any({"reference", "actions"} & set(n) for n in observed["nodes"])
            reads = [c[4] for c in f.calls if c[2] == "/password"]
            assert reads == ["GetRole", "GetState"]
            assert (await adapter.verify(target))["matches"]
            changed = {**target, "process_created": target["process_created"] + 1}
            assert (await adapter.verify(changed))["matches"] is False
        finally:
            assert await adapter.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "key,signature,value,code",
    [
        ("LockedHint", "b", True, "desktop_session_unverified"),
        ("Remote", "b", True, "desktop_session_unverified"),
        ("Active", "b", False, "desktop_session_unverified"),
        ("Active", "b", 1, "desktop_session_unverified"),
        ("State", "s", "closing", "desktop_session_unverified"),
        ("Type", "s", "tty", "desktop_headless"),
        ("Class", "s", "greeter", "desktop_headless"),
        ("User", "(uo)", [1001, "/user"], "desktop_session_unverified"),
        ("TimestampMonotonic", "t", 0, "desktop_session_unverified"),
        ("TimestampMonotonic", "s", "1", "desktop_bus_response_invalid"),
    ],
)
def test_linux_refuses_unproven_logind_conditions_without_querying_user_content(key, signature, value, code):
    async def scenario():
        f = Factory()
        f.session[key] = variant(signature, value)
        query = request("targets")
        response = await perform(query, ops=f.ops, factory=f)
        assert parse_response(response, query)["error_code"] == code
        assert len(f.opened) == 1 and f.buses["system"].closed
        assert not any(c[0] == "a11y" for c in f.calls)

    asyncio.run(scenario())


def test_user_service_fallback_uses_only_logind_display_and_never_ssh_environment():
    async def scenario():
        f = Factory()
        f.sessions.pop(50)
        adapter = await prepared(f)
        assert adapter.inspector.session.path == SESSION
        await adapter.close()
        f = Factory()
        f.sessions.pop(50)
        f.user["Display"] = variant("(so)", ["", "/"])
        result = await perform(request("probe"), ops=f.ops, factory=f)
        assert result["result"]["session"] == {"kind": "headless", "identity": None}
        assert all(not c["available"] for c in result["result"]["capabilities"])
        f = Factory()
        f.session["Type"] = variant("s", "tty")
        await perform(request("probe"), ops=f.ops, factory=f)
        assert not any(c[4] == "GetUser" for c in f.calls)

    asyncio.run(scenario())


def test_same_uid_app_without_logind_membership_is_not_accepted_or_fake_empty_success():
    async def scenario():
        f = Factory()
        f.sessions.pop(20)
        response = await perform(request("targets"), ops=f.ops, factory=f)
        assert not response["ok"] and response["error_code"] == "desktop_target_session_unverified"
        assert not any(c[2] == WINDOW for c in f.calls)
        assert all(b.closed for b in f.buses.values())

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "change,code",
    [
        (lambda f: f.seat.update(ActiveSession=variant("(so)", ["c2", "/other"])), "desktop_session_unverified"),
        (lambda f: setattr(f.buses["a11y"], "guid", "4" * 32), "desktop_bus_peer_unverified"),
        (lambda f: f.ops.processes.update({3: Process(3, 1001, 102.0)}), "desktop_bus_peer_unverified"),
        (lambda f: setattr(f, "address", "tcp:host=example.com,port=1"), "desktop_bus_address_denied"),
    ],
)
def test_wrong_seat_bus_guid_service_uid_or_remote_address_is_rejected(change, code):
    async def scenario():
        f = Factory()
        change(f)
        result = await perform(request("targets"), ops=f.ops, factory=f)
        assert not result["ok"] and result["error_code"] == code
        assert sum(bus.closed for bus in f.buses.values()) == len(f.opened)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "change,code",
    [
        (lambda f: setattr(f.buses["a11y"], "replaced", True), "desktop_bus_peer_unverified"),
        (lambda f: f.ops.processes.update({3: Process(3, 1000, 900.0)}), "desktop_bus_identity_changed"),
        (lambda f: f.session.update(LockedHint=variant("b", True)), "desktop_session_unverified"),
        (lambda f: f.sessions.update({20: "/other"}), "desktop_target_session_unverified"),
        (
            lambda f: f.ops.processes.update({20: Process(20, 1000, 999.0, "/usr/bin/owned-fixture")}),
            "desktop_target_changed",
        ),
    ],
)
def test_identity_drift_discards_observation(change, code):
    async def scenario():
        f = Factory()
        adapter = await prepared(f)
        try:
            target = (await adapter.targets(5))["items"][0]
            change(f)
            with pytest.raises(BusinessError) as denied:
                await adapter.observe(target)
            assert denied.value.code == code
        finally:
            assert await adapter.close()

    asyncio.run(scenario())


def test_late_session_change_does_not_publish_private_content():
    async def scenario():
        f = Factory()
        adapter = await prepared(f)
        try:
            target = (await adapter.targets(5))["items"][0]

            def hook(bus, destination, path, iface, member, body):
                if path == "/text" and member == "GetText":
                    f.session["LockedHint"] = variant("b", True)

            f.hook = hook
            with pytest.raises(BusinessError, match="could not be verified"):
                await adapter.observe(target)
        finally:
            assert await adapter.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("shape", ["count", "depth", "text", "cycle", "foreign"])
def test_bounded_tree_and_text_never_publish_cross_process_subtree(shape):
    async def scenario():
        f = Factory()
        root = f.nodes[WINDOW]
        if shape == "count":
            root["children"] = [[APP, "/n" + str(i)] for i in range(140)]
            f.nodes.update({path: node() for _, path in root["children"]})
        elif shape == "depth":
            root["children"] = [[APP, "/n0"]]
            f.nodes.update(
                {"/n" + str(i): node(children=[[APP, "/n" + str(i + 1)]] if i < 12 else []) for i in range(13)}
            )
        elif shape == "text":
            root["children"] = [[APP, "/n" + str(i)] for i in range(20)]
            f.nodes.update({path: node(name="中" * 600, content="文" * 600) for _, path in root["children"]})
        elif shape == "cycle":
            root["children"] = [[APP, WINDOW]]
        else:
            root["children"] = [[":1.99", "/private"]]
        adapter = await prepared(f)
        try:
            target = (await adapter.targets(5))["items"][0]
            result = await adapter.observe(target)
            assert result["truncated"] and 1 <= len(result["nodes"]) <= 128
            assert sum(len(n[k].encode()) for n in result["nodes"] for k in ("name", "text")) <= 16384
            assert not any(c[1] == ":1.99" for c in f.calls)
        finally:
            assert await adapter.close()

    asyncio.run(scenario())


def test_helper_hides_provider_body_and_cleanup_failure_cannot_report_success():
    async def scenario():
        f = Factory()

        def fail(*args):
            raise RemoteError("private-provider-error-canary")

        f.hook = fail
        query = request("probe")
        response = await perform(query, ops=f.ops, factory=f)
        assert "private-provider" not in json.dumps(response)
        assert response["result"]["session"]["kind"] == "unknown"
        f = Factory()
        f.buses["a11y"].close_result = False
        response = await perform(query, ops=f.ops, factory=f)
        assert not response["ok"] and response["error_code"] == "desktop_cleanup_pending"
        assert all(b.closed for b in f.buses.values())
        f = Factory()
        f.ops.uid = 0
        response = await perform(request("targets"), ops=f.ops, factory=f)
        assert not response["ok"] and not f.opened

    asyncio.run(scenario())


def test_session_scope_is_stable_between_helpers_but_not_across_bus_restart():
    async def scenario():
        first, second = Factory(), Factory()
        a, b = await prepared(first), await prepared(second)
        try:
            assert a.inspector.scope == b.inspector.scope
            target = (await a.targets(5))["items"][0]
            assert (await b.verify(target))["matches"]
            b.inspector.scope = "linux:wayland:" + "f" * 64
            assert not (await b.verify(target))["matches"]
        finally:
            assert await a.close() and await b.close()

    asyncio.run(scenario())


def test_linux_window_coordinates_survive_http_without_persisting_content(tmp_path):
    f = Factory()

    async def make_result():
        adapter = await prepared(f)
        try:
            target = (await adapter.targets(5))["items"][0]
            return target, await adapter.observe(target)
        finally:
            assert await adapter.close()

    target, observation = asyncio.run(make_result())

    class LinuxFixture(FixtureDesktopBackend):
        async def observe(self, target, *, before_dispatch=None):
            await self._invoke("observe", target, before_dispatch)
            return copy.deepcopy(observation)

    backend = LinuxFixture()
    backend.items = [target]
    backend.report["platform"] = "linux"
    backend.report["session"] = {"kind": "interactive", "identity": target["session_id"]}
    with TestClient(http_app(tmp_path / "home", desktop_backend=backend)) as client:
        agent, base, db = owner(client, tmp_path / "home")
        view = begin(client, base)
        snapshot = listing(client, base, view)
        response = client.post(base + "/observe", json=verification(view, snapshot))
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["coordinate_space"] == "window" and result["nodes"][0]["name"] == "private-ui-canary"
        assert not any("actions" in n or "reference" in n for n in result["nodes"])
        audits = client.portal.call(db.read, lambda u: u.query("audit", agent))
        assert "private-ui-canary" not in json.dumps(audits)
    for p in (tmp_path / "home").rglob("*.jsonl"):
        assert "private-ui-canary" not in p.read_text(encoding="utf-8")


def test_linux_coordinate_space_is_mandatory_and_cannot_be_screen():
    async def scenario():
        f = Factory()
        adapter = await prepared(f)
        try:
            target = (await adapter.targets(5))["items"][0]
            observation = await adapter.observe(target)
            for mutation in (
                lambda v: v.pop("coordinate_space"),
                lambda v: v.update(coordinate_space="screen"),
                lambda v: v["nodes"][0].update(
                    reference={"root": [1], "runtime": [1], "role": "edit", "name_sha256": "0" * 64},
                    actions=["set_text"],
                ),
            ):
                invalid = copy.deepcopy(observation)
                mutation(invalid)
                with pytest.raises(BusinessError):
                    validate_observation(invalid, target)
            readonly = copy.deepcopy(observation)
            readonly["nodes"][0].update(reference=None, actions=[])
            assert validate_observation(readonly, target)["nodes"][0]["actions"] == []
        finally:
            assert await adapter.close()

    asyncio.run(scenario())


def test_linux_host_observe_uses_owned_python_helper_and_no_inherited_bus(monkeypatch):
    from omni.adapters.platform import desktop

    async def scenario():
        f = Factory()
        adapter = await prepared(f)
        try:
            target = (await adapter.targets(5))["items"][0]
            observation = await adapter.observe(target)
        finally:
            assert await adapter.close()
        monkeypatch.setattr(desktop, "sys", SimpleNamespace(platform="linux", executable=sys.executable))
        monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "tcp:host=private-canary,port=1")
        calls = []
        owned = Owner(builder=lambda query: encoded(query, observation))
        host = desktop.DesktopBackend(process_factory=process_factory(owned, calls=calls))
        try:
            assert (await host.observe(target))["coordinate_space"] == "window"
            argv, kwargs = calls[0]
            assert argv == [sys.executable, "-P", "-u", "-m", "omni.adapters.workers.desktop"]
            assert "DBUS_SESSION_BUS_ADDRESS" not in kwargs["env"]
            assert owned.reclaimed
            assert (await host.act(target, {}, before_dispatch=lambda: None))["status"] == "denied"
        finally:
            assert await host.close()

    asyncio.run(scenario())


def test_fixed_worker_routes_linux_protocol_to_async_adapter(monkeypatch):
    from omni.adapters.workers import desktop, linux_desktop

    f = Factory()
    query, output = request("probe"), io.BytesIO()
    monkeypatch.setattr(
        desktop,
        "sys",
        SimpleNamespace(
            platform="linux",
            stdin=SimpleNamespace(buffer=io.BytesIO(json.dumps(query).encode() + b"\n")),
            stdout=SimpleNamespace(buffer=output),
        ),
    )

    async def run(query):
        return await perform(query, ops=f.ops, factory=f)

    monkeypatch.setattr(linux_desktop, "perform", run)
    assert desktop.main() == 0
    response = parse_response(json.loads(output.getvalue()), query)
    assert response["ok"] and response["result"]["platform"] == "linux"
    assert all(bus.closed for bus in f.buses.values())


def test_linux_dependency_denial_is_structured_not_headless_or_fake_ready(monkeypatch):
    from omni.adapters.platform.linux_bus import failure
    from omni.adapters.workers import linux_desktop

    def missing():
        raise failure("desktop_dependency_missing")

    monkeypatch.setattr(linux_desktop, "load_types", missing)
    query = request("probe")
    response = asyncio.run(perform(query))
    report = parse_response(response, query)["result"]
    assert report["session"]["kind"] == "unknown"
    assert all(not c["available"] and not c["installed"] for c in report["capabilities"])
    assert report["capabilities"][0]["error_code"] == "desktop_dependency_missing"
