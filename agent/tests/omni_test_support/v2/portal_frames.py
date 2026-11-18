"""Reusable portal_frames fixtures, extracted without collecting test cases."""

import asyncio
import hashlib
import io
import sys
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from omni.adapters.platform.desktop import portal as host
from omni.adapters.platform.desktop.linux.linux_pipewire import FrameExecutor, rgb_png
from omni.composition.http import http_app
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError
from omni_test_support.v2.desktop import begin
from omni_test_support.v2.desktop_http import begin as http_begin
from omni_test_support.v2.desktop_http import owner
from omni_test_support.v2.portal_service import settled
from omni_test_support.v2.v2_desktop_helpers import FixtureDesktopBackend
from omni_test_support.v2.v2_portal_helpers import Spawner


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


__all__ = [
    "BusinessError",
    "FixtureDesktopBackend",
    "FrameExecutor",
    "Image",
    "Pixels",
    "SimpleNamespace",
    "Spawner",
    "TestClient",
    "asyncio",
    "backend",
    "begin",
    "hashlib",
    "host",
    "http_app",
    "http_begin",
    "io",
    "open_instance",
    "owner",
    "pytest",
    "rgb_png",
    "settled",
    "sys",
    "threading",
]
