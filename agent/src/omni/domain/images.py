"""Validated image references, immutable model policies and conservative low-detail metering; no I/O."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from .contracts import BusinessError

MAX_IMAGES = 4
MAX_BYTES = 16 * 1024 * 1024
POLICY_DEFAULT = {"protocol": "disabled", "per_call_limit": 4, "run_budget": 12}
LOW_MODELS = {
    "gpt-4o": 85,
    "gpt-4o-2024-05-13": 85,
    "gpt-4o-2024-08-06": 85,
    "gpt-4o-2024-11-20": 85,
    "gpt-4o-mini": 2833,
    "gpt-4o-mini-2024-07-18": 2833,
}


def invalid(code: str = "image_invalid") -> BusinessError:
    return BusinessError(code, "Image, model image policy or source reference is invalid", status=422)


def image_ids(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)) or len(value) > MAX_IMAGES:
        raise invalid()
    result = []
    for item in value:
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


def policy(value: object, model: str) -> dict:
    if (
        not isinstance(value, dict)
        or set(value) != set(POLICY_DEFAULT)
        or not isinstance(value["protocol"], str)
        or value["protocol"] not in {"disabled", "openai_low"}
    ):
        raise invalid("image_policy_invalid")
    if (
        type(value["per_call_limit"]) is not int
        or not 1 <= value["per_call_limit"] <= MAX_IMAGES
        or type(value["run_budget"]) is not int
        or not value["per_call_limit"] <= value["run_budget"] <= 64
    ):
        raise invalid("image_policy_invalid")
    if value["protocol"] != "disabled":
        model_tokens(model)
    return dict(value)


def fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(frozen=True)
class ImageInfo:
    media_type: str
    width: int
    height: int
    sha256: str
    bytes: int

    def validate(self) -> None:
        if (
            not isinstance(self.media_type, str)
            or self.media_type not in {"image/png", "image/jpeg", "image/webp"}
            or type(self.width) is not int
            or type(self.height) is not int
            or type(self.bytes) is not int
            or not isinstance(self.sha256, str)
            or not 1 <= self.width <= 8192
            or not 1 <= self.height <= 8192
            or self.width * self.height > 16000000
            or not 1 <= self.bytes <= MAX_BYTES
            or not re.fullmatch(r"[0-9a-f]{64}", self.sha256)
        ):
            raise invalid()


def references(messages: Sequence[dict]) -> list[dict]:
    result = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                raise invalid()
            if item.get("type") == "omni_image":
                if (
                    message.get("role") != "user"
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
                ImageInfo(item["media_type"], item["width"], item["height"], item["sha256"], item["bytes"]).validate()
                if type(item["token_cost"]) is not int or item["token_cost"] not in set(LOW_MODELS.values()):
                    raise invalid()
                if item["part_id"] in {r["part_id"] for r in result}:
                    raise invalid()
                result.append(dict(item))
            elif item.get("type") != "text" or set(item) != {"type", "text"} or not isinstance(item["text"], str):
                # Arbitrary image URLs/data URLs never enter a canonical application request.
                raise invalid()
    if len(result) > MAX_IMAGES:
        raise invalid("image_call_limit_exceeded")
    return result


def with_images(rows: list[dict], image_rows: list[dict], config: dict, model: str) -> list[dict]:
    positions = {row["id"]: index for index, row in enumerate(rows)}
    available = sorted(
        (r for r in image_rows if r["message_id"] in positions),
        key=lambda r: (positions[r["message_id"]], r["position"]),
    )
    chosen = (
        {r["part_id"] for r in available[-config["per_call_limit"] :]} if config["protocol"] != "disabled" else set()
    )
    result = []
    for row in rows:
        parts = [r for r in available if r["message_id"] == row["id"]]
        if not parts:
            result.append(row)
            continue
        content = [{"type": "text", "text": row["content"]}]
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
