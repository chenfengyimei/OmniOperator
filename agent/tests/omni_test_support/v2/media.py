"""Reusable media fixtures, extracted without collecting test cases."""

import asyncio
from contextlib import contextmanager

import pytest
from alembic import command
from sqlalchemy import select

from omni.adapters.object_store import FileObjects
from omni.adapters.persistence import schema as s
from omni.adapters.persistence.database import Database
from omni.adapters.persistence.migration_config import configuration
from omni.adapters.persistence.sqlalchemy_access import database_engine
from omni.adapters.primitives import SystemClock, UUIDs
from omni.domain.contracts import BusinessError
from omni.domain.media import source_row, transcript_payload

VIDEO = {"bvid": "BV1xx411c7mD", "aid": "123", "cid": "456", "title": "Fixture video", "duration_s": 120}

EXPIRES = "2099-01-01T00:00:00+00:00"


@contextmanager
def rejects(code):
    with pytest.raises(BusinessError) as caught:
        yield
    assert caught.value.code == code


async def _agent_and_source(db, tmp_path):
    await db.write(lambda u: u.initialize())
    agent = (await db.read(lambda u: u.query("agents", "")))[0]["id"]
    created = await db.write(
        lambda u: u.configuration_create(
            agent, "channel", "Bili fixture", True, {"driver": "bilibili", "account": "456"}
        )
    )
    config = await db.read(lambda u: u.configuration_get(agent, created["id"]))
    source = await db.write(
        lambda u: u.media_source_register(agent, source_row(config["id"], str(config["revision_id"]), VIDEO))
    )
    return agent, source


import hashlib  # noqa: E402
import io  # noqa: E402
import uuid  # noqa: E402

from PIL import Image  # noqa: E402

from omni.composition.instance import open_instance  # noqa: E402
from omni.domain.bilibili import ADAPTER  # noqa: E402
from omni.domain.contracts import InvocationContext, ModelReply, Outcome  # noqa: E402

BV = "BV1xx411c7mD"

CHANNEL = {"driver": "bilibili", "account": "456", "credential_ref": "fixture/bili"}


class NoModel:
    async def complete(self, messages, tools, settings):
        return ModelReply("fixture reply", cost_micro=0)


class Secrets:
    def get(self, reference):
        return None

    def set(self, reference, value):
        pass

    def delete(self, reference):
        pass


def sheet_png(width, height):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (30, 90, 200)).save(buffer, "PNG")
    return buffer.getvalue()


class FakeGateway:
    identity = ADAPTER

    def __init__(self):
        self.reads, self.downloads = [], []

    def account_key(self, value):
        return value["account"]

    async def read(self, configuration, operation, target, limit=10):
        self.reads.append((operation, target))
        if operation == "video":
            return {
                "bvid": target,
                "aid": "170001",
                "cid": "230001",
                "title": "Fixture video",
                "duration_s": 600,
            }
        if operation == "subtitle":
            return {"language": "zh-CN", "text": "第一行字幕\n第二行字幕"}
        if operation == "streams":
            return {
                "audio": {"url": "https://cdn.example/audio.m4s"},
                "video": {"url": "https://cdn.example/video.m4s"},
            }
        raise AssertionError(operation)

    async def read_source(self, configuration, operation, source):
        return await self.read(configuration, operation, str(source["bvid"]))

    async def download(self, configuration, url, destination, *, max_bytes, budget_s, before_dispatch=None):
        self.downloads.append((url, max_bytes))
        destination.write_bytes(b"fixture media bytes")
        return {"bytes": destination.stat().st_size}

    async def send(self, message):
        raise AssertionError("media tests never dispatch bilibili deliveries")

    async def close(self, timeout_s=30):
        return True


class FakeProcessor:
    def __init__(self):
        self.calls = []
        self.hang = False
        self.failure = None

    def status(self):
        return {
            "available": True,
            "running": False,
            "probed": {"av": True, "faster_whisper": True, "PIL": True},
            "cleanup_pending": False,
        }

    async def probe(self):
        return {"ok": True, "components": {"av": True, "faster_whisper": True, "PIL": True}}

    async def transcribe(self, audio, output, plan):
        self.calls.append(("transcribe", str(audio), plan))
        if self.hang:
            await asyncio.sleep(60)
        if self.failure is not None:
            raise self.failure
        output.write_bytes(transcript_payload("asr", "zh", [[0.0, 2.5, "你好世界"], [2.5, 4.0, "第二句锚点"]]))
        return {"ok": True, "language": "zh"}

    async def frames(self, video, sheet_output, count, width, timestamps=None, select="equidistant"):
        self.calls.append(("frames", count, width))
        if self.hang:
            await asyncio.sleep(60)
        if self.failure is not None:
            raise self.failure
        content = sheet_png(width, width * 9 // 16)
        sheet_output.write_bytes(content)
        anchors = list(timestamps) if timestamps is not None else [round(index * 10.0, 1) for index in range(count)]
        return {
            "ok": True,
            "frame_count": count,
            "width": width,
            "height": width * 9 // 16,
            "timestamps": anchors,
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
        }

    def stop_accepting(self):
        pass

    async def close(self, timeout_s=30):
        return True


async def _seed(i):
    agent = (await i.application.query("agents", ""))[0]["id"]
    config = await i.configurations.create(agent, "channel", "Bili fixture", True, CHANNEL)
    return agent, config


async def _settle(i, agent, job_id, timeout=8.0):
    async with asyncio.timeout(timeout):
        while True:
            job = await i.media.job(agent, str(job_id))
            if job["state"] not in ("queued", "running"):
                return job
            await asyncio.sleep(0.02)


async def _settle_running(i, agent, job_id, timeout=8.0):
    async with asyncio.timeout(timeout):
        while True:
            job = await i.media.job(agent, str(job_id))
            assert job["state"] not in ("failed", "unknown"), job
            if job["state"] == "running":
                return job
            await asyncio.sleep(0.02)


def _context(agent):
    return InvocationContext(agent_id=agent, run_id=uuid.uuid4().hex, session_id=uuid.uuid4().hex, policy_revision=1)


def _media_instance(tmp_path, gateway=None, processor=None):
    return open_instance(
        tmp_path,
        model=NoModel(),
        capabilities=(),
        bilibili_backend=gateway or FakeGateway(),
        media_processor=processor or FakeProcessor(),
        credential_backend=Secrets(),
    )


import time  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from omni.composition.http import http_app  # noqa: E402


def _wait_done(client, base, job_id, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = client.get(f"{base}/media/jobs/{job_id}").json()
        if row["state"] not in ("queued", "running"):
            return row
        time.sleep(0.05)
    raise AssertionError(row)


__all__ = [
    "ADAPTER",
    "BV",
    "BusinessError",
    "CHANNEL",
    "Database",
    "EXPIRES",
    "FakeGateway",
    "FakeProcessor",
    "FileObjects",
    "Image",
    "InvocationContext",
    "ModelReply",
    "NoModel",
    "Outcome",
    "Secrets",
    "SystemClock",
    "TestClient",
    "UUIDs",
    "VIDEO",
    "_agent_and_source",
    "_context",
    "_media_instance",
    "_seed",
    "_settle",
    "_settle_running",
    "_wait_done",
    "asyncio",
    "command",
    "configuration",
    "contextmanager",
    "database_engine",
    "hashlib",
    "http_app",
    "io",
    "open_instance",
    "pytest",
    "rejects",
    "s",
    "select",
    "sheet_png",
    "source_row",
    "time",
    "transcript_payload",
    "uuid",
]
