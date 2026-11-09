"""Bounded in-memory Markdown/ZIP parsing; never extracts files or executes package content."""

from __future__ import annotations

import asyncio
import io
import stat
import zipfile
from collections.abc import Callable
from typing import TypeVar

import yaml

from ..domain.skills import MAX_BYTES, MAX_FILES, MAX_ZIP, SkillPackage, document, file_path, invalid

T = TypeVar("T")


def capture(payload: bytes, format: str) -> SkillPackage:
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_ZIP or format not in {"markdown", "zip"}:
        raise invalid()
    files = []
    try:
        if format == "markdown":
            files = [("SKILL.md", payload)]
        else:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                entries = archive.infolist()
                if not 1 <= len(entries) <= MAX_FILES or sum(i.file_size for i in entries) > MAX_BYTES:
                    raise invalid()
                for entry in entries:
                    name = file_path(entry.orig_filename)
                    if name != entry.filename:
                        raise invalid()
                    mode = entry.external_attr >> 16
                    if (
                        entry.is_dir()
                        or entry.flag_bits & 1
                        or stat.S_ISLNK(mode)
                        or (stat.S_IFMT(mode) not in {0, stat.S_IFREG})
                        or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                    ):
                        raise invalid()
                    maximum = 128 * 1024 if name == "SKILL.md" else 64 * 1024
                    if not 1 <= entry.file_size <= maximum:
                        raise invalid()
                    with archive.open(entry) as source:
                        raw = source.read(maximum + 1)
                    if len(raw) != entry.file_size:
                        raise invalid()
                    files.append((name, raw))
        main = next(raw for name, raw in files if name == "SKILL.md").decode("utf-8-sig")
        lines = main.splitlines(keepends=True)
        if not lines or lines[0].strip() != "---":
            raise invalid()
        end = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
        header = "".join(lines[1:end])
        if len(header.encode()) > 4096:
            raise invalid()
        metadata = document(yaml.safe_load(header), "".join(lines[end + 1 :]).strip())
        result = SkillPackage(metadata, tuple(sorted(files)))
        result.validate()
        return result
    except (
        ValueError,
        KeyError,
        StopIteration,
        UnicodeError,
        RecursionError,
        yaml.YAMLError,
        zipfile.BadZipFile,
        NotImplementedError,
        OSError,
    ):
        raise invalid() from None


class SkillPackages:
    def __init__(self) -> None:
        self._pending: set[asyncio.Task] = set()
        self._slots = asyncio.Semaphore(2)
        self._closing = False

    async def parse(self, payload: bytes, format: str) -> SkillPackage:
        return await self._execute(lambda: capture(payload, format))

    async def archive(self, package: SkillPackage) -> bytes:
        def perform():
            package.validate()
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, content in package.files:
                    entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                    entry.external_attr = (stat.S_IFREG | 0o600) << 16
                    archive.writestr(entry, content, compress_type=zipfile.ZIP_DEFLATED)
            return output.getvalue()

        return await self._execute(perform)

    async def _execute(self, operation: Callable[[], T]) -> T:
        if self._closing or len(self._pending) >= 8:
            raise invalid("skill_codec_busy")

        async def perform():
            async with self._slots:
                return await asyncio.to_thread(operation)

        task = asyncio.create_task(perform(), name="skill-package-parse")
        self._pending.add(task)

        def done(future):
            self._pending.discard(future)
            if not future.cancelled():
                future.exception()

        task.add_done_callback(done)
        return await asyncio.shield(task)

    async def close(self, timeout_s: float = 30) -> bool:
        self._closing = True
        if self._pending:
            await asyncio.wait(self._pending, timeout=max(0, timeout_s))
        return not self._pending
