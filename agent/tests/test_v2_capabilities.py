"""Capability report: bounded projection of live health, never an acceptance claim."""

from fastapi.testclient import TestClient

from omni.composition.http import http_app
from omni.domain.capabilities import FORMAT, capability_report


def test_report_is_bounded_and_marks_attention_without_guessing_missing_areas():
    health = {
        "ready": True,
        "api_version": 1,
        "schema_version": "v2_0037",
        "build_id": "build",
        "platform_acceptance": "pending",
        "database": {"closed": False, "pending_reads": 1, "pending_writes": 0, "path": "C:/private/omni.db"},
        "mcp": {"closing": True, "transports": {"stdio": 1}},
        "extensions": {"hosts": {"resident": 1}, "pending": 0, "staging_root": "C:/private/staging"},
        "skills": {"ready": False, "platform": "windows"},
        "qq": {"last_error": "qq_transport_unavailable", "listeners": []},
        "unknown_area": {"secret": "must-not-cross"},
    }
    report = capability_report(health)
    assert report["format"] == FORMAT and report["entrypoint"] == "v1" and report["v2_accepted"] is False
    assert report["ready"] is True and report["platform_acceptance"] == "pending"
    areas = {area["id"]: area for area in report["areas"]}
    assert "unknown_area" not in areas
    assert areas["database"]["facts"] == {"closed": False, "pending_reads": 1, "pending_writes": 0}
    assert areas["database"]["state"] == "available"
    assert areas["mcp"]["state"] == "attention" and areas["mcp"]["facts"]["transports"] == 1
    assert areas["extensions"]["facts"]["hosts"] == 1
    assert areas["skills"]["state"] == "attention"
    assert areas["qq"]["state"] == "attention"
    assert report["counts"]["attention"] == 3
    assert all("private" not in str(area) for area in report["areas"])
    assert "默认正式入口仍是 V1" in report["notes"][0]


def test_capability_endpoint_requires_owner_and_exposes_no_secrets(tmp_path):
    home = tmp_path / "instance"
    with TestClient(http_app(home)) as client:
        assert client.get("/api/v1/capabilities").status_code == 401
        token = (home / "run/owner.token").read_text(encoding="utf-8")
        headers = {"Authorization": "Bearer " + token}
        response = client.get("/api/v1/capabilities", headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["format"] == FORMAT and body["entrypoint"] == "v1" and body["v2_accepted"] is False
        ids = {area["id"] for area in body["areas"]}
        assert {"database", "mcp", "extensions", "terminals", "desktop"} <= ids
        assert token not in response.text and "owner.token" not in response.text
        assert str(home) not in response.text
        assert body["counts"]["areas"] == len(body["areas"])
