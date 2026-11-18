"""Reusable delivery_http fixtures, extracted without collecting test cases."""

import time

from fastapi.testclient import TestClient

from omni.composition.http import http_app
from omni_test_support.v2.http import LocalModel
from omni_test_support.v2.v2_delivery_helpers import FakePort


def seed(client):
    agent = client.get("/api/v1/agents").json()["items"][0]["id"]
    base = f"/api/v1/agents/{agent}"
    settings = client.get(base + "/settings").json()
    profile = client.get(base + "/configurations/" + settings["value"]["model_profile_id"]).json()
    assert (
        client.put(
            base + "/configurations/" + profile["id"],
            json={
                "name": profile["name"],
                "enabled": True,
                "revision": profile["revision"],
                "value": {**profile["value"], "model": "fake"},
            },
        ).status_code
        == 200
    )
    channel = client.post(
        base + "/configurations",
        json={
            "kind": "channel",
            "name": "Fixture",
            "enabled": True,
            "value": {"driver": "qq", "account": "account-one", "credential_ref": "fixture/key"},
        },
    ).json()
    receipt = client.post(base + "/turns", json={"key": "turn", "message": "private-message-canary"}).json()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        run = client.get(base + "/runs/" + receipt["run_id"]).json()
        if run["state"] == "succeeded":
            break
        time.sleep(0.02)
    assert run["state"] == "succeeded", run
    message = client.get(base + "/sessions/" + receipt["session_id"] + "/messages").json()["items"][-1]
    return base, channel, message, receipt


__all__ = ["FakePort", "LocalModel", "TestClient", "http_app", "seed", "time"]
