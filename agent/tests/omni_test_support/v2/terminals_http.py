"""Reusable terminals_http fixtures, extracted without collecting test cases."""

import uuid

from fastapi.testclient import TestClient

from omni.adapters.persistence.migration_config import head as migration_head
from omni.composition.http import http_app
from omni_test_support.v2.v2_terminal_helpers import FixtureTerminalBackend, configure_workspace, open_request


def owner(client, home, workspace):
    client.headers["Authorization"] = "Bearer " + (home / "run/owner.token").read_text()
    agent = client.get("/api/v1/agents").json()["items"][0]["id"]
    db = client.app.state.runtime.application._db
    client.portal.call(db.write, lambda u: configure_workspace(u, agent, workspace))
    return agent, f"/api/v1/agents/{agent}", db


__all__ = [
    "FixtureTerminalBackend",
    "TestClient",
    "configure_workspace",
    "http_app",
    "migration_head",
    "open_request",
    "owner",
    "uuid",
]
