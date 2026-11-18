"""Reusable skills fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import io
import json
import zipfile

import pytest
from sqlalchemy import delete, update

from omni.adapters.persistence import schema as s
from omni.adapters.skill_packages import capture
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError, Invocation, ModelReply
from omni_test_support.v2.execution import wait_state
from omni_test_support.v2.routing import Model
from omni_test_support.v2.v2_configuration_helpers import model_instance


def markdown(
    name="test-skill", text="Follow the approved procedure and verify the result.", platforms="[windows, macos, linux]"
):
    return f"---\nname: {name}\ndescription: Reusable reviewed procedure\nplatforms: {platforms}\n---\n\n## When to Use\nWhen explicitly requested.\n\n## Procedure\n{text}\n\n## Pitfalls\nDo not bypass permissions.\n\n## Verification\nRead back actual state.\n"


def package(content=None, references=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("SKILL.md", content or markdown())
        for name, value in (references or {"references/guide.md": "Reference text, no execution."}).items():
            archive.writestr(name, value)
    return output.getvalue()


async def enable(instance, agent, row):
    return await instance.skills.state(
        agent, row["id"], row["revision"], "enabled", row["revision_id"], row["manifest_sha256"]
    )


async def setup(instance):
    agent = (await instance.application.query("agents", ""))[0]["id"]
    await model_instance(instance, agent, model="fixture")
    row = await instance.skills.save(agent, markdown())
    return agent, row


__all__ = [
    "BusinessError",
    "Invocation",
    "Model",
    "ModelReply",
    "asyncio",
    "capture",
    "delete",
    "enable",
    "io",
    "json",
    "markdown",
    "model_instance",
    "open_instance",
    "package",
    "pytest",
    "s",
    "setup",
    "update",
    "wait_state",
    "zipfile",
]
