"""Reusable images fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import uuid

import pytest
from PIL import Image, PngImagePlugin
from sqlalchemy import delete, select, update

from omni.adapters.images import ImageCodec, inspect_image
from omni.adapters.persistence import schema as s
from omni.adapters.persistence.object_inventory import registry
from omni.adapters.token_counter import ConservativeTokenCounter
from omni.application.context import ContextService
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError, Invocation, ModelReply, ToolSpec
from omni.domain.images import ImageInfo, model_tokens, policy, references
from omni_test_support.v2.execution import wait_state


def png(size=(80, 40)):
    output = io.BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("source", "private-image-canary")
    Image.new("RGB", size, "red").save(output, format="PNG", pnginfo=metadata)
    return output.getvalue()


async def configure(instance, agent, *, per_call=4, budget=12, model="gpt-4o", protocol="openai_low"):
    settings = await instance.application.settings(agent)
    row = await instance.configurations.get(agent, settings["value"]["model_profile_id"])
    return await instance.configurations.save(
        agent,
        row["id"],
        row["name"],
        True,
        {**row["value"], "model": model},
        row["revision"],
        {"protocol": protocol, "per_call_limit": per_call, "run_budget": budget},
    )


class Model:
    def __init__(self, *, error=False, steps=0):
        self.requests = []
        self.error, self.steps = error, steps

    async def complete(self, messages, tools, settings):
        self.requests.append(messages)
        if self.error:
            raise OSError("provider-image-canary")
        calls = (Invocation("call" + str(len(self.requests)), "read", {}),) if len(self.requests) <= self.steps else ()
        return ModelReply("image response", calls=calls, cost_micro=0)


__all__ = [
    "BusinessError",
    "ConservativeTokenCounter",
    "ContextService",
    "Image",
    "ImageCodec",
    "ImageInfo",
    "Invocation",
    "Model",
    "ModelReply",
    "PngImagePlugin",
    "ToolSpec",
    "asyncio",
    "base64",
    "configure",
    "delete",
    "inspect_image",
    "io",
    "json",
    "model_tokens",
    "open_instance",
    "png",
    "policy",
    "pytest",
    "references",
    "registry",
    "s",
    "select",
    "update",
    "uuid",
    "wait_state",
]
