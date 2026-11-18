"""Reusable desktop_control_service fixtures, extracted without collecting test cases."""

import asyncio
import json
import sys
import uuid
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from omni.adapters.persistence.repository import Repositories
from omni.adapters.platform.desktop.desktop_control import DesktopControlBackend
from omni.adapters.platform.runtime.processes import OwnedProcess
from omni.composition.http import http_app
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError
from omni.domain.desktop_control import PURPOSE
from omni_test_support.v2.desktop import begin
from omni_test_support.v2.desktop_control_backend import PROGRAM, linux
from omni_test_support.v2.desktop_http import begin as http_begin
from omni_test_support.v2.desktop_http import owner as http_owner
from omni_test_support.v2.v2_desktop_helpers import FixtureDesktopBackend


def request(view, mask=3):
    return {"view_id": view["view_id"], "purpose": PURPOSE, "consent": True, "devices": mask}


def backend(owners):
    async def spawn(argv, **options):
        child = await OwnedProcess.spawn([sys.executable, "-u", "-c", PROGRAM], **options)
        owners.append(child)
        return child

    return DesktopControlBackend(process_factory=spawn)


@asynccontextmanager
async def prepared(tmp_path, monkeypatch):
    linux(monkeypatch)
    owners = []
    native = backend(owners)
    instance = await open_instance(
        tmp_path / "home", capabilities=(), desktop_backend=FixtureDesktopBackend(), control_backend=native
    )
    agent = (await instance.application.query("agents", ""))[0]["id"]
    try:
        yield instance, agent, native, owners
    finally:
        assert (await instance.close())["complete"]
        assert all(child.reclaimed for child in owners)


async def settled(service, agent, view, wanted):
    async with asyncio.timeout(5):
        while True:
            result = (await service.status(agent, view["view_id"]))["grant"]
            if (
                result is not None
                and result["phase"] == wanted
                and (wanted != "system_authorized" or result["system_permission_recorded"])
            ):
                return result
            await asyncio.sleep(0)


__all__ = [
    "BusinessError",
    "DesktopControlBackend",
    "FixtureDesktopBackend",
    "OwnedProcess",
    "PROGRAM",
    "PURPOSE",
    "Repositories",
    "TestClient",
    "asynccontextmanager",
    "asyncio",
    "backend",
    "begin",
    "http_app",
    "http_begin",
    "http_owner",
    "json",
    "linux",
    "open_instance",
    "prepared",
    "pytest",
    "request",
    "settled",
    "sys",
    "uuid",
]
