"""Reusable portal_service fixtures, extracted without collecting test cases."""

import asyncio
import json
import sys
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from omni.adapters.persistence.repository import Repositories
from omni.adapters.platform.desktop import portal as host
from omni.composition.http import http_app
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError
from omni_test_support.v2.desktop import begin
from omni_test_support.v2.desktop_http import begin as http_begin
from omni_test_support.v2.desktop_http import owner
from omni_test_support.v2.v2_desktop_helpers import FixtureDesktopBackend
from omni_test_support.v2.v2_portal_helpers import Spawner


@asynccontextmanager
async def prepared(tmp_path, monkeypatch, *, hold=False, fault=None):
    monkeypatch.setattr(host, "sys", SimpleNamespace(platform="linux", executable=sys.executable))
    spawn = Spawner(hold=hold, fault=fault)
    backend = host.PortalBackend(process_factory=spawn)
    instance = await open_instance(
        tmp_path / "home", capabilities=(), desktop_backend=FixtureDesktopBackend(), portal_backend=backend
    )
    agent = (await instance.application.query("agents", ""))[0]["id"]
    try:
        yield instance, spawn, backend, agent
    finally:
        assert (await instance.close())["complete"]


async def settled(service, agent, view, wanted):
    async with asyncio.timeout(5):
        while True:
            result = await service.status(agent, view["view_id"])
            grant = result["grant"]
            if grant is not None and grant["phase"] == wanted and (wanted != "active" or grant["authorized"]):
                return grant
            await asyncio.sleep(0)


__all__ = [
    "BusinessError",
    "FixtureDesktopBackend",
    "Repositories",
    "SimpleNamespace",
    "Spawner",
    "TestClient",
    "asynccontextmanager",
    "asyncio",
    "begin",
    "host",
    "http_app",
    "http_begin",
    "json",
    "open_instance",
    "owner",
    "prepared",
    "pytest",
    "settled",
    "sys",
    "uuid",
]
