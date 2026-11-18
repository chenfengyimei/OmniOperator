"""Skill ZIP buffers close on completion and failure, including work outliving its caller."""

from __future__ import annotations

import asyncio
import io
import stat
import threading
import zipfile
from types import SimpleNamespace

import pytest
from omni_test_support.v2.skill_zip_errors import FILES, archive_bytes

from omni.adapters import skill_packages
from omni.adapters.skill_packages import SkillPackages, capture
from omni.domain.contracts import BusinessError
from omni.domain.skills import SkillPackage, invalid


class Resources:
    def __init__(self, monkeypatch, phase=None):
        self.buffers, self.archives, self.entries = [], [], []
        self.reads, self.writes = [], []
        self.entered, self.release = threading.Event(), threading.Event()
        self.error = OSError("PRIVATE_SKILL_ZIP_RESOURCE_FAULT")
        self.business_error = invalid()
        owner = self

        def fault(stage):
            if phase == "pause_" + stage:
                owner.entered.set()
                assert owner.release.wait(10), "test must release the actual codec thread"
            if phase == stage:
                raise owner.error

        class Buffer(io.BytesIO):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                owner.buffers.append(self)

            def getvalue(self):
                fault("getvalue")
                return super().getvalue()

        class Archive(zipfile.ZipFile):
            def __init__(self, *args, **kwargs):
                self.fp = None
                owner.archives.append(self)
                fault("construct")
                super().__init__(*args, **kwargs)

            def open(self, *args, **kwargs):
                if self.mode != "r":
                    return super().open(*args, **kwargs)
                number = "first" if not owner.reads else "second"
                fault("open_" + number)
                entry = super().open(*args, **kwargs)
                owner.entries.append(entry)
                read_method = entry.read

                def read(*read_args, **read_kwargs):
                    fault("read_" + number)
                    return read_method(*read_args, **read_kwargs)

                entry.read = read
                owner.reads.append(entry.name)
                return entry

            def writestr(self, info, data, *args, **kwargs):
                fault("write_first" if not owner.writes else "write_second")
                owner.writes.append((info.filename, data))
                return super().writestr(info, data, *args, **kwargs)

            def close(self):
                was_open = self.fp is not None
                super().close()
                if was_open:
                    fault("finalize")

        monkeypatch.setattr(skill_packages, "io", SimpleNamespace(BytesIO=Buffer))
        monkeypatch.setattr(
            skill_packages,
            "zipfile",
            SimpleNamespace(
                ZipFile=Archive,
                ZipInfo=zipfile.ZipInfo,
                ZIP_STORED=zipfile.ZIP_STORED,
                ZIP_DEFLATED=zipfile.ZIP_DEFLATED,
                BadZipFile=zipfile.BadZipFile,
            ),
        )
        if phase in {"metadata", "document", "validate"}:

            def reject(*args, **kwargs):
                raise owner.business_error

            if phase == "validate":
                monkeypatch.setattr(SkillPackage, "validate", reject)
            else:
                monkeypatch.setattr(skill_packages, "_metadata" if phase == "metadata" else "document", reject)

    def assert_closed(self):
        # Strong references prevent garbage collection from satisfying these checks.
        assert self.buffers and all(buffer.closed for buffer in self.buffers)
        assert all(archive.fp is None for archive in self.archives)
        assert all(entry.closed for entry in self.entries)


@pytest.mark.parametrize(
    "phase",
    [
        None,
        "construct",
        "open_first",
        "open_second",
        "read_first",
        "read_second",
        "metadata",
        "document",
        "validate",
        "finalize",
    ],
)
def test_capture_closes_input_buffer_and_all_allocated_zip_resources(monkeypatch, phase):
    payload = archive_bytes()
    resources = Resources(monkeypatch, phase)
    if phase is None:
        package = capture(payload, "zip")
        assert dict(package.files) == FILES and package.metadata["name"] == "test-skill"
    else:
        with pytest.raises(BusinessError) as caught:
            capture(payload, "zip")
        assert caught.value.code == "invalid_skill" and caught.value.status == 422
        if phase in {"metadata", "document", "validate"}:
            assert caught.value is resources.business_error
        else:
            assert caught.value.__cause__ is None and "PRIVATE_SKILL" not in str(caught.value)
    resources.assert_closed()


