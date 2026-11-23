"""Run one real visual-subagent call through the production composition on an isolated instance.

Usage (owner-authorized real window, requires OPENAI_API_KEY for the selected model):
    uv run --no-sync python agent/scripts/accept_visual_production.py
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image, ImageDraw
from sqlalchemy import select

from omni.adapters.diagnostics import Redactor
from omni.adapters.models import LiteLLMModel
from omni.adapters.persistence import schema as s
from omni.application.visual_subagent import VISUAL_SUBAGENT
from omni.composition.instance import open_instance
from omni.domain.contracts import Invocation, ModelReply

MODEL = "openai/gpt-4o"
HOME = Path("temp/build/k14-visual-production-accept")
load_dotenv(Path(__file__).resolve().parents[2] / ".env")


def _screenshot() -> bytes:
    with io.BytesIO() as output:
        image = Image.new("RGB", (320, 180), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((24, 24, 120, 120), fill=(220, 30, 30))
        draw.text((140, 70), "HELLO OMNI", fill=(0, 0, 0))
        image.save(output, format="PNG")
        return output.getvalue()


class RoutingModel:
    """Scripted master, real LiteLLM only for the image-bearing visual call."""

    def __init__(self) -> None:
        self.object_id = ""
        self.real = LiteLLMModel(None, Redactor())
        self.visual_calls = 0

    async def complete(self, messages, tools, settings):
        visual = not tools and any(
            isinstance(message.get("content"), list)
            and any(
                isinstance(part, dict) and part.get("type") in {"image_url", "image", "input_image"}
                for part in message["content"]
            )
            for message in messages
        )
        if visual:
            self.visual_calls += 1
            return await self.real.complete(messages, tools, settings)
        if any(message.get("role") == "tool" for message in messages):
            return ModelReply("visual accepted", cost_micro=1)
        return ModelReply(
            "",
            (
                Invocation(
                    "visual-1",
                    VISUAL_SUBAGENT.name,
                    {
                        "task": "Read the large text and describe the colored shape in one sentence.",
                        "frame_object_id": self.object_id,
                        "focus": "general",
                    },
                ),
            ),
            cost_micro=1,
            completion_reason="tool_calls",
        )


async def main() -> int:
    if not os.environ.get("OPENAI_API_KEY"):
        print(json.dumps({"ok": False, "error": "OPENAI_API_KEY missing"}, ensure_ascii=False))
        return 2
    home = (HOME / f"run-{int(time.time())}").resolve()
    home.mkdir(parents=True, exist_ok=True)
    model = RoutingModel()
    instance = await open_instance(home, model=model)
    instance.maintenance.stop_accepting()
    started = time.monotonic()
    try:
        app = instance.application
        agent = (await app.query("agents", ""))[0]["id"]
        db = instance._database
        settings = await app.settings(agent)
        profile_id = str(settings["value"]["model_profile_id"])
        current = await instance.configurations.get(agent, profile_id)
        model_value = {**current["value"], "model": MODEL}
        updated = await db.write(
            lambda u: u.configuration_put(
                agent, profile_id, str(current["name"]), True, model_value, int(current["revision"])
            )
        )
        value = dict(settings["value"])
        value["daily_budget_micro"] = 1_000_000
        value["call_reservation_micro"] = 10_000
        await app.save_settings(agent, value, settings["revision"])
        await instance.configurations.save(
            agent,
            str(updated["id"]),
            str(updated["name"]),
            True,
            updated["value"],
            int(updated["revision"]),
            image_policy={"protocol": "openai_low", "per_call_limit": 4, "run_budget": 12},
        )
        uploaded = await instance.images.upload(agent, _screenshot())
        model.object_id = str(uploaded["object_id"])
        receipt = await app.submit_turn(agent, None, "visual-production", "Analyze the screenshot")
        app.notify()
        async with asyncio.timeout(180):
            while True:
                run = await app.get_run(agent, receipt["run_id"])
                if run["state"] in {"succeeded", "failed"}:
                    break
                await asyncio.sleep(0.05)
        action = await db.read(
            lambda u: (
                u.connection.execute(select(s.actions).where(s.actions.c.tool == VISUAL_SUBAGENT.name))
                .mappings()
                .first()
            )
        )
        result = action["result"] if action else None
        print(
            json.dumps(
                {
                    "ok": bool(action and action["state"] == "ok"),
                    "home": str(home),
                    "run_state": run["state"],
                    "visual_calls": model.visual_calls,
                    "seconds": round(time.monotonic() - started, 2),
                    "action_state": action["state"] if action else None,
                    "action_result": result,
                },
                ensure_ascii=False,
                default=str,
            )
        )
        return 0 if action and action["state"] == "ok" else 1
    finally:
        await instance.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
