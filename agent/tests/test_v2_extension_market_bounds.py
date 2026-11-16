"""Local market enumeration and manifest I/O stay bounded without capturing or importing plugins."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_v2_extensions import MANIFEST
from test_v2_extensions_http import owner
from v2_extension_helpers import installation, plugin_source

from omni.adapters.extensions import packages as p
from omni.composition.http import http_app
from omni.domain.contracts import BusinessError
from omni.domain.extensions import MAX_MANIFEST_BYTES, catalog_fingerprint


def candidate(root, name="candidate", size=None):
    path = root / name
    path.mkdir(parents=True)
    raw = json.dumps(MANIFEST, ensure_ascii=False).encode()
    if size is not None:
        assert size >= len(raw)
        raw += b" " * (size - len(raw))
    manifest = path / "extension.json"
    manifest.write_bytes(raw)
    return manifest


def scan(root, cache):
    async def scenario():
        storage = p.ExtensionPackages(cache)
        try:
            return await storage.market(str(root))
        finally:
            assert await storage.close(5)

    return asyncio.run(scenario())


class Trace:
    def __init__(self, monkeypatch, manifests):
        self.streams, self.read_sizes, self.enumerated, self.scans = [], [], [], []
        self.before_read = self.after_read = None
        self.enumeration_error = None
        native_open, native_fdopen, native_scandir = Path.open, os.fdopen, os.scandir
        owner = self

        class Stream:
            def __init__(self, native):
                self.native = native
                owner.streams.append(native)

            def fileno(self):
                return self.native.fileno()

            def read(self, size=-1):
                owner.read_sizes.append(size)
                if owner.before_read:
                    owner.before_read()
                value = self.native.read(size)
                if owner.after_read:
                    owner.after_read()
                return value

            def __enter__(self):
                self.native.__enter__()
                return self

            def __exit__(self, *args):
                return self.native.__exit__(*args)

        class Scan:
            def __init__(self, path):
                self.native, self.closed = native_scandir(path), False
                owner.scans.append(self)

            def __enter__(self):
                return self

            def __iter__(self):
                return self

            def __next__(self):
                if owner.enumeration_error and len(owner.enumerated) == 2:
                    raise owner.enumeration_error
                entry = next(self.native)
                owner.enumerated.append(entry.name)
                return entry

            def __exit__(self, *args):
                self.native.close()
                self.closed = True

        def opened(path, *args, **kwargs):
            value = native_open(path, *args, **kwargs)
            mode = args[0] if args else kwargs.get("mode", "r")
            return Stream(value) if path in manifests and mode == "rb" else value

        def fdopened(*args, **kwargs):
            return Stream(native_fdopen(*args, **kwargs))

        monkeypatch.setattr(Path, "open", opened)
        monkeypatch.setattr(p, "os", SimpleNamespace(**{**vars(os), "fdopen": fdopened, "scandir": Scan}))

    def assert_closed(self):
        assert all(stream.closed for stream in self.streams)
        assert all(scan.closed for scan in self.scans)


@pytest.mark.parametrize("size", [MAX_MANIFEST_BYTES + 1, 1_048_576, 1_048_577])
def test_oversized_manifest_is_skipped_before_reading_any_content(tmp_path, monkeypatch, size):
    root = tmp_path / "market"
    path = candidate(root, size=size)
    trace = Trace(monkeypatch, {path})
    assert scan(root, tmp_path / "cache") == []
    trace.assert_closed()
    assert not trace.read_sizes


@pytest.mark.parametrize("size", [1024, MAX_MANIFEST_BYTES - 1, MAX_MANIFEST_BYTES])
def test_valid_manifest_uses_bounded_read_and_keeps_exact_candidate_metadata(tmp_path, monkeypatch, size):
    root = tmp_path / "market"
    path = candidate(root, size=size)
    trace = Trace(monkeypatch, {path})
    result = scan(root, tmp_path / "cache")
    assert result == [
        {
            "name": MANIFEST["id"],
            "version": MANIFEST["version"],
            "entry": MANIFEST["entry"],
            "source": str(path.parent),
            "catalog_sha256": catalog_fingerprint(MANIFEST),
        }
    ]
    trace.assert_closed()
    assert trace.read_sizes == [MAX_MANIFEST_BYTES + 1]


def test_manifest_growing_after_stat_never_reads_past_the_limit(tmp_path, monkeypatch):
    root = tmp_path / "market"
    path = candidate(root)
    trace = Trace(monkeypatch, {path})

    def grow():
        with path.open("ab") as output:
            output.write(b" " * (MAX_MANIFEST_BYTES * 2))

    trace.before_read = grow
    assert scan(root, tmp_path / "cache") == []
    trace.assert_closed()
    assert trace.read_sizes == [MAX_MANIFEST_BYTES + 1]


def test_manifest_changed_during_read_is_skipped(tmp_path, monkeypatch):
    root = tmp_path / "market"
    path = candidate(root)
    trace = Trace(monkeypatch, {path})

    def change():
        data = json.dumps({**MANIFEST, "version": "2"}).encode()
        path.write_bytes(data)
        info = path.stat()
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 2_000_000_000))

    trace.after_read = change
    assert scan(root, tmp_path / "cache") == []
    trace.assert_closed()


def test_hardlinked_manifest_is_skipped_without_reading(tmp_path, monkeypatch):
    root = tmp_path / "market"
    path = candidate(root)
    os.link(path, tmp_path / "other-manifest.json")
    trace = Trace(monkeypatch, {path})
    assert scan(root, tmp_path / "cache") == []
    trace.assert_closed()
    assert not trace.read_sizes


def test_manifest_read_failure_closes_stream_and_keeps_scanning(tmp_path, monkeypatch):
    root = tmp_path / "market"
    path = candidate(root)
    trace = Trace(monkeypatch, {path})

    def fail():
        raise OSError("PRIVATE_MARKET_READ_FAULT")

    trace.before_read = fail
    assert scan(root, tmp_path / "cache") == []
    assert trace.streams
    trace.assert_closed()


@pytest.mark.parametrize("count", [0, 64, 65, 80])
@pytest.mark.parametrize("kind", ["files", "directories"])
def test_directory_budget_counts_all_entries_before_manifest_reads(tmp_path, count, kind):
    root = tmp_path / "market"
    root.mkdir()
    for index in reversed(range(count)):
        if kind == "files":
            (root / f"item-{index:03}.txt").write_bytes(b"ordinary file")
        else:
            candidate(root, f"item-{index:03}")
    if count > 64:
        with pytest.raises(BusinessError) as caught:
            scan(root, tmp_path / "cache")
        assert caught.value.code == "extension_invalid" and caught.value.status == 422
    else:
        result = scan(root, tmp_path / "cache")
        expected = min(32, count) if kind == "directories" else 0
        assert len(result) == expected
        assert [row["source"] for row in result] == sorted(row["source"] for row in result)


@pytest.mark.parametrize("case", ["success", "overflow", "enumeration_error"])
def test_native_directory_iterator_is_bounded_and_explicitly_closed(tmp_path, monkeypatch, case):
    root = tmp_path / "market"
    root.mkdir()
    count = 80 if case == "overflow" else 4
    for index in range(count):
        (root / str(index)).write_bytes(b"file")
    trace = Trace(monkeypatch, set())
    if case == "enumeration_error":
        trace.enumeration_error = OSError("PRIVATE_MARKET_SCAN_FAULT")
    if case == "success":
        assert scan(root, tmp_path / "cache") == []
    else:
        with pytest.raises(BusinessError) as caught:
            scan(root, tmp_path / "cache")
        assert caught.value.code == "extension_invalid" and "PRIVATE_MARKET" not in str(caught.value)
    assert len(trace.enumerated) == (65 if case == "overflow" else 2 if case == "enumeration_error" else count)
    assert len(trace.scans) == 1
    trace.assert_closed()
    assert not trace.read_sizes


def test_cancelled_market_waiter_retains_bounded_read_until_actual_thread_closes(tmp_path, monkeypatch):
    root = tmp_path / "market"
    path = candidate(root)
    trace = Trace(monkeypatch, {path})
    entered, release = threading.Event(), threading.Event()

    def pause():
        entered.set()
        assert release.wait(10), "test must release actual market thread"

    trace.before_read = pause

    async def scenario():
        storage = p.ExtensionPackages(tmp_path / "cache")
        waiter = asyncio.create_task(storage.market(str(root)))
        try:
            assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 8), 10)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert len(storage._jobs) == 1 and not await storage.close(0)
            assert trace.streams and not trace.streams[0].closed
            release.set()
            assert await storage.close(8)
            trace.assert_closed()
            assert trace.read_sizes == [MAX_MANIFEST_BYTES + 1]
        finally:
            release.set()
            await asyncio.gather(waiter, return_exceptions=True)
            assert await storage.close(8)

    asyncio.run(scenario())


def test_http_oversized_market_rejects_without_installing_then_accepts_valid_source(tmp_path):
    root = tmp_path / "market"
    root.mkdir()
    for index in range(65):
        (root / f"item-{index:03}.txt").write_bytes(b"file")
    marker = tmp_path / "must-not-import"
    source = plugin_source(tmp_path / "source", marker=marker)
    home = tmp_path / "instance"
    with TestClient(http_app(home)) as client:
        _, base = owner(client, home)
        saved = client.post(base, json=installation(source))
        assert saved.status_code == 201
        before = client.get(base).json()
        response = client.post(base + "/market", json={"source": str(root)})
        assert response.status_code == 422 and "extension_invalid" in response.text
        assert response.headers["cache-control"] == "no-store" and client.get(base).json() == before
        small = tmp_path / "small"
        plugin_source(small / "valid", marker=marker)
        result = client.post(base + "/market", json={"source": str(small)})
        assert result.status_code == 200 and result.json()["scanned"] == 1
        assert client.get(base).json() == before
    assert not marker.exists()
