"""Reusable extension_repository fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
from dataclasses import replace

import pytest
from sqlalchemy import select

from omni.adapters.extensions.packages import capture_directory
from omni.adapters.persistence import schema as s
from omni.adapters.persistence.maintenance import backup, restore
from omni.adapters.platform.runtime.paths import InstancePaths
from omni.domain.contracts import ActionResult, BusinessError, Effect, Outcome
from omni.domain.extensions import request_fingerprint, tool_spec
from omni_test_support.v2.extensions_contract import chinese_manifest, manifest
from omni_test_support.v2.objects import Clock, opened
from omni_test_support.v2.v2_configuration_helpers import model_uow


def package_source(directory, *, version="1.0", count=2, value=None):
    directory.mkdir(parents=True, exist_ok=True)
    value = value or manifest(count)
    value["version"] = version
    (directory / "extension.json").write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    (directory / "plugin.py").write_text(
        "raise RuntimeError('repository fixtures must never import plugin code')\n", encoding="utf-8"
    )
    return directory


def metadata(captured):
    return {
        "manifest": captured.manifest,
        "files": list(captured.files),
        "package_sha256": captured.package_sha256,
        "catalog_sha256": captured.catalog_sha256,
    }


def install_request(source, key="install", previous=None):
    return {
        "key": key,
        "source": str(source),
        "extension_id": previous["id"] if previous else None,
        "revision": previous["revision"] if previous else 0,
    }


async def install(database, objects, agent, source, key="install", previous=None):
    captured = capture_directory(source)
    package = await objects.publish(agent, captured.content, "application/zip")
    request = install_request(source, key, previous)
    row = await database.write(lambda unit: unit.extension_install(agent, request, metadata(captured), package["id"]))
    return row, captured, package, request


def authorization(row, names=None):
    return {
        "revision": row["revision"],
        "package_sha256": row["package_sha256"],
        "catalog_sha256": row["catalog_sha256"],
        "history_epoch": row["history_epoch"],
        "tools": names or [tool["name"] for tool in row["tools"]],
        "trust": True,
    }


async def grant(database, agent, row, names=None):
    return await database.write(lambda unit: unit.extension_authorize(agent, row["id"], authorization(row, names)))


def new_turn(unit, agent, key="turn"):
    return unit.accept_turn(agent, None, key, "isolated plugin action", {})


def prepare_action(unit, agent, turn, arguments=None, *, start=True):
    if start:
        unit.transition(agent, turn["run_id"], "running")
    binding = unit.extension_run_tools(agent, turn["run_id"])[0]
    spec = tool_spec(binding)
    policy = unit.settings(agent)["policy_revision"]
    action = unit.prepare_action(agent, turn["run_id"], spec, arguments or {}, policy)
    return action, binding


def approve_action(unit, agent, action):
    pending = next(row for row in unit.pending_approvals(agent) if row["action_id"] == action["id"])
    unit.resolve_approval(agent, pending["id"], True, action["policy_revision"])


def table_rows(unit, table, agent):
    return [
        dict(row)
        for row in unit.connection.execute(
            select(table).where(table.c.agent_id == agent).order_by(table.c.id)
        ).mappings()
    ]


__all__ = [
    "ActionResult",
    "BusinessError",
    "Clock",
    "Effect",
    "InstancePaths",
    "Outcome",
    "approve_action",
    "asyncio",
    "authorization",
    "backup",
    "capture_directory",
    "chinese_manifest",
    "dt",
    "grant",
    "hashlib",
    "install",
    "install_request",
    "json",
    "manifest",
    "metadata",
    "model_uow",
    "new_turn",
    "opened",
    "package_source",
    "prepare_action",
    "pytest",
    "replace",
    "request_fingerprint",
    "restore",
    "s",
    "select",
    "table_rows",
    "tool_spec",
]
