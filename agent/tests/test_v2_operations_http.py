"""Owner-only operational endpoints return bounded metadata, never invoke models."""

import io
import json
import zipfile

from fastapi.testclient import TestClient

from omni.adapters.persistence.migration_config import head as migration_head
from omni.composition.http import http_app


class NoModel:
    async def complete(self, *args):
        raise AssertionError("Diagnostics cannot invoke a model")


def test_operational_http_authentication_and_bounded_download(tmp_path):
    with TestClient(http_app(tmp_path, model=NoModel())) as client:
        for path in ("/api/v1/system/metrics", "/api/v1/system/diagnostics"):
            assert client.get(path).status_code == 401
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text()
        report = client.get("/api/v1/system/metrics")
        assert report.status_code == 200
        data = report.json()
        # Eleven fixed operations (including desktop), each with five outcomes.
        assert data["runtime"]["ready"] and data["measurements"]["max_series"] == 55
        assert data["persisted"]["database"]["schema_version"] == migration_head()
        download = client.get("/api/v1/system/diagnostics")
        assert download.status_code == 200 and download.headers["content-type"] == "application/zip"
        assert download.headers["content-disposition"] == 'attachment; filename="omni-diagnostics.zip"'
        assert len(download.content) < 2 * 1024 * 1024
        with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
            assert archive.testzip() is None
            metadata = json.loads(archive.read("summary.json"))
            assert metadata["host"]["platform_acceptance"] == "pending"
            assert metadata["runtime"]["ready"] is True


def test_log_read_failure_is_explicit_and_export_failure_is_correlated(tmp_path, monkeypatch):
    from omni.adapters.log_query import LogReader

    with TestClient(http_app(tmp_path, model=NoModel())) as client:
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text()

        def denied(*args):
            raise PermissionError("private-filesystem-canary")

        monkeypatch.setattr(LogReader, "tail", denied)
        download = client.get("/api/v1/system/diagnostics")
        assert download.status_code == 200
        with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
            report = json.loads(archive.read("summary.json"))
            assert report["diagnostic_log_excerpt"] == {"available": False, "error_code": "diagnostic_logs_unavailable"}
            assert "private-filesystem-canary" not in archive.read("summary.json").decode()

        def broken(_):
            raise OSError("private-package-canary")

        monkeypatch.setattr(client.app.state.runtime.operations._diagnostic, "_bundle", broken)
        response = client.get("/api/v1/system/diagnostics")
        assert response.status_code == 500
        assert response.json()["error"]["request_id"] == response.headers["x-request-id"]
        assert "private-package-canary" not in response.text
