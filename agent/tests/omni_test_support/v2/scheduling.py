"""Reusable scheduling fixtures, extracted without collecting test cases."""

import asyncio
import datetime as dt
import hashlib

import pytest
from sqlalchemy import select, update

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.database import Database
from omni.adapters.primitives import UUIDs
from omni.adapters.timezones import PinnedTimezones
from omni.application.scheduling import SchedulingService
from omni.domain.contracts import BusinessError
from omni.domain.objects import Blob
from omni.domain.scheduling import Cron, due_selection, instant, schedule_value, timestamp
from omni.domain.skills import PLATFORMS


class Clock:
    def __init__(self, now="2026-11-03T00:00:00.000000+00:00"):
        self.value = now

    def now(self):
        return self.value

    def monotonic(self):
        return 0.0

    def advance(self, seconds):
        self.value = timestamp(instant(self.value) + dt.timedelta(seconds=seconds))


async def setup(tmp_path, platform="linux"):
    clock = Clock()
    db = Database(tmp_path / "omni.db", clock, UUIDs())
    await db.write(lambda u: u.initialize())
    agent = (await db.read(lambda u: u.query("agents", "")))[0]["id"]

    async def cancel(a, r):
        await db.write(lambda u: u.cancel(a, r))

    service = SchedulingService(db, clock, PinnedTimezones(), lambda: None, cancel, platform)
    return db, agent, clock, service


def interval(prompt="scheduled"):
    return {"kind": "interval", "expression": "", "interval_seconds": 60, "prompt": prompt}


def skill_markdown(name):
    return (
        f"---\nname: {name}\ndescription: Scheduled procedure\nplatforms: [windows, macos, linux]\n---\n\n"
        "## When to Use\nWhen the schedule runs.\n\n## Procedure\nFollow the reviewed steps.\n\n"
        "## Pitfalls\nDo not bypass permissions.\n\n## Verification\nRead back actual state.\n"
    )


async def seed_skill(db, agent, name="schedule-skill", platforms=PLATFORMS):
    """Install and enable one skill so a schedule binding can reference a real revision."""

    def write(unit):
        content = skill_markdown(name).encode()
        digest = hashlib.sha256(content).hexdigest()
        stored = unit.object_register(Blob(agent, digest, len(content)), "text/markdown", "2099-01-01T00:00:00+00:00")
        row = unit.skill_install(
            agent,
            {"name": name, "description": "Scheduled procedure", **{p: p in platforms for p in PLATFORMS}},
            [{"path": "SKILL.md", "object_id": stored["id"], "sha256": digest, "bytes": len(content)}],
        )
        return unit.skill_state(
            agent, row["id"], row["revision"], "enabled", row["revision_id"], row["manifest_sha256"]
        )

    return await db.write(write)


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
    "seed_skill",
    "select",
    "setup",
    "timestamp",
    "update",
]
