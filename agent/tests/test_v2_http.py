"""Authenticated V2 HTTP use cases use a fresh database and injected model."""

import json
import logging
import time

from fastapi.testclient import TestClient

from omni.application.objects import ObjectService
from omni.composition.http import http_app
from omni.domain.contracts import ModelReply
from omni.system.catalog import build_index


class LocalModel:
    async def complete(self, messages, tools, settings):
        return ModelReply("verified reply", cost_micro=0)


def test_auth_chat_and_revision_contract(tmp_path):
    with TestClient(http_app(tmp_path, model=LocalModel())) as client:
        declared = {
            (row["path"], row["method"])
            for row in build_index()["endpoints"]
            if row["scope"] == "v2" and row["path"].startswith("/api/v1/")
        }
        actual = {
            (path, method.upper())
            for path, methods in client.get("/openapi.json").json()["paths"].items()
            if path.startswith("/api/v1/")
            for method in methods
            if method.upper() in {"GET", "POST", "PUT", "DELETE"}
        }
        assert declared == actual
        assert client.get("/health/ready").status_code == 200
        assert client.get("/api/v1/agents").status_code == 401
        token = (tmp_path / "run/owner.token").read_text()
        client.headers["Authorization"] = "Bearer " + token
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        base = f"/api/v1/agents/{agent}"
        assert client.get("/").status_code == 200
        assert client.get("/assets/ui/v2/main.js").status_code == 200
        cfg = client.get(base + "/settings").json()
        identity = cfg["value"]["model_profile_id"]
        profile = client.get(base + "/configurations/" + identity).json()
        assert (
            client.put(
                base + "/configurations/" + identity,
                json={
                    "name": profile["name"],
                    "enabled": True,
                    "revision": profile["revision"],
                    "value": {**profile["value"], "model": "fake"},
                },
            ).status_code
            == 200
        )
        payload = {"revision": cfg["revision"], "value": cfg["value"]}
        assert client.put(base + "/settings", json=payload).status_code == 200
        assert client.put(base + "/settings", json=payload).status_code == 409
        receipt = client.post(base + "/turns", json={"key": "test", "message": "hello"}).json()
        duplicate = client.post(base + "/turns", json={"key": "test", "message": "hello"}).json()
        assert duplicate["duplicate"] and receipt["run_id"] == duplicate["run_id"]
        for _ in range(100):
            run = client.get(base + "/runs/" + receipt["run_id"]).json()
            if run["state"] == "succeeded":
                break
            time.sleep(0.02)
        assert run["state"] == "succeeded", run
        messages = client.get(base + "/sessions/" + receipt["session_id"] + "/messages").json()["items"]
        assert [m["content"] for m in messages] == ["hello", "verified reply"]
        events = client.get(base + "/runs/" + receipt["run_id"] + "/events").json()
        assert events["next_cursor"] and events["stream"]["high_water"] > 0
        assert (
            client.get(
                base + "/runs/" + receipt["run_id"] + "/events", params={"cursor": events["next_cursor"]}
            ).json()["items"]
            == []
        )
        assert client.get("/api/chat").status_code == 404
        assert client.get("/api/v1/agents", headers={"Origin": "https://untrusted.example"}).status_code == 403
        assert "verified reply" not in client.get("/api/v1/system/logs").text
        logging.getLogger("omni.v2.http-test").error("diagnostic-query-marker")
        for _ in range(100):
            page = client.get("/api/v1/system/logs", params={"level": "ERROR", "component": "omni.v2.http-test"}).json()
            if page["items"]:
                break
            time.sleep(0.02)
        assert [row["message"] for row in page["items"]] == ["diagnostic-query-marker"]
        resumed = client.get("/api/v1/system/logs", params={"cursor": page["next_cursor"]})
        assert resumed.status_code == 200 and resumed.json()["items"] == []
        malformed = client.get("/api/v1/system/logs", params={"cursor": "broken"})
        assert malformed.status_code == 422 and malformed.json()["error"]["code"] == "invalid_log_cursor"
        assert client.get("/api/v1/system/logs", params={"limit": 501}).status_code == 422


def test_authenticated_objects_are_bounded_pinned_and_downloaded_as_attachments(tmp_path):
    with TestClient(http_app(tmp_path, model=LocalModel())) as client:
        assert (
            client.post("/api/v1/agents/00000000-0000-0000-0000-000000000000/objects", content=b"private").status_code
            == 401
        )
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text(encoding="utf-8")
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        base = f"/api/v1/agents/{agent}/objects"
        published = client.post(base, content=b"<private document>", headers={"Content-Type": "text/html"})
        assert published.status_code == 201
        row = published.json()
        assert row["pinned"] and row["revision"] == 1
        assert client.post(base, content=b"<private document>").json()["id"] == row["id"]
        content = client.get(base + "/" + row["id"] + "/content")
        assert content.content == b"<private document>"
        assert content.headers["content-type"] == "application/octet-stream"
        assert content.headers["content-disposition"].startswith("attachment;")
        assert content.headers["x-content-type-options"] == "nosniff"
        update = {"pinned": False, "revision": 1}
        assert client.put(base + "/" + row["id"] + "/pin", json=update).status_code == 200
        assert client.put(base + "/" + row["id"] + "/pin", json=update).status_code == 409
        assert client.post(base, content=b"x" * (16 * 1024 * 1024 + 1)).status_code == 413
        assert len(client.get(base).json()["items"]) == 1
        assert "<private document>" not in client.get("/api/v1/system/logs").text


