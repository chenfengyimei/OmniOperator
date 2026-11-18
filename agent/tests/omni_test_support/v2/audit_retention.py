"""Reusable audit_retention fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
import shutil

import pytest
from sqlalchemy import event, select

from omni.adapters.persistence import execution_pruning, execution_retention_repository, offline_plans
from omni.adapters.persistence import schema as s
from omni.adapters.persistence.reference_release import (
    apply_reference_release,
    cancel_reference_release,
    plan_reference_release,
    read_reference_plan,
)
from omni.adapters.persistence.repository_base import RepositoryBase
from omni.adapters.platform.runtime.paths import InstancePaths
from omni.adapters.primitives import UUIDs
from omni.application.delivery import DeliveryService
from omni.domain.contracts import ActionResult, BusinessError, Outcome, ToolSpec
from omni_test_support.v2.delivery import drain
from omni_test_support.v2.objects import Clock, opened
from omni_test_support.v2.v2_configuration_helpers import model_uow
from omni_test_support.v2.v2_delivery_helpers import FakePort, Observation

AS_OF = "2026-11-05T00:00:00.000000+00:00"

PRIVATE_BODY = "private-audit-preview-body-must-never-be-returned"

ELIGIBLE_KINDS = {"run.completed", "action.ok", "action.error", "action.denied", "action.cancelled"}


def _clock(value=AS_OF):
    clock = Clock()
    clock.current = dt.datetime.fromisoformat(value)
    return clock


def _turn(u, agent, key, outcome=Outcome.OK, *, finish=True, fail=False, artifacts=()):
    turn = u.accept_turn(agent, None, key, PRIVATE_BODY, {})
    u.transition(agent, turn["run_id"], "running")
    u.prepare_context(agent, turn["run_id"])
    action = u.prepare_action(agent, turn["run_id"], ToolSpec("files_read", "read", {}, False), {}, 1)
    u.authorize_action(agent, action["id"])
    u.settle_action(agent, action["id"], ActionResult(outcome, PRIVATE_BODY, artifacts=artifacts))
    if fail:
        u.transition(agent, turn["run_id"], "failed", "fixture_failure")
    elif finish:
        u.finish(agent, turn["run_id"], PRIVATE_BODY)
    return {**turn, "action_id": action["id"]}


@pytest.fixture(scope="module")
def audit_template(tmp_path_factory):
    root = tmp_path_factory.mktemp("audit-retention-template")
    clock = _clock()
    clock.current -= dt.timedelta(days=200)

    async def setup():
        async with opened(root / "home", clock) as (paths, db, objects, agent):
            await db.write(lambda u: model_uow(u, agent))
            blob = await objects.publish(agent, b"retained tool artifact", "text/plain")
            turns = {}
            for outcome in (Outcome.OK, Outcome.ERROR, Outcome.DENIED, Outcome.CANCELLED):
                turns[outcome.value] = await db.write(
                    lambda u, outcome=outcome: _turn(
                        u, agent, outcome.value, outcome, artifacts=(blob["id"],) if outcome == Outcome.OK else ()
                    )
                )
            turns["unpruned"] = await db.write(lambda u: _turn(u, agent, "unpruned"))
            await db.write(lambda u: u.retention_pin(agent, turns["unpruned"]["run_id"], True, 1))
            turns["recent_end"] = await db.write(lambda u: _turn(u, agent, "recent_end", finish=False))
            turns["failed"] = await db.write(lambda u: _turn(u, agent, "failed", fail=True))
            other = (await db.write(lambda u: u.create_agent("Other")))["id"]
            await db.write(lambda u: model_uow(u, other))
            foreign = await db.write(lambda u: _turn(u, other, "foreign"))
            special = {}
            for name, kind, identity in (
                ("action_mismatch", "action.error", turns["ok"]["action_id"]),
                ("run_mismatch", "run.completed", turns["failed"]["run_id"]),
                ("missing", "action.ok", UUIDs().new()),
                ("missing_operation", "run.completed", None),
                ("foreign_action", "action.ok", foreign["action_id"]),
                ("foreign_run", "run.completed", foreign["run_id"]),
                ("wrong_entity", "action.ok", turns["ok"]["run_id"]),
                ("unknown_kind", "action.ok.future", turns["ok"]["action_id"]),
            ):
                special[name] = await db.write(
                    lambda u, kind=kind, identity=identity: u.audit(
                        agent, kind, identity, {"run_id": turns["ok"]["run_id"], "text": PRIVATE_BODY}
                    )
                )
            return {
                "home": paths.home,
                "agent": agent,
                "other": other,
                "turns": turns,
                "blob": blob,
                "special": special,
            }

    value = asyncio.run(setup())
    clock.current += dt.timedelta(days=31)
    paths = InstancePaths.resolve(value["home"])
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(offline_plans, "SystemClock", lambda: clock)
        execution_pruning.plan_pruning(paths, root / "body-plan.json")
        plan = execution_pruning.read_pruning_plan(root / "body-plan.json")
        assert execution_pruning.apply_pruning(paths, plan, root / "body-backup")["complete"]

    async def unpin():
        async with opened(paths.home, clock) as (_, db, _, _):
            await db.write(lambda u: u.retention_pin(value["agent"], value["turns"]["unpruned"]["run_id"], False, 2))

    asyncio.run(unpin())
    return value


@pytest.fixture
def prepared(audit_template, tmp_path):
    # The template is closed and checkpointed; each test owns its database and lock.
    home = tmp_path / "home"
    shutil.copytree(audit_template["home"], home)
    return {**audit_template, "home": home}


async def _pages(db, agent, *, as_of=AS_OF):
    cursor, rows, seen = "", [], set()
    for _ in range(20):
        page = await db.read(lambda u: u.audit_retention_preview(agent, cursor=cursor, limit=20, as_of=as_of))
        assert page["as_of"] == as_of and page["can_apply"] is False
        assert page["counts"] == {
            "scanned": len(page["items"]),
            "eligible": sum(row["eligible"] for row in page["items"]),
            "protected": sum(not row["eligible"] for row in page["items"]),
        }
        for row in page["items"]:
            assert row["audit_id"] not in seen and row["agent_id"] == agent
            seen.add(row["audit_id"])
        rows.extend(page["items"])
        if not page["has_more"]:
            assert [r["sequence"] for r in rows] == sorted(r["sequence"] for r in rows)
            return {row["audit_id"]: row for row in rows}
        assert page["next_cursor"] and page["next_cursor"] != cursor
        cursor = page["next_cursor"]
    pytest.fail("Audit preview failed to make bounded pagination progress")


def _matching(rows, kind, identity):
    return [row for row in rows.values() if row["kind"] == kind and row["operation_id"] == identity]


__all__ = [
    "AS_OF",
    "ActionResult",
    "BusinessError",
    "Clock",
    "DeliveryService",
    "ELIGIBLE_KINDS",
    "FakePort",
    "InstancePaths",
    "Observation",
    "Outcome",
    "PRIVATE_BODY",
    "RepositoryBase",
    "ToolSpec",
    "UUIDs",
    "_clock",
    "_matching",
    "_pages",
    "_turn",
    "apply_reference_release",
    "asyncio",
    "audit_template",
    "cancel_reference_release",
    "drain",
    "dt",
    "event",
    "execution_pruning",
    "execution_retention_repository",
    "json",
    "model_uow",
    "offline_plans",
    "opened",
    "plan_reference_release",
    "prepared",
    "pytest",
    "re",
    "read_reference_plan",
    "s",
    "select",
    "shutil",
]
