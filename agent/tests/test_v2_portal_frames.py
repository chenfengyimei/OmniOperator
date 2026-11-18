"""Actual Portal worker/host/application/object path with injected pixels and peers, never a native screen or bus."""

import asyncio
import hashlib
import io
import sys
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from test_v2_desktop import begin
from test_v2_desktop_http import begin as http_begin
from test_v2_desktop_http import owner
from test_v2_portal_service import settled
from v2_desktop_helpers import FixtureDesktopBackend
from v2_portal_helpers import Spawner

from omni.adapters.platform.desktop import portal as host
from omni.adapters.platform.desktop.linux.linux_pipewire import FrameExecutor, rgb_png
from omni.composition.http import http_app
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError


class Pixels:
    def __init__(self, *, hold=False):
        self.hold = hold
        self.entered, self.release = threading.Event(), threading.Event()
        self.calls = []

    def probe(self):
        return None

    def read(self, remote, serial, path, stopped):
        self.calls.append((remote.fileno(), serial))
        self.entered.set()
        while self.hold and not self.release.wait(0.01):
            if stopped.is_set():
                raise BusinessError("portal_revoked", "injected stopped", status=409)
        content = rgb_png(2, 2, 8, 0, b"\xff\x00\x00\x00\xff\x00\x00\x00" + b"\x00\x00\xff\xff\xff\xff\x00\x00")
        path.write_bytes(content)
        return {
            "width": 2,
            "height": 2,
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "method": "pipewire_window",
        }


def backend(tmp_path, monkeypatch, *, hold=False):
    monkeypatch.setattr(host, "sys", SimpleNamespace(platform="linux", executable=sys.executable))
    pixels = Pixels(hold=hold)
    spawn = Spawner(frame_factory=lambda: FrameExecutor(pixels))
    return host.PortalBackend(process_factory=spawn, capture_root=tmp_path / "staging"), pixels, spawn


def test_explicit_api_frame_is_verified_published_scoped_and_released(tmp_path, monkeypatch):
    io_backend, pixels, spawn = backend(tmp_path, monkeypatch)
    home = tmp_path / "home"
    with TestClient(http_app(home, desktop_backend=FixtureDesktopBackend(), portal_backend=io_backend)) as client:
        agent, base, db = owner(client, home)
        turn = client.portal.call(db.write, lambda u: u.accept_turn(agent, None, "sample", "fixture", {}))
        view = http_begin(client, base)
        response = client.post(base + "/portal", json={"consent": True, "view_id": view["view_id"]})
        assert response.status_code == 202, response.text
        grant = response.json()

        async def active():
            async with asyncio.timeout(5):
                while True:
                    value = await io_backend.status(next(h.binding for h in io_backend._hosts.values()))
                    if value["phase"] == "active":
                        return
                    await asyncio.sleep(0)

        client.portal.call(active)
        # The public permission also waits for durable authorization audit.
        for _ in range(100):
            current = client.get(base + "/portal", params={"view_id": view["view_id"]}).json()["grant"]
            if current["authorized"]:
                break
        assert current["capture_available"] and not current["input_authorized"]
        request = {
            "consent": True,
            "view_id": view["view_id"],
            "grant_id": grant["grant_id"],
            "session_id": turn["session_id"],
        }
        for extra in ({"fd": 3}, {"serial": 123456}, {"capture_path": "private"}, {"consent": False}):
            assert client.post(base + "/portal/capture", json={**request, **extra}).status_code == 422
        other = client.portal.call(db.write, lambda u: u.create_agent("other"))["id"]
        other_turn = client.portal.call(db.write, lambda u: u.accept_turn(other, None, "other", "fixture", {}))
        assert (
            client.post(base + "/portal/capture", json={**request, "session_id": other_turn["session_id"]}).status_code
            == 404
        )
        assert pixels.calls == []
        captured = client.post(base + "/portal/capture", json=request)
        assert captured.status_code == 200, captured.text
        record = captured.json()
        assert record["method"] == "pipewire_window" and record["attached_to_model"] is False
        assert pixels.calls[0][1] == 123456
        assert all(remote.descriptor == -1 for f in spawn.factories for remote in f.buses["user"].remotes)
        row = client.portal.call(db.read, lambda u: u.object_get(agent, record["object_id"]))
        assert row["references"] == 1 and not list((tmp_path / "staging").iterdir())
        released = client.delete(f"/api/v1/agents/{agent}/sessions/{turn['session_id']}/captures/{record['id']}")
        assert released.status_code == 204, released.text
        assert client.portal.call(db.read, lambda u: u.object_get(agent, record["object_id"]))["references"] == 0
    assert all(o.reclaimed for o in spawn.owners)


