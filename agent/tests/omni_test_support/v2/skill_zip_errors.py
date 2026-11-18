"""Reusable skill_zip_errors fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import io
import struct
import zipfile
import zlib

import pytest
from fastapi.testclient import TestClient

from omni.adapters.skill_packages import SkillPackages, capture
from omni.composition.http import http_app
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError
from omni_test_support.v2.routing import Model
from omni_test_support.v2.skills import markdown

FILES = {
    "SKILL.md": markdown(text="PRIVATE_SKILL_ZIP_TEXT approved instructions.").encode(),
    "references/guide.md": b"Declared reference text, PRIVATE_SKILL_ZIP_TEXT.",
}


def archive_bytes(compression=zipfile.ZIP_DEFLATED):
    with io.BytesIO() as output:
        with zipfile.ZipFile(output, "w", compression=compression) as archive:
            for name, raw in FILES.items():
                archive.writestr(name, raw)
        return output.getvalue()


def damaged(target, kind="block"):
    raw = bytearray(archive_bytes())
    with io.BytesIO(raw) as source, zipfile.ZipFile(source) as archive:
        info = archive.getinfo(target)
        assert info.compress_type == zipfile.ZIP_DEFLATED and info.compress_size >= 5
        if kind == "crc":
            offset = archive.start_dir
            for entry in archive.infolist():
                name, extra, comment = struct.unpack_from("<HHH", raw, offset + 28)
                if entry.filename == target:
                    struct.pack_into("<I", raw, offset + 16, entry.CRC ^ 1)
                    break
                offset += 46 + name + extra + comment
        else:
            name, extra = struct.unpack_from("<HH", raw, info.header_offset + 26)
            start = info.header_offset + 30 + name + extra
            if kind == "block":
                raw[start] = 0x07  # Reserved DEFLATE block type.
            else:
                assert kind == "length"
                raw[start : start + 5] = b"\x01\0\0\0\0"  # Stored LEN/NLEN do not complement each other.
    return bytes(raw)


__all__ = [
    "BusinessError",
    "FILES",
    "Model",
    "SkillPackages",
    "TestClient",
    "archive_bytes",
    "asyncio",
    "capture",
    "damaged",
    "http_app",
    "io",
    "markdown",
    "open_instance",
    "pytest",
    "struct",
    "zipfile",
    "zlib",
]
