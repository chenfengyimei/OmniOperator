"""Reusable scheduling fixtures, extracted without collecting test cases."""

import asyncio
import datetime as dt

import pytest
from sqlalchemy import select, update

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.database import Database
from omni.adapters.primitives import UUIDs
from omni.adapters.timezones import PinnedTimezones
from omni.application.scheduling import SchedulingService
from omni.domain.contracts import BusinessError
from omni.domain.scheduling import Cron, due_selection, instant, schedule_value, timestamp


class Clock:
    def __init__(self, now="2026-11-03T00:00:00.000000+00:00"):
        self.value = now

    def now(self):
        return self.value

    def monotonic(self):
        return 0.0

    def advance(self, seconds):
        self.value = timestamp(instant(self.value) + dt.timedelta(seconds=seconds))


async def setup(tmp_path):
    clock = Clock()
    db = Database(tmp_path / "omni.db", clock, UUIDs())
    await db.write(lambda u: u.initialize())
    agent = (await db.read(lambda u: u.query("agents", "")))[0]["id"]

    async def cancel(a, r):
        await db.write(lambda u: u.cancel(a, r))

    service = SchedulingService(db, clock, PinnedTimezones(), lambda: None, cancel)
    return db, agent, clock, service


def interval(prompt="scheduled"):
    return {"kind": "interval", "expression": "", "interval_seconds": 60, "prompt": prompt}


__all__ = [
    "BusinessError",
    "Clock",
    "Cron",
    "Database",
    "PinnedTimezones",
    "SchedulingService",
    "UUIDs",
    "asyncio",
    "dt",
    "due_selection",
    "instant",
    "interval",
    "pytest",
    "s",
    "schedule_value",
    "select",
    "setup",
    "timestamp",
    "update",
]