def test_canceled_http_waiter_retains_real_frame_and_view_revoke_discards_it(tmp_path, monkeypatch):
    async def scenario():
        io_backend, pixels, spawn = backend(tmp_path, monkeypatch, hold=True)
        instance = await open_instance(
            tmp_path / "home", capabilities=(), desktop_backend=FixtureDesktopBackend(), portal_backend=io_backend
        )
        task = None
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            turn = await instance._database.write(lambda u: u.accept_turn(agent, None, "sample", "fixture", {}))
            view = await begin(instance.desktop, agent)
            grant = await instance.desktop_portal.open(agent, view)
            await settled(instance.desktop_portal, agent, view, "active")
            task = asyncio.create_task(
                instance.desktop_portal.capture(
                    agent, {**view, "grant_id": grant["grant_id"], "session_id": turn["session_id"]}
                )
            )
            async with asyncio.timeout(5):
                while not pixels.entered.is_set():
                    await asyncio.sleep(0)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            assert io_backend.health()["captures_pending"] == 1
            await instance.desktop.forget(agent, view["view_id"])
            async with asyncio.timeout(5):
                while io_backend.health()["captures_pending"]:
                    await asyncio.sleep(0)
            assert await instance.objects.captures(agent, turn["session_id"]) == {"items": [], "next_after": None}
            assert all(o.reclaimed for o in spawn.owners)
            assert io_backend.health()["staging_pending"] == 0
        finally:
            pixels.release.set()
            assert (await instance.close())["complete"]
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_failed_staging_cleanup_remains_owned_and_close_can_retry(tmp_path, monkeypatch):
    async def scenario():
        import uuid

        from v2_portal_helpers import phase

        io_backend, _pixels, spawn = backend(tmp_path, monkeypatch)
        scope = {key: str(uuid.uuid4()) for key in ("agent_id", "view_id", "boot_id", "grant_id")}
        original = host.desktop_capture.discard
        failed = False

        def discard(root, path):
            nonlocal failed
            if not failed:
                failed = True
                raise OSError("private-staging-canary")
            return original(root, path)

        monkeypatch.setattr(host.desktop_capture, "discard", discard)

        async def guard():
            return None

        try:
            await io_backend.open(scope, before_dispatch=guard)
            await phase(io_backend, scope, "active")
            with pytest.raises(BusinessError) as unavailable:
                await io_backend.capture(scope, before_dispatch=guard)
            assert unavailable.value.code == "portal_frame_staging_unavailable" and "private-staging" not in str(
                unavailable.value
            )
            assert io_backend.health()["staging_pending"] == io_backend.health()["cleanup_pending"] == 1
            assert await io_backend.close(6)
            assert not list((tmp_path / "staging").iterdir())
            assert all(o.reclaimed for o in spawn.owners)
        finally:
            assert await io_backend.close(6)

    asyncio.run(scenario())


def test_stride_padding_and_colors_are_not_interpreted_as_pixels():
    content = rgb_png(2, 2, 8, 0, b"\xff\x00\x00\x00\xff\x00XX" + b"\x00\x00\xff\xff\xff\xffXX")
    with Image.open(io.BytesIO(content)) as picture:
        assert picture.size == (2, 2) and [picture.getpixel(p) for p in ((0, 0), (1, 0), (0, 1), (1, 1))] == [
            (255, 0, 0),
            (0, 255, 0),
            (0, 0, 255),
            (255, 255, 255),
        ]


@pytest.mark.parametrize(
    "width,height,stride,offset,raw",
    [
        (0, 1, 3, 0, b"abc"),
        (4097, 1, 12291, 0, b"abc"),
        (4096, 4096, 12288, 0, b"abc"),
        (2, 2, 5, 0, b"x" * 20),
        (2, 2, 8, -1, b"x" * 20),
        (2, 2, 8, 100, b"x" * 20),
    ],
)
def test_invalid_frame_layout_is_rejected_before_decoding(width, height, stride, offset, raw):
    with pytest.raises(BusinessError):
        rgb_png(width, height, stride, offset, raw)


def test_uniform_content_is_explicitly_unverified():
    with pytest.raises(BusinessError, match="Window frame") as failed:
        rgb_png(2, 2, 6, 0, b"x" * 12)
    assert failed.value.code == "desktop_capture_blank"


def test_closing_during_dependency_probe_reaps_actual_thread_without_false_helper_failure():
    from v2_portal_helpers import portal_factory

    from omni.adapters.platform.desktop.linux.linux_portal import PortalSession
    from omni.adapters.platform.desktop.linux.linux_session import SessionInspector
    from omni.adapters.workers.portal import run
    from omni.domain.desktop_portal import VERSION, decode, encode

    async def scenario():
        pixels = Pixels()

        def probe():
            pixels.entered.set()
            pixels.release.wait()

        pixels.probe = probe
        frames = FrameExecutor(pixels)
        factory = portal_factory()
        import uuid

        scope = {key: str(uuid.uuid4()) for key in ("agent_id", "view_id", "boot_id", "grant_id")}
        input = asyncio.StreamReader()
        messages = []

        async def send(data):
            messages.append(decode(data))

        input.feed_data(encode({"version": VERSION, "binding": scope, "command": "open"}))
        task = asyncio.create_task(
            run(
                input,
                send,
                session_factory=lambda: PortalSession(SessionInspector(factory.ops, factory)),
                frame_factory=lambda: frames,
            )
        )
        try:
            async with asyncio.timeout(5):
                while not pixels.entered.is_set():
                    await asyncio.sleep(0)
                input.feed_data(encode({"version": VERSION, "binding": scope, "command": "close"}))
                while not frames.stopped.is_set():
                    await asyncio.sleep(0)
                pixels.release.set()
                assert await task == 0
            assert messages[-1]["phase"] == "closed" and messages[-1]["clean"] is True
        finally:
            pixels.release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
