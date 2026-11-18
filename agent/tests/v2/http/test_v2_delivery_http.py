"""Authenticated bounded delivery API; fake adapter acceptance is not a real send."""

from omni_test_support.v2.delivery_http import (
    FakePort as FakePort,
)
from omni_test_support.v2.delivery_http import (
    LocalModel as LocalModel,
)
from omni_test_support.v2.delivery_http import (
    TestClient as TestClient,
)
from omni_test_support.v2.delivery_http import (
    http_app as http_app,
)
from omni_test_support.v2.delivery_http import (
    seed as seed,
)
from omni_test_support.v2.delivery_http import (
    time as time,
)


def test_delivery_http_identity_limits_cas_receipts_and_terminal_run_cancel(tmp_path):
    port = FakePort()
    with TestClient(http_app(tmp_path, model=LocalModel(), delivery_ports={"qq": port})) as client:
        assert client.get("/api/v1/agents/00000000-0000-0000-0000-000000000000/deliveries").status_code == 401
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text()
        base, channel, message, receipt = seed(client)
        path = base + "/deliveries"
        payload = {
            "message_id": message["id"],
            "config_id": channel["id"],
            "target": "fixture:target",
            "key": "delivery-key",
        }
        assert client.post(path, content=b" " * 8193).status_code == 413
        assert client.post(path, json={**payload, "text": "not-accepted"}).status_code == 422
        row = client.post(path, json=payload).json()
        assert row["state"] == "awaiting_approval" and port.calls == []
        assert client.post(path, json=payload).json()["id"] == row["id"]
        assert client.get(path + "?limit=201").status_code == 422
        decision = {"allowed": True, "revision": row["revision"], "fingerprint": row["fingerprint"]}
        assert client.post(path + "/" + row["id"] + "/decision", json={**decision, "allowed": 1}).status_code == 422
        assert client.post(path + "/" + row["id"] + "/decision", json=decision).status_code == 200
        assert client.post(path + "/" + row["id"] + "/decision", json=decision).status_code == 409
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            final = client.get(path + "/" + row["id"]).json()
            if final["state"] == "sent":
                break
            time.sleep(0.02)
        assert final["state"] == "sent" and len(port.calls) == 1
        attempts = client.get(path + "/" + row["id"] + "/attempts").json()["items"]
        assert attempts[0]["results"][0]["receipt_id"] == "fixture-receipt"
        assert "token" not in attempts[0]
        other = client.post(path, json={**payload, "target": "other", "key": "cancel-me"}).json()
        assert client.post(base + "/runs/" + receipt["run_id"] + "/cancel").status_code == 200
        assert client.get(path + "/" + other["id"]).json()["state"] == "cancelled"
        assert client.get(base + "/runs/" + receipt["run_id"]).json()["state"] == "succeeded"
        assert client.delete(path + "/" + row["id"], params={"revision": final["revision"]}).status_code == 200
        assert len(client.get(path).json()["items"]) == 1
        assert len(client.get(path + "?include_archived=true").json()["items"]) == 2
        logs = client.get("/api/v1/system/logs").text
        assert "private-message-canary" not in logs and "verified reply" not in logs


def test_default_delivery_capability_reports_installed_qq_and_rejects_unconfigured_sender(tmp_path):
    with TestClient(http_app(tmp_path, model=LocalModel())) as client:
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text()
        base, channel, message, _ = seed(client)
        report = client.get(base + "/deliveries/capabilities").json()
        assert report["items"] == [
            {"driver": "qq", "adapter_id": "onebot11-text/v2", "installed": True},
            {"driver": "bilibili", "adapter_id": "bilibili-ui/v2", "installed": True},
        ]
        assert report["approval_required"] is True
        response = client.post(
            base + "/deliveries",
            json={"message_id": message["id"], "config_id": channel["id"], "target": "fixture", "key": "no-real-send"},
        )
        assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_qq_connection"


def test_credential_reference_with_slash_revokes_pending_intent_and_stays_private(tmp_path):
    from cryptography.fernet import Fernet

    home = tmp_path / "instance"
    key_file = tmp_path / "external-master.key"
    key_file.write_bytes(Fernet.generate_key())
    key_file.chmod(0o600)
    port = FakePort()
    with TestClient(
        http_app(home, model=LocalModel(), delivery_ports={"qq": port}, master_key_file=key_file)
    ) as client:
        client.headers["Authorization"] = "Bearer " + (home / "run/owner.token").read_text()
        base, channel, message, _ = seed(client)
        row = client.post(
            base + "/deliveries",
            json={
                "message_id": message["id"],
                "config_id": channel["id"],
                "target": "fixture",
                "key": "credential-test",
            },
        ).json()
        canary = "fixture-private-credential-rotation-canary"
        saved = client.put("/api/v1/credentials", params={"reference": "fixture/key"}, json={"value": canary})
        assert saved.status_code == 200, saved.text
        assert client.get(base + "/deliveries/" + row["id"]).json()["state"] == "cancelled"
        assert port.calls == []
        assert len(list((home / "private/vault").glob("*.secret"))) == 1
        assert canary not in client.get("/api/v1/system/logs").text
        assert (
            client.put("/api/v1/credentials", params={"reference": "not a ref"}, json={"value": canary}).status_code
            == 422
        )
        assert (
            client.put("/api/v1/credentials", params={"reference": "x" * 201}, json={"value": canary}).status_code
            == 422
        )
        assert (
            client.put("/api/v1/credentials", params={"reference": "fixture/key"}, content=b" " * 266241).status_code
            == 413
        )
        assert client.delete("/api/v1/credentials", params={"reference": "fixture/key"}).status_code == 200
        assert not list((home / "private/vault").glob("*.secret"))
