"""Reusable mcp_flow fixtures, extracted without collecting test cases."""

import time

from fastapi.testclient import TestClient

from omni.composition.http import http_app
from omni_test_support.v2.v2_mcp_helpers import CallModel, FixtureHTTP, stdio_server


class SeededSecrets:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, reference):
        return self.values.get(reference)

    def set(self, reference, value):
        self.values[reference] = value

    def delete(self, reference):
        self.values.pop(reference, None)


def owner_client(tmp_path, model, secrets=None):
    client = TestClient(http_app(tmp_path, model=model, credential_backend=secrets or SeededSecrets()))
    client.__enter__()
    client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text(encoding="utf-8")
    agent = client.get("/api/v1/agents").json()["items"][0]["id"]
    base = f"/api/v1/agents/{agent}"
    settings = client.get(base + "/settings").json()
    profile = client.get(base + "/configurations/" + settings["value"]["model_profile_id"]).json()
    changed = client.put(
        base + "/configurations/" + profile["id"],
        json={
            "name": profile["name"],
            "enabled": True,
            "revision": profile["revision"],
            "value": {**profile["value"], "model": "fake"},
        },
    )
    assert changed.status_code == 200, changed.text
    return client, agent, base


def configure(client, base, name, value):
    response = client.post(
        base + "/configurations", json={"kind": "mcp", "name": name, "enabled": True, "value": value}
    )
    assert response.status_code == 201, response.text
    return response.json()


def connect(client, base, config):
    return client.post(base + f"/mcp/servers/{config['id']}/connect", json={"revision": config["revision"]}).json()


def authorize_search(client, base, config, tool):
    catalog = client.get(base + f"/mcp/servers/{config['id']}/catalog").json()
    response = client.put(
        base + f"/mcp/servers/{config['id']}/tools/{tool['tool_id']}/authorization",
        json={
            "catalog_fingerprint": catalog["catalog"]["fingerprint"],
            "read_only": True,
            "authorized": True,
            "evidence": "owner reviewed this inputSchema",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def wait_run(client, base, run_id, states=("succeeded", "failed", "interrupted")):
    for _ in range(400):
        run = client.get(base + "/runs/" + run_id).json()
        if run["state"] in states:
            return run
        time.sleep(0.02)
    raise AssertionError(run)


__all__ = [
    "CallModel",
    "FixtureHTTP",
    "SeededSecrets",
    "TestClient",
    "authorize_search",
    "configure",
    "connect",
    "http_app",
    "owner_client",
    "stdio_server",
    "time",
    "wait_run",
]
