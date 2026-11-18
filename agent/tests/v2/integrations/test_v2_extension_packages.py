"""Immutable, zero-execution packages and bounded I/O retain ownership after caller cancellation."""

import asyncio
import hashlib
import io
import os
import stat
import threading
import uuid
import zipfile
from contextlib import nullcontext
from pathlib import Path

import pytest
from omni_test_support.v2.v2_extension_helpers import plugin_source

from omni.adapters.extensions import packages as p
from omni.domain.contracts import BusinessError
from omni.domain.extensions import MAX_FILE_BYTES, MAX_FILES, PROTOCOL


def revision(captured, agent):
    return {
        "agent_id": agent,
        "manifest": captured.manifest,
        "files": list(captured.files),
        "package_sha256": captured.package_sha256,
        "catalog_sha256": captured.catalog_sha256,
        "entry": captured.manifest["entry"],
        "protocol": PROTOCOL,
        "version": captured.manifest["version"],
        "file_count": len(captured.files),
        "total_bytes": sum(row["bytes"] for row in captured.files),
    }


def test_capture_prepare_verify_are_deterministic_and_never_import_entry(tmp_path):
    async def scenario():
        marker = tmp_path / "must-not-import"
        source = plugin_source(tmp_path / "source", marker=marker)
        (source / "unicode.txt").write_text("中文🧪", encoding="utf-8")
        storage = p.ExtensionPackages(tmp_path / "cache")
        try:
            captured = await storage.capture(str(source))
            assert captured == await storage.capture(str(source))
            assert captured.package_sha256 == hashlib.sha256(captured.content).hexdigest()
            assert [item["path"] for item in captured.files] == ["extension.json", "plugin.py", "unicode.txt"]
            agent = str(uuid.uuid4())
            record = revision(captured, agent)
            location = Path(await storage.prepare(agent, record, captured.content))
            assert location == tmp_path / "cache" / agent / captured.package_sha256
            await storage.verify(str(location), record)
            assert not marker.exists()
            assert {path.relative_to(location).as_posix() for path in location.rglob("*") if path.is_file()} == {
                item["path"] for item in captured.files
            }
            with zipfile.ZipFile(io.BytesIO(captured.content)) as archive:
                assert all(item.compress_type == zipfile.ZIP_STORED for item in archive.infolist())
                assert all(item.date_time == (1980, 1, 1, 0, 0, 0) for item in archive.infolist())
        finally:
            assert await storage.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/absolute",
        "a\\b",
        "a//b",
        "a/./b",
        "a:b",
        "CON.txt",
        "com¹.py",
        "end.",
        ".env",
        "private/x",
        "sitecustomize.py",
        "secrets.key",
        "a\x00b",
    ],
)
def test_package_paths_reject_traversal_windows_aliases_and_private_material(name):
    with pytest.raises(BusinessError):
        p.relative_path(name)


def test_capture_uses_path_stat_when_directory_entry_link_count_is_incomplete(tmp_path, monkeypatch):
    source = plugin_source(tmp_path / "source")
    original_scandir = p.os.scandir

    class Entry:
        def __init__(self, entry):
            self.path = entry.path

        def stat(self, **kwargs):
            raise AssertionError("DirEntry.stat must not decide single-link ownership on Windows")

    class Directory:
        def __init__(self, path):
            self.actual = original_scandir(path)

        def __enter__(self):
            return (Entry(entry) for entry in self.actual.__enter__())

        def __exit__(self, *args):
            return self.actual.__exit__(*args)

    monkeypatch.setattr(p.os, "scandir", Directory)
    assert p.capture_directory(source).files


def test_capture_rejects_real_hardlinks_and_linked_directories(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    private = outside / "payload.txt"
    private.write_text("must stay outside")
    hardlinked = plugin_source(tmp_path / "hardlinked")
    os.link(private, hardlinked / "payload.txt")
    with pytest.raises(BusinessError):
        p.capture_directory(hardlinked)
    linked = plugin_source(tmp_path / "linked")
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(outside), str(linked / "linked-folder"))
    else:
        (linked / "linked-folder").symlink_to(outside, target_is_directory=True)
    with pytest.raises(BusinessError):
        p.capture_directory(linked)
    assert private.read_text() == "must stay outside"


@pytest.mark.parametrize("limit", ["file", "count", "total"])
def test_capture_enforces_individual_count_and_total_budgets(tmp_path, limit):
    source = plugin_source(tmp_path / "source")
    if limit == "file":
        (source / "big.txt").write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    elif limit == "count":
        for number in range(MAX_FILES - 1):
            (source / f"file-{number}.txt").write_bytes(b"x")
    else:
        for number in range(8):
            (source / f"file-{number}.txt").write_bytes(b"x" * MAX_FILE_BYTES)
    with pytest.raises(BusinessError):
        p.capture_directory(source)


