"""Reusable extensions_http fixtures, extracted without collecting test cases."""

import uuid

from fastapi.testclient import TestClient

from omni.adapters.persistence.migration_config import head as migration_head
from omni.composition.http import http_app
from omni_test_support.v2.v2_extension_helpers import authorization, installation, plugin_source


def owner(client, home):
    client.headers["Authorization"] = "Bearer " + (home / "run/owner.token").read_text()
    agent = client.get("/api/v1/agents").json()["items"][0]["id"]
    return agent, f"/api/v1/agents/{agent}/extensions"


__all__ = [
    "TestClient",
    "authorization",
    "http_app",
    "installation",
    "migration_head",
    "owner",
    "plugin_source",
    "uuid",
]