def test_unhandled_http_error_keeps_request_identity_without_exception_payload(tmp_path, monkeypatch):
    async def failed(*args, **kwargs):
        raise RuntimeError("private-http-exception-canary")

    monkeypatch.setattr(ObjectService, "page", failed)
    with TestClient(http_app(tmp_path, model=LocalModel()), raise_server_exceptions=False) as client:
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text(encoding="utf-8")
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        response = client.get(f"/api/v1/agents/{agent}/objects")
        assert response.status_code == 500
        identity = response.json()["error"]["request_id"]
        assert identity and response.headers["x-request-id"] == identity
    content = (tmp_path / "logs/runtime.jsonl").read_text(encoding="utf-8")
    rows = [json.loads(line) for line in content.splitlines()]
    matched = [row for row in rows if row["event"] == "http.failed"]
    assert len(matched) == 1 and matched[0]["request_id"] == identity
    assert "RuntimeError" in matched[0]["exception"] and "private-http-exception-canary" not in content


def test_people_documents_accounts_and_erasure_use_new_contracts(tmp_path):
    with TestClient(http_app(tmp_path, model=LocalModel())) as client:
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text(encoding="utf-8")
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        base = f"/api/v1/agents/{agent}"
        owner = client.get(base + "/people/owner").json()
        assert (
            client.post(base + "/people", json={"name": "fake owner", "owner_id": owner["owner_id"]}).status_code == 422
        )
        person = client.post(base + "/people", json={"name": "A person"}).json()
        target = base + "/people/" + person["id"]
        assert (
            client.post(
                target + "/accounts", json={"channel": "qq", "external_id": "123", "verification": "verified"}
            ).status_code
            == 422
        )
        binding = client.post(target + "/accounts", json={"channel": "qq", "external_id": "123"})
        assert binding.status_code == 201 and binding.json()["verification"] == "manual"
        document = client.put(
            target + "/documents/profile", json={"entry_id": None, "revision": 0, "content": "a private profile"}
        )
        assert document.status_code == 200
        entry = document.json()
        assert client.get(base + "/memory").json()["items"] == []
        assert client.get(base + "/memory/" + entry["entry_id"] + "/revisions").json()["items"][0]["sources"][0][
            "manual_audit_id"
        ]
        assert (
            client.put(
                target + "/documents/profile", json={"entry_id": None, "revision": 0, "content": "stale"}
            ).status_code
            == 409
        )
        assert client.delete(target, params={"revision": 1}).status_code == 409
        fresh = client.get(target).json()
        assert client.delete(target, params={"revision": fresh["revision"]}).status_code == 200
        assert client.get(target).status_code == 410
        erased = client.get(base + "/memory/" + entry["entry_id"] + "/revisions").json()["items"]
        assert erased[0]["erased"] and erased[0]["content"] == "" and erased[0]["sources"] == []
        assert client.delete(base + "/people/" + owner["id"], params={"revision": owner["revision"]}).status_code == 409


def test_schedule_api_validation_scope_pagination_and_soft_archive(tmp_path):
    with TestClient(http_app(tmp_path, model=LocalModel())) as client:
        token = (tmp_path / "run/owner.token").read_text()
        client.headers["Authorization"] = "Bearer " + token
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        base = f"/api/v1/agents/{agent}/schedules"
        payload = {"name": "calendar", "value": {"prompt": "private-schedule-canary"}}
        response = client.post(base, json=payload)
        assert response.status_code == 201, response.text
        row = response.json()
        assert not row["enabled"] and row["progress"]["next_at"] is None
        assert client.get(base, params={"limit": 201}).status_code == 422
        assert client.get(base, params={"limit": 1}).json()["next_after"] == row["id"]
        assert client.get(base, params={"after": row["id"]}).json()["items"] == []
        assert client.put(base + "/" + row["id"], json={**payload, "revision": 0}).status_code == 409
        assert client.post(base, json={**payload, "enabled": "yes"}).status_code == 422
        assert client.post(base, json={**payload, "value": {"prompt": "x", "timezone": "../UTC"}}).status_code == 422
        assert (
            client.post(
                base,
                json={
                    **payload,
                    "value": {"prompt": "x", "kind": "once", "expression": "", "start_at": "2020-01-01T00:00:00"},
                },
            ).status_code
            == 422
        )
        assert client.post(base, content=b"x" * 100001).status_code == 413
        assert client.get(base + "/" + row["id"] + "/history").json()["items"][0]["prompt"] == "private-schedule-canary"
        assert client.delete(base + "/" + row["id"], params={"revision": 1}).status_code == 200
        assert client.get(base).json()["items"] == []
        archived = client.get(base, params={"include_archived": True}).json()["items"]
        assert archived[0]["id"] == row["id"] and archived[0]["deleted_at"]
        assert client.get(base + "/" + row["id"]).json()["deleted_at"]
        assert "private-schedule-canary" not in client.get("/api/v1/system/logs").text