@pytest.mark.parametrize("attack", ["traversal", "duplicate", "compressed", "symlink", "case_collision"])
def test_prepare_rejects_noncanonical_or_unsafe_archive_even_with_matching_archive_sha(tmp_path, attack):
    async def scenario():
        source = plugin_source(tmp_path / "source")
        captured = p.capture_directory(source)
        agent = str(uuid.uuid4())
        record = revision(captured, agent)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for row in captured.files:
                archive.writestr(row["path"], (source / row["path"]).read_bytes())
            if attack == "compressed":
                archive.writestr("extra.txt", b"compress me", compress_type=zipfile.ZIP_DEFLATED)
            elif attack == "symlink":
                entry = zipfile.ZipInfo("linked.py")
                entry.create_system = 3
                entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(entry, b"../outside")
            else:
                name = {"traversal": "../escaped.txt", "duplicate": "plugin.py", "case_collision": "PLUGIN.py"}[attack]
                with pytest.warns(UserWarning) if attack == "duplicate" else nullcontext():
                    archive.writestr(name, b"untrusted")
        payload = stream.getvalue()
        record["package_sha256"] = hashlib.sha256(payload).hexdigest()
        storage = p.ExtensionPackages(tmp_path / "cache")
        try:
            with pytest.raises(BusinessError, match="fingerprints") as refused:
                await storage.prepare(agent, record, payload)
            assert refused.value.code == "extension_package_invalid"
            assert not (tmp_path / "escaped.txt").exists()
            assert not (tmp_path / "cache" / agent / record["package_sha256"]).exists()
        finally:
            assert await storage.close()

    asyncio.run(scenario())


def test_existing_snapshot_drift_is_never_overwritten_and_agent_scope_is_enforced(tmp_path):
    async def scenario():
        captured = p.capture_directory(plugin_source(tmp_path / "source"))
        agent = str(uuid.uuid4())
        record = revision(captured, agent)
        storage = p.ExtensionPackages(tmp_path / "cache")
        try:
            location = Path(await storage.prepare(agent, record, captured.content))
            original = location / "plugin.py"
            original.write_text("tampered snapshot", encoding="utf-8")
            for operation in (storage.verify(str(location), record), storage.prepare(agent, record, captured.content)):
                with pytest.raises(BusinessError) as refused:
                    await operation
                assert refused.value.code == "extension_snapshot_drift"
                assert original.read_text() == "tampered snapshot"
            with pytest.raises(BusinessError):
                await storage.verify(str(location), {**record, "agent_id": str(uuid.uuid4())})
            moved = location.with_name("preserved-drift")
            location.rename(moved)
            restored = Path(await storage.prepare(agent, record, captured.content))
            assert (restored / "plugin.py").read_bytes() != b"tampered snapshot"
            assert (moved / "plugin.py").read_text() == "tampered snapshot"
        finally:
            assert await storage.close()

    asyncio.run(scenario())


def test_cancelled_io_keeps_actual_two_workers_eight_jobs_until_reclaimed(tmp_path, monkeypatch):
    async def scenario():
        storage = p.ExtensionPackages(tmp_path / "cache")
        source = plugin_source(tmp_path / "source")
        original = p.capture_directory
        release = threading.Event()
        guard = threading.Lock()
        counts = {"active": 0, "maximum": 0, "finished": 0}

        def held_capture(path):
            with guard:
                counts["active"] += 1
                counts["maximum"] = max(counts["maximum"], counts["active"])
            assert release.wait(10)
            try:
                return original(path)
            finally:
                with guard:
                    counts["active"] -= 1
                    counts["finished"] += 1

        monkeypatch.setattr(p, "capture_directory", held_capture)
        requests = [asyncio.create_task(storage.capture(str(source))) for _ in range(8)]
        try:
            async with asyncio.timeout(5):
                while len(storage._jobs) != 8 or counts["active"] != 2:
                    await asyncio.sleep(0)
            for task in requests:
                task.cancel()
            assert all(
                isinstance(item, asyncio.CancelledError)
                for item in await asyncio.gather(*requests, return_exceptions=True)
            )
            assert len(storage._jobs) == 8 and counts["maximum"] == 2
            with pytest.raises(BusinessError) as full:
                await storage.capture(str(source))
            assert full.value.code == "extension_packages_busy"
            assert not await storage.close(0)
            assert len(storage._jobs) == 8 and not storage._closed
        finally:
            release.set()
            await asyncio.gather(*requests, return_exceptions=True)
            assert await storage.close(10)
        assert counts["maximum"] == 2 and counts["finished"] == 8 and not storage._jobs

    asyncio.run(scenario())
