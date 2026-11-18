"""Reusable media_admission fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio

import pytest

from omni.application.media import MediaService
from omni.domain.contracts import BusinessError

VIDEO = {"bvid": "BV1xx411c7mD", "aid": "123", "cid": "456", "title": "Owned fixture", "duration_s": 120}


class Gate:
    def __init__(self):
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.cancelled = False

    async def wait(self):
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class Database:
    def __init__(self):
        self.gate, self.calls, self.created = Gate(), [], []

    async def read(self, operation):
        self.calls.append("read")
        return operation(self)

    async def write(self, operation):
        self.calls.append("write")
        row = operation(self)
        if row.get("operation"):
            # The create was committed, but delivery to submit is still pending.
            await self.gate.wait()
        return row

    def bilibili_read_authorized(self, agent_id, config_id, revision_id=""):
        return {"value": {}, "revision_id": "revision", "authority": "fixture-authority"}

    def media_source_register(self, agent_id, row):
        self.created.append(row)
        return {"id": "source", **row}

    def media_job_create(self, agent_id, source_id, operation, plan):
        row = {"id": "job", "operation": operation, "params": plan}
        self.created.append(row)
        return row


class Gateway:
    def __init__(self):
        self.gate, self.calls = Gate(), []

    async def read(self, configuration, operation, target, limit=10):
        self.calls.append(operation)
        assert operation == "video"
        await self.gate.wait()
        return VIDEO


class Processor:
    def __init__(self):
        self.gate, self.calls, self.close_budgets = Gate(), [], []
        self.close_ok = True

    def status(self):
        return {"available": True}

    async def probe(self):
        self.calls.append("probe")
        await self.gate.wait()
        return {"ok": True}

    async def close(self, timeout_s=30):
        self.close_budgets.append(timeout_s)
        return self.close_ok


def fixture(tmp_path, operation):
    db, gateway, processor = Database(), Gateway(), Processor()
    service = MediaService(db, None, None, gateway, processor, None, tmp_path)
    jobs, job_gate = [], Gate()
    job_gate.release.set()

    async def execute(job_id):
        jobs.append(job_id)
        await job_gate.wait()

    service._execute = execute
    gate = {"register": gateway.gate, "submit": db.gate, "probe": processor.gate}[operation]

    async def invoke():
        if operation == "register":
            return await service.register("agent", "config", VIDEO["bvid"])
        if operation == "submit":
            return await service.submit("agent", "source", "subtitle", {})
        return await service.processor_probe()

    return service, db, gateway, processor, gate, invoke, jobs, job_gate


__all__ = [
    "BusinessError",
    "Database",
    "Gate",
    "Gateway",
    "MediaService",
    "Processor",
    "VIDEO",
    "asyncio",
    "fixture",
    "pytest",
]
