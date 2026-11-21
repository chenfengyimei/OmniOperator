"""Validated image references, immutable model policies and conservative low-detail metering; no I/O."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from typing import cast

from .contracts import MAX_IMAGES, ImageInfo, invalid

POLICY_DEFAULT = {"protocol": "disabled", "per_call_limit": 4, "run_budget": 12}
LOW_MODELS = {
    "gpt-4o": 85,
    "gpt-4o-2024-05-13": 85,
    "gpt-4o-2024-08-06": 85,
    "gpt-4o-2024-11-20": 85,
    "gpt-4o-mini": 2833,
    "gpt-4o-mini-2024-07-18": 2833,
}


def image_ids(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise invalid()
    items = cast(list[object] | tuple[object, ...], value)
    if len(items) > MAX_IMAGES:
        raise invalid()
    result: list[str] = []
    for item in items:
        try:
            if not isinstance(item, str) or str(uuid.UUID(item)) != item or item in result:
                raise invalid()
        except (ValueError, AttributeError):
            raise invalid() from None
        result.append(item)
    return result


def model_tokens(model: str) -> int:
    name = model.removeprefix("openai/")
    if name not in LOW_MODELS:
        raise invalid("image_model_unsupported")
    return LOW_MODELS[name]


def policy(value: object, model: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise invalid("image_policy_invalid")
    record = cast(dict[str, object], value)
    if (
        set(record) != set(POLICY_DEFAULT)
        or not isinstance(record["protocol"], str)
        or record["protocol"] not in {"disabled", "openai_low"}
    ):
        raise invalid("image_policy_invalid")
    per_call = cast(int, record["per_call_limit"])
    run_budget = cast(int, record["run_budget"])
    if (
        type(record["per_call_limit"]) is not int
        or not 1 <= per_call <= MAX_IMAGES
        or type(record["run_budget"]) is not int
        or not per_call <= run_budget <= 64
    ):
        raise invalid("image_policy_invalid")
    if record["protocol"] != "disabled":
        model_tokens(model)
    return dict(record)


def fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def references(messages: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for raw_item in cast(list[object], content):
            if not isinstance(raw_item, dict):
                raise invalid()
            item = cast(dict[str, object], raw_item)
            if item.get("type") == "omni_image":
                if (
                    message.get("role") not in {"user", "tool"}
                    or set(item)
                    != {
                        "type",
                        "part_id",
                        "object_id",
                        "sha256",
                        "width",
                        "height",
                        "media_type",
                        "bytes",
                        "token_cost",
                        "detail",
                        "policy_id",
                    }
                    or item["detail"] != "low"
                ):
                    raise invalid()
                for key in ("part_id", "object_id", "policy_id"):
                    image_ids([item[key]])
                ImageInfo(
                    cast(str, item["media_type"]),
                    cast(int, item["width"]),
                    cast(int, item["height"]),
                    cast(str, item["sha256"]),
                    cast(int, item["bytes"]),
                ).validate()
                token_cost = cast(int, item["token_cost"])
                if type(item["token_cost"]) is not int or token_cost not in set(LOW_MODELS.values()):
                    raise invalid()
                if item["part_id"] in {r["part_id"] for r in result}:
                    raise invalid()
                result.append({**item, "role": message.get("role")})
            elif item.get("type") != "text" or set(item) != {"type", "text"} or not isinstance(item["text"], str):
                # Arbitrary image URLs/data URLs never enter a canonical application request.
                raise invalid()
    if len(result) > MAX_IMAGES:
        raise invalid("image_call_limit_exceeded")
    return result


def with_images(
    rows: list[dict[str, object]],
    image_rows: list[dict[str, object]],
    config: dict[str, object],
    model: str,
) -> list[dict[str, object]]:
    positions = {cast(str, row["id"]): index for index, row in enumerate(rows)}
    available = sorted(
        (r for r in image_rows if r["message_id"] in positions),
        key=lambda r: (positions[cast(str, r["message_id"])], cast(int, r["position"])),
    )
    limit = cast(int, config["per_call_limit"])
    chosen: set[str] = (
        {cast(str, r["part_id"]) for r in available[-limit:]} if config["protocol"] != "disabled" else set()
    )
    result: list[dict[str, object]] = []
    for row in rows:
        parts = [r for r in available if r["message_id"] == row["id"]]
        if not parts:
            result.append(row)
            continue
        content: list[dict[str, object]] = [{"type": "text", "text": row["content"]}]
        for item in parts:
            if item["part_id"] not in chosen:
                reason = "image_capability_disabled" if config["protocol"] == "disabled" else "context_image_limit"
                content.append(
                    {
                        "type": "text",
                        "text": f"[Image registered but not attached: {reason}; do not infer its content.]",
                    }
                )
            else:
                content.append(
                    {
                        "type": "omni_image",
                        **{
                            k: item[k]
                            for k in ("part_id", "object_id", "sha256", "width", "height", "media_type", "bytes")
                        },
                        "token_cost": model_tokens(model),
                        "detail": "low",
                        "policy_id": config["id"],
                    }
                )
        result.append({**row, "content": content})
    return result
