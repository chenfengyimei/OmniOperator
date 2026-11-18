"""Reusable linux_desktop fixtures, extracted without collecting test cases."""

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

from omni.adapters.platform.desktop.linux.linux_atspi import AtspiDesktop, parse_native_id
from omni.adapters.platform.desktop.linux.linux_bus import Process, RemoteError
from omni.adapters.platform.desktop.linux.linux_session import SessionInspector
from omni.adapters.workers.linux_desktop import perform
from omni.composition.http import http_app
from omni.domain.contracts import BusinessError
from omni.domain.desktop import VERSION, parse_response, validate_capabilities, validate_observation
from omni_test_support.v2.desktop import verification
from omni_test_support.v2.desktop_backend import Owner, encoded
from omni_test_support.v2.desktop_backend import factory as process_factory
from omni_test_support.v2.desktop_http import begin, listing, owner
from omni_test_support.v2.v2_desktop_helpers import FixtureDesktopBackend
from omni_test_support.v2.v2_linux_desktop_helpers import APP, SESSION, WINDOW, Factory, node, variant


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


__all__ = [
    "APP",
    "AtspiDesktop",
    "BusinessError",
    "Factory",
    "FixtureDesktopBackend",
    "Owner",
    "Process",
    "RemoteError",
    "SESSION",
    "SessionInspector",
    "SimpleNamespace",
    "TestClient",
    "VERSION",
    "WINDOW",
    "asyncio",
    "begin",
    "copy",
    "encoded",
    "http_app",
    "io",
    "json",
    "listing",
    "node",
    "owner",
    "parse_native_id",
    "parse_response",
    "perform",
    "prepared",
    "process_factory",
    "pytest",
    "request",
    "sys",
    "uuid",
    "validate_capabilities",
    "validate_observation",
    "variant",
    "verification",
]
