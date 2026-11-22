"""Reusable media-zoom instance fixtures extracted from collected tests; no test cases are defined here."""

from __future__ import annotations

from omni.composition.instance import open_instance

from .media import FakeGateway, FakeProcessor, Secrets


async def zoom_instance(tmp_path, model, *, processor=None, gateway=None):
    return await open_instance(
        tmp_path,
        model=model,
        bilibili_backend=gateway or FakeGateway(),
        media_processor=processor or FakeProcessor(),
        credential_backend=Secrets(),
    )


async def configure_image_policy(instance, agent):
    settings = await instance.application.settings(agent)
    row = await instance.configurations.get(agent, settings["value"]["model_profile_id"])
    await instance.configurations.save(
        agent,
        row["id"],
        row["name"],
        True,
        {**row["value"], "model": "gpt-4o"},
        row["revision"],
        {"protocol": "openai_low", "per_call_limit": 4, "run_budget": 12},
    )


__all__ = ["configure_image_policy", "zoom_instance"]
