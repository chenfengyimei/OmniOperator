"""Offline metadata export owns the instance, never upgrades or overwrites existing files."""

import asyncio
import hashlib
import json
import subprocess
import sys
import zipfile

import httpx
import pytest

from omni.adapters import diagnostic_bundle
from omni.adapters.persistence.migration_config import head as migration_head
from omni.adapters.platform.runtime.paths import InstancePaths
from omni.composition.cli import diagnostic_download
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError


def command(home, destination, *options):
    return subprocess.run(
        [sys.executable, "-m", "omni.composition.cli", "--home", str(home), "diagnostics", str(destination), *options],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )


def test_offline_diagnostic_export_uses_read_only_database_and_explicit_paths(tmp_path):
    home = tmp_path / "home with spaces 空格#suffix"

    async def seed():
        instance = await open_instance(home, capabilities=())
        assert (await instance.close())["complete"]

    asyncio.run(seed())
    database = home / "data/omni.db"
    original = hashlib.sha256(database.read_bytes()).hexdigest()
    destination = tmp_path / "diagnostics.zip"
    result = command(home, destination, "--offline")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["includes_business_bodies"] is False
    assert hashlib.sha256(database.read_bytes()).hexdigest() == original
    with zipfile.ZipFile(destination) as archive:
        data = json.loads(archive.read("summary.json"))
        assert data["runtime"]["state"] == "offline" and not data["runtime"]["ready"]
        assert not data["measurements"]["available"]
        assert data["host"]["logs"]["dropped"] is None
        assert data["persisted"]["database"]["schema_version"] == migration_head()
    assert command(home, destination, "--offline").returncode == 1
    assert command(home, home / "data/do-not-write.zip", "--offline").returncode == 1
    assert not (home / "data/do-not-write.zip").exists()
    assert not list((home / "data").glob("schema-backups/*"))


def test_offline_export_refuses_live_instance_and_remote_credentials(tmp_path):
    async def scenario():
        instance = await open_instance(tmp_path / "instance", capabilities=())
        destination = tmp_path / "blocked.zip"
        try:
            result = await asyncio.to_thread(command, instance.paths.home, destination, "--offline")
            assert result.returncode == 1 and json.loads(result.stderr)["error"] == "instance_busy"
            assert not destination.exists()
        finally:
            await instance.close()
        remote = await asyncio.to_thread(
            command, instance.paths.home, destination, "--url", "https://untrusted.invalid"
        )
        assert remote.returncode == 1 and json.loads(remote.stderr)["error"] == "invalid_local_endpoint"
        assert not destination.exists()

    asyncio.run(scenario())


def bundle_bytes(**extras):
    import io

    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in {
            "summary.json": "{}",
            "correlations.jsonl": "",
            "README.txt": "diagnostics",
            **extras,
        }.items():
            archive.writestr(name, content)
    return stream.getvalue()


def test_diagnostic_publication_is_atomic_private_and_never_overwrites(tmp_path, monkeypatch):
    paths = InstancePaths.resolve(tmp_path / "home")
    destination = tmp_path / "diagnostic.zip"
    payload = bundle_bytes()
    original_sync = diagnostic_bundle.os.fsync

    def full(_descriptor):
        assert not destination.exists()
        raise OSError("simulated full filesystem")

    monkeypatch.setattr(diagnostic_bundle.os, "fsync", full)
    with pytest.raises(OSError):
        diagnostic_bundle.publish_diagnostic(paths, destination, payload)
    assert not destination.exists() and not list(tmp_path.glob(".omni-diagnostic-*"))
    monkeypatch.setattr(diagnostic_bundle.os, "fsync", original_sync)
    diagnostic_bundle.publish_diagnostic(paths, destination, payload)
    assert destination.read_bytes() == payload
    with pytest.raises(BusinessError, match="never overwritten"):
        diagnostic_bundle.publish_diagnostic(paths, destination, payload)
    assert destination.read_bytes() == payload and not list(tmp_path.glob(".omni-diagnostic-*"))


@pytest.mark.parametrize("kind", ["invalid", "extra", "expanding"])
def test_diagnostic_publication_rejects_invalid_extra_and_expanding_content(tmp_path, kind):
    payload = (
        b"not a zip"
        if kind == "invalid"
        else bundle_bytes(
            **({"private.db": "private"} if kind == "extra" else {"README.txt": "x" * (2 * 1024 * 1024 + 1)})
        )
    )
    with pytest.raises(BusinessError) as result:
        diagnostic_bundle.publish_diagnostic(InstancePaths.resolve(tmp_path / "home"), tmp_path / "output.zip", payload)
    assert result.value.code == "invalid_diagnostic_response"
    assert not (tmp_path / "output.zip").exists()


@pytest.mark.parametrize("kind", ["oversize", "mime", "redirect", "encoding"])
def test_online_download_enforces_stream_limit_type_and_no_redirects(tmp_path, monkeypatch, kind):
    paths = InstancePaths.resolve(tmp_path / "home")
    paths.initialize()
    (paths.control / "owner.token").write_text("fixture-owner", encoding="utf-8")
    original_client = httpx.Client
    requests = []

    class LargeBody(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(33):
                yield b"x" * 65536

    def handle(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer fixture-owner"
        if kind == "redirect":
            return httpx.Response(302, headers={"location": "https://untrusted.invalid/"})
        return httpx.Response(
            200,
            headers={
                "content-type": "text/html" if kind == "mime" else "application/zip",
                **({"content-encoding": "gzip"} if kind == "encoding" else {}),
            },
            stream=LargeBody(),
        )

    monkeypatch.setattr(
        httpx, "Client", lambda **options: original_client(transport=httpx.MockTransport(handle), **options)
    )
    with pytest.raises(BusinessError) as result:
        diagnostic_download(paths, "http://127.0.0.1:8766")
    assert (
        result.value.code
        == {
            "oversize": "diagnostics_too_large",
            "mime": "invalid_diagnostic_response",
            "redirect": "local_api_unavailable",
            "encoding": "invalid_diagnostic_response",
        }[kind]
    )
    assert len(requests) == 1
