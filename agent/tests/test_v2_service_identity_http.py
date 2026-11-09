"""Local proof is tied to one boot and never transfers owner credentials to a listener."""

from __future__ import annotations

import json
import sys

import pytest
from fastapi.testclient import TestClient
from test_v2_deployment_files import installation as installation
from test_v2_deployment_files import plan_for

from omni.adapters.platform.runtime_identity import canonical_proof, read_private
from omni.composition import cli
from omni.composition.http import http_app
from omni.composition.serving import serve_instance
from omni.domain.contracts import BusinessError
from omni.domain.service_deployment import make_plan


def test_local_challenge_proves_boot_without_authorization_and_rotates_key(tmp_path):
    home = tmp_path / "instance"
    challenge = "a" * 64
    with TestClient(http_app(home), client=("127.0.0.1", 40000)) as client:
        response = client.get("/health/identity", params={"challenge": challenge})
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert "authorization" not in response.request.headers
        key = read_private(home / "run/readiness.key", 32)
        report = response.json()
        assert len(key) == 32
        assert report == canonical_proof(report["identity"], challenge, key)
        token = (home / "run/owner.token").read_text()
        assert token not in response.text and key.hex() not in response.text
        assert report["identity"]["ready"] is True
        assert client.get("/health/identity", params={"challenge": "invalid"}).status_code == 422
        assert client.get("/api/v1/version").status_code == 401
    with TestClient(http_app(home), client=("127.0.0.1", 40000)) as client:
        later = client.get("/health/identity", params={"challenge": challenge}).json()
        next_key = read_private(home / "run/readiness.key", 32)
        assert next_key != key
        assert later["identity"]["instance_id"] == report["identity"]["instance_id"]
        assert later["identity"]["boot_id"] != report["identity"]["boot_id"]
        assert later != canonical_proof(later["identity"], challenge, key)


def test_remote_client_cannot_request_local_proof(tmp_path):
    with TestClient(http_app(tmp_path), client=("192.0.2.1", 40000)) as client:
        assert client.get("/health/identity", params={"challenge": "a" * 64}).status_code == 403


def test_cli_service_preparation_never_opens_instance_or_queries_manager(installation, tmp_path, monkeypatch, capsys):
    files, _, _, _, _ = installation
    monkeypatch.setattr("omni.adapters.platform.deployment_files.CurrentDeploymentFiles", lambda: files)
    calls = []
    monkeypatch.setattr(cli, "open_instance", lambda *_a, **_k: calls.append(True))
    output = tmp_path / "runtime.json"
    monkeypatch.setattr(sys, "argv", ["omni", "--home", str(tmp_path / "unused"), "service", "capture", str(output)])
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["captured"]
    assert not calls and not (tmp_path / "unused").exists()


@pytest.mark.parametrize(
    "origin", ["https://[invalid", "https://host:invalid", "http://example.com", "https://u:p@example.com"]
)
def test_remote_startup_origin_errors_are_structured(origin, installation, tmp_path):
    files, _, _, _, _ = installation
    plan = plan_for(files, tmp_path)
    request = {**plan["request"], "host": "0.0.0.0", "external_url": origin}
    with pytest.raises(BusinessError):
        make_plan(request, plan["runtime"], plan["id"], plan["created_at"])
    with pytest.raises(BusinessError):
        serve_instance(tmp_path, host="0.0.0.0", port=8766, external_url=origin, master_key_file=None, stdout_logs=True)