def test_invalid_zip_constructor_still_closes_input_buffer(monkeypatch):
    resources = Resources(monkeypatch)
    with pytest.raises(BusinessError) as caught:
        capture(b"not a zip", "zip")
    assert caught.value.code == "invalid_skill" and caught.value.status == 422
    resources.assert_closed()


@pytest.mark.parametrize("phase", [None, "construct", "write_first", "write_second", "getvalue", "finalize"])
def test_archive_closes_output_buffer_on_success_and_failure(monkeypatch, phase):
    package = capture(archive_bytes(), "zip")
    resources = Resources(monkeypatch, phase)

    async def scenario():
        codec = SkillPackages()
        try:
            if phase is None:
                assert_export(await codec.archive(package), package)
            else:
                with pytest.raises(OSError) as caught:
                    await codec.archive(package)
                assert caught.value is resources.error
            assert not codec._pending
        finally:
            assert await codec.close()

    asyncio.run(scenario())
    resources.assert_closed()


def test_archive_validates_before_allocating_output_buffer(monkeypatch):
    package = capture(archive_bytes(), "zip")
    resources = Resources(monkeypatch, "validate")

    async def scenario():
        codec = SkillPackages()
        try:
            with pytest.raises(BusinessError) as caught:
                await codec.archive(package)
            assert caught.value is resources.business_error and not codec._pending
        finally:
            assert await codec.close()

    asyncio.run(scenario())
    assert not resources.buffers and not resources.archives


@pytest.mark.parametrize("operation", ["parse", "archive"])
def test_cancelled_waiter_keeps_actual_thread_owned_until_resources_close(monkeypatch, operation):
    payload = archive_bytes()
    package = capture(payload, "zip")
    resources = Resources(monkeypatch, "pause_read_first" if operation == "parse" else "pause_write_first")

    async def scenario():
        codec = SkillPackages()
        waiter = asyncio.create_task(codec.parse(payload, "zip") if operation == "parse" else codec.archive(package))
        try:
            assert await asyncio.wait_for(asyncio.to_thread(resources.entered.wait, 8), 10)
            actual = next(iter(codec._pending))
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert not await codec.close(0) and codec._pending == {actual} and not actual.cancelled()
            assert len(resources.buffers) == 1 and not resources.buffers[0].closed
            resources.release.set()
            result = await asyncio.wait_for(asyncio.shield(actual), 10)
            assert await codec.close(8)
            if operation == "parse":
                assert result == package and resources.reads == list(FILES)
            else:
                assert_export(result, package)
                assert resources.writes == list(package.files)
            resources.assert_closed()
        finally:
            resources.release.set()
            await asyncio.gather(waiter, return_exceptions=True)
            assert await codec.close(8)

    asyncio.run(scenario())


def assert_export(payload, package):
    with io.BytesIO(payload) as buffer, zipfile.ZipFile(buffer) as archive:
        assert archive.namelist() == [name for name, _ in package.files]
        assert {name: archive.read(name) for name in archive.namelist()} == dict(package.files)
        for entry in archive.infolist():
            assert entry.date_time == (1980, 1, 1, 0, 0, 0)
            assert entry.external_attr == (stat.S_IFREG | 0o600) << 16
            assert entry.compress_type == zipfile.ZIP_DEFLATED


@pytest.mark.parametrize("compression", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_real_zip_roundtrip_preserves_files_metadata_and_export_parameters(compression):
    async def scenario():
        codec = SkillPackages()
        try:
            original = await codec.parse(archive_bytes(compression), "zip")
            payload = await codec.archive(original)
            assert_export(payload, original)
            assert await codec.parse(payload, "zip") == original
        finally:
            assert await codec.close()

    asyncio.run(scenario())
