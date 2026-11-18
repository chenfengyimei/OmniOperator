"""Reusable desktop_http fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from omni.composition.http import http_app
from omni_test_support.v2.v2_desktop_helpers import FixtureDesktopBackend


def owner(client, home):
    client.headers["Authorization"] = "Bearer " + (home / "run/owner.token").read_text()
    agent = client.get("/api/v1/agents").json()["items"][0]["id"]
    return agent, f"/api/v1/agents/{agent}/desktop", client.app.state.runtime.application._db


def begin(client, base, view_id=None):
    body = {"consent": True, "view_id": view_id or str(uuid.uuid4())}
    response = client.post(base + "/view", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["view_id"] == body["view_id"]
    return body


def listing(client, base, body):
    response = client.post(base + "/targets", json={**body, "limit": 10})
    assert response.status_code == 200, response.text
    return response.json()


__all__ = [
    "FixtureDesktopBackend",
    "TestClient",
    "asyncio",
    "begin",
    "http_app",
    "httpx",
    "listing",
    "owner",
    "pytest",
    "uuid",
]
