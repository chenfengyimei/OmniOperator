"""Reusable media_output fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import hashlib
import io
from pathlib import Path

import pytest

from omni.application.media import MediaService
from omni.domain.contracts import BusinessError
from omni.domain.media import MAX_SHEET_BYTES, MAX_TRANSCRIPT_BYTES, transcript_payload


class Output:
    def __init__(self, content, *, error=None):
        self.content, self.error, self.reads, self.closed = content, error, [], []

    def open(self, mode):
        assert mode == "rb"
        owner = self

        class Reader(io.BytesIO):
            def read(self, size=-1):
                owner.reads.append(size)
                if owner.error:
                    raise owner.error
                return super().read(size)

            def close(self):
                owner.closed.append(True)
                super().close()

        return Reader(self.content)

    def read_bytes(self):
        with self.open("rb") as stream:
            return stream.read()


class Objects:
    def __init__(self):
        self.calls = []

    async def publish(self, agent_id, content, mime):
        self.calls.append((agent_id, content, mime))
        return {"id": "object", "bytes": len(content)}


class Images:
    def __init__(self):
        self.calls = []

    async def prepare(self, agent_id, object_id):
        self.calls.append((agent_id, object_id))
        return {"state": "registered_not_attached", "width": 320, "height": 180, "media_type": "image/png"}


def fixture(tmp_path, content):
    objects, images = Objects(), Images()
    service = MediaService(None, objects, images, None, None, None, tmp_path)
    result = {
        "frame_count": 1,
        "width": 320,
        "height": 180,
        "timestamps": [1.0],
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }
    return service, objects, images, result


__all__ = [
    "BusinessError",
    "Images",
    "MAX_SHEET_BYTES",
    "MAX_TRANSCRIPT_BYTES",
    "MediaService",
    "Objects",
    "Output",
    "Path",
    "asyncio",
    "fixture",
    "hashlib",
    "io",
    "pytest",
    "transcript_payload",
]
